from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import shlex
import socket
import subprocess
import sys
from tempfile import TemporaryDirectory
from threading import Thread
import time
import unittest
from urllib.error import URLError

from ride.mock_vllm import server as mock_server
from ride.registry import controller_adapter
from ride.sdk import PeerClient


def free_endpoint():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return f"http://127.0.0.1:{listener.getsockname()[1]}"


@contextmanager
def temporary_model():
    with mock_server("127.0.0.1", 0) as model:
        worker = Thread(target=model.serve_forever, daemon=True)
        worker.start()
        try:
            yield f"http://127.0.0.1:{model.server_port}"
        finally:
            model.shutdown()
            worker.join()


@contextmanager
def child(arguments, log_path):
    with log_path.open("w+", encoding="utf-8") as log:
        process = subprocess.Popen(arguments, stdout=log, stderr=subprocess.STDOUT)
        try:
            yield process
        finally:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=5)


def wait_for(observe, condition, description, process=None):
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if process is not None and process.poll() is not None:
            break
        try:
            value = observe()
        except URLError:
            time.sleep(0.05)
            continue
        if condition(value):
            return value
        time.sleep(0.05)
    raise AssertionError(f"Timed out waiting for {description}")


def specification(directory, backend):
    config = {
        "schema": "ride.controller/v1",
        "id": "template-smoke",
        "role": "course-starter",
        "endpoint": free_endpoint(),
        "backend": backend,
        "state_file": str(directory / "controller-state.json"),
    }
    path = directory / "controller.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    return {
        "id": config["id"],
        "endpoint": config["endpoint"],
        "type": config["role"],
        "configuration": str(path),
        "configuration_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }


class ControllerSmokeTest(unittest.TestCase):
    def check_request(self, endpoint):
        client = PeerClient(endpoint, timeout=4)
        response = client.post(
            "/v1/chat/completions",
            {"model": "mock-vllm", "messages": [{"role": "user", "content": "hello"}]},
        )
        self.assertEqual(response["choices"][0]["message"]["content"], "hello")
        self.assertEqual(response["ride_evidence_type"], "mock")
        self.assertEqual(client.get("/status")["completed"], 1)

    def test_installed_adapter_and_real_cpu_request(self):
        with TemporaryDirectory() as work, temporary_model() as backend:
            directory = Path(work).resolve()
            spec = specification(directory, backend)
            adapter = controller_adapter(spec["type"])
            self.assertIsNotNone(adapter)
            with self.assertRaisesRegex(ValueError, "SHA256"):
                adapter.command({**spec, "configuration_sha256": "0" * 64})
            args = shlex.split(adapter.command(spec))
            self.assertEqual(args[0], "python")
            log = directory / "controller.log"
            with child([sys.executable, *args[1:]], log) as process:
                try:
                    result = wait_for(
                        lambda: adapter.probe(spec),
                        lambda value: value["controller_ready"],
                        "SDK probe",
                        process,
                    )
                    self.assertEqual(result["identity"]["backend"], backend)
                    self.check_request(spec["endpoint"])
                except (AssertionError, OSError):
                    print(log.read_text(encoding="utf-8", errors="replace"))
                    raise

    @unittest.skipUnless(
        os.name == "posix" and os.environ.get("RIDE_MANAGED_SMOKE") == "1",
        "Set RIDE_MANAGED_SMOKE=1 on Linux or WSL to run the managed lifecycle",
    )
    def test_local_ride_starts_stops_and_restarts_controller(self):
        with TemporaryDirectory() as work, temporary_model() as backend:
            directory = Path(work).resolve()
            spec = specification(directory, backend)
            name = spec["id"]
            management = free_endpoint()
            management_log = directory / "ride.log"
            manager = [
                sys.executable, "-m", "ride", "start", "--executor", "local",
                "--listen", management.removeprefix("http://"), "--workspace", "course-smoke",
                "--state-file", str(directory / "ride-state.json"),
            ]

            def ride(*args):
                result = subprocess.run(
                    [sys.executable, "-m", "ride", *args, "--address", management],
                    capture_output=True, text=True, timeout=20,
                )
                if result.returncode:
                    raise AssertionError(f"RIDE {' '.join(args)}: {result.stderr}")
                return json.loads(result.stdout)

            def describe():
                return ride("describe", "controller", name)

            cli_spec = directory / "instance.json"
            cli_spec.write_text(
                json.dumps({key: value for key, value in spec.items() if key != "id"}),
                encoding="utf-8",
            )
            with child(manager, management_log) as process:
                running = False
                try:
                    wait_for(
                        lambda: PeerClient(management, timeout=2).get("/healthz"),
                        lambda value: True, "RIDE management", process,
                    )
                    original = ride("start", "controller", spec["type"], "--name", name,
                                    "--config", str(cli_spec))
                    running = True
                    first = wait_for(describe, lambda info: info["ready"], "first Controller")
                    self.assertEqual(first["job_id"], original["job_id"])
                    self.check_request(spec["endpoint"])
                    ride("stop", "controller", name)
                    running = False
                    self.assertEqual(describe()["job_status"], "STOPPED")
                    self.assertEqual(PeerClient(backend, timeout=2).get("/health")["status"], "ok")
                    restarted = ride("start", "controller", name)
                    running = True
                    ready = wait_for(describe, lambda info: info["ready"], "restarted Controller")
                    self.assertEqual(restarted["job_id"], ready["job_id"])
                    self.assertNotEqual(ready["job_id"], first["job_id"])
                    self.check_request(spec["endpoint"])
                except (AssertionError, OSError):
                    print(management_log.read_text(encoding="utf-8", errors="replace"))
                    raise
                finally:
                    if running:
                        ride("stop", "controller", name)

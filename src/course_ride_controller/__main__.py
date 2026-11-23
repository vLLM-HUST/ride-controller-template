"""Expose the RIDE SDK probe endpoints and a local, non-streaming request path."""

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from threading import Lock
from urllib.error import URLError
from urllib.parse import urlsplit

from ride.sdk import ApiError, PeerClient, PROTOCOL_VERSION, load_configuration

from .adapter import CONTROLLER_TYPE
from .policy import prepare_request


CHAT_PATH = "/v1/chat/completions"
MAX_BODY_BYTES = 1024 * 1024


def run(config):
    endpoint = urlsplit(config["endpoint"])
    if endpoint.scheme != "http" or endpoint.hostname != "127.0.0.1" or not endpoint.port:
        raise ValueError("The CPU starter needs an explicit http://127.0.0.1:PORT endpoint")

    backend = PeerClient(config["backend"], timeout=5)
    identity = {
        "id": config["id"],
        "role": CONTROLLER_TYPE,
        "configuration_sha256": config["_sha256"],
        "protocol_version": PROTOCOL_VERSION,
        "backend": backend.endpoint,
    }
    counters = {"completed": 0, "failed": 0}
    lock = Lock()

    class Handler(BaseHTTPRequestHandler):
        def reply(self, status, data):
            encoded = json.dumps(data, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def do_GET(self):
            if self.path == "/health":
                self.reply(200, {"status": "ok"})
            elif self.path == "/identity":
                self.reply(200, identity)
            elif self.path == "/status":
                with lock:
                    snapshot = dict(counters)
                self.reply(200, snapshot)
            else:
                self.reply(404, {"error": "Unknown endpoint"})

        def do_POST(self):
            if self.path != CHAT_PATH:
                self.reply(404, {"error": "Unknown endpoint"})
                return
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if not 0 < size <= MAX_BODY_BYTES:
                    raise ValueError("Expected a JSON body of at most 1 MiB")
                request = json.loads(self.rfile.read(size))
                if not isinstance(request, dict) or request.get("stream", False) is not False:
                    raise ValueError("Expected a non-streaming JSON request")
            except ValueError as error:
                self.reply(400, {"error": str(error)})
                return

            outgoing = prepare_request(request)
            try:
                response = backend.post(CHAT_PATH, outgoing)
            except ApiError as error:
                self.forwarded(error.status, {"error": "Model service rejected the request"})
            except (URLError, TimeoutError, ValueError):
                self.forwarded(502, {"error": "Model service unavailable or returned invalid JSON"})
            else:
                self.forwarded(200, response)

        def forwarded(self, status, payload):
            with lock:
                counters["completed" if status == 200 else "failed"] += 1
            self.reply(status, payload)

    with ThreadingHTTPServer(("127.0.0.1", endpoint.port), Handler) as server:
        server.serve_forever()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--configuration", required=True)
    parser.add_argument("--configuration-sha256", required=True)
    args = parser.parse_args()
    config = load_configuration(
        args.configuration, args.configuration_sha256, role=CONTROLLER_TYPE
    )
    run(config)


if __name__ == "__main__":
    main()

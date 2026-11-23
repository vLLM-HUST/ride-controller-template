# RIDE Control Plane Controller 模板

《大模型推理系统与实践》选做加分项目 · 为 yancan 负责的 RIDE Control Plane 开发一个插件 · 最多 +30 分。

本模板用 RIDE 公共 SDK 注册独立进程，提供健康、身份、状态和非流式请求入口。本地服务器监听回环地址，使用 CPU 模型替身复现请求。起始策略原样转发，学生在 `policy.py` 实现并验证自己的行为。

## 跑通 CPU 冒烟测试

在 Linux 或 WSL 的仓库根目录使用 Python 3.10 或更新版本执行。需要 Git 和 Python 的 `venv` 模块。Ubuntu 上若缺少 `venv`，先安装 `python3-venv`。

```bash
python3 -m venv .venv
source .venv/bin/activate
proxy_exceptions="${no_proxy:-${NO_PROXY:-}}"
export NO_PROXY="127.0.0.1,localhost${proxy_exceptions:+,$proxy_exceptions}"
export no_proxy="$NO_PROXY"
python -m pip install 'ride-control-plane @ git+https://github.com/vLLM-HUST/ride.git@e4fb25ec6920624c29f1ad2f261a6e8a4e55755b' .
python -m unittest discover -s tests -v
```

两条 `export` 让本机回环请求直连，同时保留已有的代理例外。Windows PowerShell 可用 `py -m venv .venv` 建环境，用 `.\.venv\Scripts\python.exe` 执行安装与默认测试。如果设置了 HTTP 代理，也请将 `127.0.0.1` 和 `localhost` 加入 `NO_PROXY`。默认用例从已安装的包读取注册入口，启动真实 Controller 和模型替身，验证配置哈希、身份、请求和状态。

在 Linux 或 WSL 同一虚拟环境执行下面的命令，同时验收 **真实 RIDE local 管理进程 → Controller → CPU 模型替身**，包含启动、请求、停止、后端仍正常、重新启动及再次发送请求。测试自动分配回环端口和临时状态目录。

```bash
RIDE_MANAGED_SMOKE=1 python -m unittest discover -s tests -v
```

## 实现自己的 Controller

1. 为发行包、模块、`ride.controllers` 注册类型和 SDK 角色取独立名称，同步修改配置与测试。
2. 从 `policy.py` 实现自己的策略，在 `__main__.py` 接入所需的业务状态和观测。保留 `/health`、`/identity`、`/status`，使 RIDE 能读取实际进程的身份和状态。
3. 参考 `tests/test_smoke.py` 的 `specification` 构造配置。SDK 要求绝对配置路径和配置文件的 SHA-256，实例 ID、角色及 endpoint 与实际进程保持一致。补上能触发新行为的请求和失败用例。
4. 在新建的输出目录构建并重新安装这次生成的 wheel，验证改名后的包仍能注册和处理请求。

```bash
python -m pip install build
wheel_dir="$(mktemp -d)"
python -m build --wheel --outdir "$wheel_dir"
python -m pip install --force-reinstall --no-deps "$wheel_dir"/*.whl
RIDE_MANAGED_SMOKE=1 python -m unittest discover -s tests -v
```

在 Linux 或 WSL 运行上述托管测试。真实模型、Ray 和性能验证按选题需要增补。第 8 讲提交项目，按 [实验记录模板](EXPERIMENT.md) 保存证据。

建议在项目内用 `evidence/` 保存复跑输出，并在 `EXPERIMENT.md` 写出相对路径。托管测试退出时会清理临时状态。若要提交 Ready、身份和状态原文，在自己的 Controller 运行期间执行 `ride describe controller "$controller_name" --address "$ride_address"`，将输出保存到 `evidence/ride-describe.json`。这两个变量分别是启动时的实例名称和 RIDE 管理地址。将策略触发请求、对照结果和生命周期测试输出一并保存。

[RIDE SDK 开发契约](https://github.com/vLLM-HUST/ride/blob/e4fb25ec6920624c29f1ad2f261a6e8a4e55755b/docs/guides/CONTROLLER_DEVELOPMENT.md)

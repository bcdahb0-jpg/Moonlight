# Moonlight Backend

FastAPI 与 WebSocket 后端，运行时包根为 `src/open_llm_vtuber`。

## 本地运行

请使用 Python 3.12 创建并维护 `backend/.venv`，然后在 `backend` 目录运行：

```powershell
& .\.venv\Scripts\python.exe run_server.py
```

默认监听 `127.0.0.1:12393`。存活探针为 `/healthz`，初始化探针为 `/readyz`；
业务依赖就绪状态由 `/api/readiness` 返回。

## 测试

```powershell
& .\.venv\Scripts\python.exe -m pytest tests\ -x -q --basetemp=.pytest-tmp
```

`conf.yaml` 是本机配置，可能包含真实 API key，禁止提交。发行构建不能直接复用
开发机 `.venv`，必须另行制作经过审查的 Python 运行时，并在干净 Windows 环境验证。

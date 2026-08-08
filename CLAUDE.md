# CLAUDE.md — Moonlight 项目环境速查

> 本项目完整约定见 **`AGENTS.md`（单一事实源）**，本文件为速查版，两者冲突时以 AGENTS.md 为准。
> 当前主任务：按 `docs/agent-harness-platform-plan.md`（v2.1）执行任务制智能体平台，Phase 0→6。

## 环境铁律（违反必炸）

1. **Python 只用 `backend/.venv`**（系统 Python 3.12.10）。禁止 managed 3.13 venv（`pydantic_core` 装不上）。命令一律 `.venv/Scripts/python.exe`。
2. **沙箱环境启动前端必须** `MOONLIGHT_USER_DATA=.electron-user-data`（否则 Electron SingletonLock 失败白屏）。
3. **杀后端用 `taskkill /F /PID <pid>`，绝不带 `/T`**（连坐杀 VOICEVOX 引擎）。
4. 后端存活 = `curl 127.0.0.1:12393` → 200。

## 端口

| 端口 | 服务 |
|---|---|
| 12393 | 后端 HTTP/WS（主） |
| 12394 | MCP 服务端（401 正常） |
| 5173 | Vite（Electron 加载） |
| 50021 | VOICEVOX 引擎（用户手动启动） |

## 启动

```bash
cd backend && ./.venv/Scripts/python.exe run_server.py          # 后端，日志 server_run.log
cd frontend && MOONLIGHT_USER_DATA=.electron-user-data npm run dev  # 前端
cd frontend && npx tsc --noEmit                                  # 前端类型校验
cd backend && ./.venv/Scripts/python.exe -m pytest tests/ -x -q  # 后端测试
```

## 网络

- Clash 代理 **7897**；GitHub：`git -c http.proxy=http://127.0.0.1:7897 clone ...`
- TLS 拦截证书不受信：curl `-k`；npm `NODE_TLS_REJECT_UNAUTHORIZED=0`
- 后端装包：`UV_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple uv add <pkg>`
- 前端装包：pnpm + `--registry=https://registry.npmmirror.com` + `npm_config_cache=".pnpm-cache"`

## 代码约定（要点）

- 后端包根 `backend/src/open_llm_vtuber/`；新路由 = `init_xxx_route()` + `server.py` 里 `include_router`（参考 `engine_route.py`）。
- conf.yaml 改完**重启后端**生效；敏感配置在 conf.yaml，勿提交展示。
- 前端渲染进程改动 HMR 生效；`electron/main.ts` 改动需重启 Electron。
- 样式在 `src/styles/global.css`（CSS 变量 `--bg-*`/`--text-*`）。

## 参考（只读，禁止合并）

- 计划书：`docs/agent-harness-platform-plan.md`
- 参考仓库：`reference/智能体平台/{deer-flow, pi-agent, dwsy-agent}`
- 新增模块：`backend/src/open_llm_vtuber/task_platform/` + 前端 TaskModeView

## 安全

`backend/conf.yaml` 含真实 API key，永不提交/展示。删除/杀进程前先确认目标。

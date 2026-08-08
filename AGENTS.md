# Moonlight — Coding Agent 环境速查与约定

> 本文件为 **单一事实源**，Claude Code 与 Codex 均应先读本文件再动手。
> 配套计划书：`docs/agent-harness-platform-plan.md`（任务制智能体平台 v2.1，评审通过，待执行）。

## 项目一句话

AI 桌宠（Moonlight）：FastAPI 后端 + React/Electron 前端 + Live2D + 语音全链路（ASR/TTS/翻译）+ 四层记忆 + MCP 双向。

## ⚠️ 环境铁律（违反必炸）

1. **Python 必须用 `backend/.venv`**（系统 Python 3.12.10 创建）。禁止用任何 managed 3.13 建新 venv——`pydantic_core` 原生扩展装不上，LangGraph/pydantic v2 必炸。所有 python/uv/pytest 命令用 `.venv/Scripts/python.exe`（Windows venv 路径，不要裸 `python`）。
2. **沙箱/托管环境（WorkBuddy 等）启动 Electron 必须设 `MOONLIGHT_USER_DATA=.electron-user-data`**（在 frontend 目录下），否则 Electron 在 `%APPDATA%\moonlight-frontend` 建 SingletonLock 失败（Error code: 5）白屏。用户自己终端启动则不需要。
3. **杀后端进程用 `taskkill /F /PID <pid>`（不带 `/T`）**——`/T` 连坐杀死后端名下所有子进程，会把 VOICEVOX 引擎一起杀掉。
4. 后端存活判断 = `curl http://127.0.0.1:12393` 返回 200；端口在 LISTENING 但 HTTP 000 属于假死，需要重启。

## 端口表

| 端口 | 服务 | 说明 |
|---|---|---|
| 12393 | 后端 HTTP/WS | 主 API，健康检查用 |
| 12394 | MCP 服务端 | FastMCP + 鉴权，返回 401 属正常 |
| 5173 | Vite 前端 | Electron 加载 `http://127.0.0.1:5173` |
| 50021 | VOICEVOX 引擎 | 本地 TTS，由用户手动启动（`backend/vendor/voicevox_engine/windows-cpu/run.exe`），`/version` 返回 200 即在线 |

## 启动 / 重启 / 停止

```bash
# 后端（frontend 同级别目录）
cd backend && ./.venv/Scripts/python.exe run_server.py        # 日志 backend/server_run.log

# 前端（Electron 窗口）
cd frontend && MOONLIGHT_USER_DATA=.electron-user-data npm run dev   # 日志 frontend/dev.log

# 验证
curl http://127.0.0.1:12393 -o /dev/null -w "%{http_code}"    # 期望 200
curl http://127.0.0.1:5173  -o /dev/null -w "%{http_code}"    # 期望 200
tasklist | grep -i electron                                    # 主/GPU/渲染 3-4 个进程

# 停止（按端口找 PID，勿按进程名批量杀 electron.exe——可能误杀 WorkBuddy 自身）
netstat -ano | findstr 12393
taskkill /F /PID <pid>
```

## 网络 / 依赖安装（本机代理 TLS 拦截）

- Clash 代理端口 **7897**（非常规 7890）。GitHub 慢/early EOF：`git -c http.proxy=http://127.0.0.1:7897 clone/pull <repo>`。
- 代理做了 TLS 拦截但证书不受信 → curl 加 `-k`；npm/pnpm 用 `NODE_TLS_REJECT_UNAUTHORIZED=0 npm_config_strict_ssl=false`。
- **Python 装包（后端）**：`cd backend && UV_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple uv add <pkg>`（uv 走清华镜像，避开 TLS 坑）。
- **前端装包**：`NODE_TLS_REJECT_UNAUTHORIZED=0 npm_config_strict_ssl=false npm_config_cache=".pnpm-cache" pnpm add <pkg> --registry=https://registry.npmmirror.com`（缓存指向项目内相对路径，写 `AppData\Local\pnpm-cache` 会被沙箱拦截）。

## 后端开发约定

- 包根：`backend/src/open_llm_vtuber/`。
- **新路由模式**：写 `init_xxx_route()` 返回 `APIRouter`，在 `server.py` 的 `setup_routes()` 里 `self.app.include_router(init_xxx_route())` 注册（参考 `memory_route.py` / `engine_route.py` / `perf_route.py`）。
- 配置：`backend/conf.yaml`（YAML）。配置类大多启动时读取，**改完需重启后端生效**；字符级写入参考 `translator_route` 的 `_write_engine_fields`（surgical leaf 写入 + 白名单）。
- 测试：`backend/tests/`（unittest 风格），跑法 `cd backend && ./.venv/Scripts/python.exe -m pytest tests/ -x -q`。
- 日志：loguru，输出在 `backend/server_run.log`。
- 依赖清单：`backend/pyproject.toml`（uv 管理，勿手改 lock 之外的依赖）。

## 前端开发约定

- 渲染进程改动（`frontend/src/**`）Vite HMR 自动生效；`electron/main.ts` / `preload.ts` 改动需要重启 Electron（dev.mjs watch 会自动 rebuild，偶发单例锁失败则手动重启）。
- 校验：`cd frontend && npx tsc --noEmit`（必须 0 错误）。
- 样式：`frontend/src/styles/global.css`（暗夜月光主题，CSS 变量 `--bg-*` / `--text-*` / `--gradient-main`，新样式沿用）。
- 会话/消息/WS 协议类型：`frontend/src/core/types/ws.ts` + `messageHandlers.ts`（后端 `contracts.py` 为源头）。
- 依赖：pnpm（见网络节）。

## 当前主计划（执行中，优先事项）

1. `docs/agent-harness-platform-plan.md` —— **任务制智能体平台**（v2.1，已评审通过）：新建任务（绑工作目录）→ skill 系统 → LangGraph agent → 本地直执行+路径白名单 → 前端任务模式。按 Phase 0→6 执行，每阶段完成更新计划书状态。
2. 参考仓库（只读借鉴，**禁止合并代码**）：`reference/智能体平台/{deer-flow, pi-agent, dwsy-agent}`。
3. 新增模块目标：后端 `backend/src/open_llm_vtuber/task_platform/`（独立包，不侵入 `conversations/`/`memory/`/`mcp/`）；前端 `TaskModeView` + 导航「任务」分区。

## 已知遗留（非本次任务范围，不要"顺手修"）

- MCP 外部工具 `time`（SDK `McpError` 命名冲突）/ `ddg-search`（PyPI 无此包）连接失败 → MCP 工具数为 0，**不影响主链路**。
- DeepLX 已弃用，翻译引擎固定 LLM（DeepSeek）。
- 桌宠默认角色小月：DeepSeek + VOICEVOX（日语引擎），中文回复经 LLM 翻译成日文合成，属正常链路。

## 安全红线

- `backend/conf.yaml` 含真实 API key，是本地配置，**永不提交/展示**；不要为"方便"放宽 `.gitignore`。
- 删除操作（文件/进程/目录）前先列清单；杀进程前确认 PID 归属。

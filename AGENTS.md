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
5. **后端只能由用户在自己终端启动**（`cd backend && ./.venv/Scripts/python.exe run_server.py`）。WorkBuddy/沙箱启动的后端进程落在沙箱身份上：会在用户目录建 `C:\Users\Elysia\.pi` 等目录并注入 `CodexSandboxUsers:(OI)(CI)(RX)` 只读 ACL → 桌宠 delegate_to_task 建任务 mkdir 被拒，**Windows 伪报 [WinError 2] 系统找不到指定的文件**（并非路径不存在）→ 聊天一切工具调用 500。识别沙箱启动：进程 ppid 链到 `~/.workbuddy/vendor/PortableGit/usr/bin/bash.exe`。验证/调试后端可用沙箱，但**长期运行必须移交用户终端**。
6. **后端启动 segfault（Segment fault）时先查 onnxruntime DLL 劫持**（2026-08-12 修复）：`C:\Windows\SYSTEM32\onnxruntime.dll`（Windows 内置 ORT 1.17）会劫持 sherpa_onnx pyd 的按名加载 → 加载模型时 C++ 层崩溃（伴随警告 "The requested API version [27] is not available..."）。**修复已内置**：`run_server.py` 与 `asr/sherpa_onnx_asr.py` 顶部 `_preload_onnxruntime_dll()`（必须在 `import sherpa_onnx` 前显式 `ctypes.WinDLL` venv 的 `onnxruntime/capi/onnxruntime.dll`）。若改了 import 顺序或换了 C 扩展，保持"先 preload 后 import"。
7. **MCP 服务端（12394）由 `run_server.py` 在 `server.initialize()` 成功后启动**（`server.start_mcp_service()`，2026-08-12 从 WebSocketServer 构造函数移出）——构造函数里启动会与 ASR 模型加载并发。改后端启动流程时勿把 MCP 启动移回构造函数。

## 端口表

| 端口 | 服务 | 说明 |
|---|---|---|
| 12393 | 后端 HTTP/WS | 主 API，健康检查用 |
| 12394 | MCP 服务端 | FastMCP + 鉴权，返回 401 属正常 |
| 5173 | Vite 前端 | Electron 加载 `http://127.0.0.1:5173` |
| 50021 | VOICEVOX 引擎 | 本地 TTS，由用户手动启动（`backend/vendor/voicevox_engine/windows-cpu/run.exe`），`/version` 返回 200 即在线 |
| 1188 | DeepLX 翻译 | 本地翻译服务（`backend/vendor/deeplx/deeplx.exe`），`POST /v2/translate` 返回 JSON 即在线 |

## 启动 / 重启 / 停止

> 📖 **AI 快速启动教程**：`docs/startup-runbook.md`（探测→补齐→验证全流程 + 故障排查表 + 沙箱专项，Agent 启动前必读）。下面是最简速查：

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

### 本地引擎（按需，不影响主链路）

| 引擎 | 端口 | 一键启动 | 手动启动 / 健康检查 |
|---|---|---|---|
| VOICEVOX（日语 TTS） | 50021 | 前端「性能 → 引擎库 → VOICEVOX」卡内下载/启动/停止（沙箱/托管环境需用户自己终端手动，见下） | `cd backend/vendor/voicevox_engine/windows-cpu && ./run.exe`；`curl http://127.0.0.1:50021/version` 返回 JSON 即在线 |
| DeepLX（本地翻译） | 1188 | 前端「语音 → 跨语音翻译 → 引擎选 DeepLX」卡内「一键启动/停止」按钮（后端 spawn，**沙箱亦可**） | `cd backend/vendor/deeplx && ./start_deeplx.bat`（或直接 `./deeplx.exe`）；健康检查 `curl -X POST http://127.0.0.1:1188/v2/translate -H "Content-Type: application/json" -d '{"text":["hi"],"target_lang":"JA"}'` 返回 JSON 即在线（偶发 429 为 DeepL 官方限流，属外部限制） |

DeepLX 管理接口（仿 VOICEVOX 引擎管理）：`GET /api/deeplx/status`、`POST /api/deeplx/start|stop`（`deeplx_manager.py`）。

## 网络 / 依赖安装（本机代理 TLS 拦截）

- Clash 代理端口 **7897**（非常规 7890）。GitHub 慢/early EOF：`git -c http.proxy=http://127.0.0.1:7897 clone/pull <repo>`。
- 代理做了 TLS 拦截但证书不受信 → curl 加 `-k`；npm/pnpm 用 `NODE_TLS_REJECT_UNAUTHORIZED=0 npm_config_strict_ssl=false`。
- **Python 装包（后端）**：`cd backend && UV_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple uv add <pkg>`（uv 走清华镜像，避开 TLS 坑）。大包（playwright 36MB 等）下载易中断：加 `UV_HTTP_TIMEOUT=300` + dangerouslyDisableSandbox 重试。
- **前端装包**：`NODE_TLS_REJECT_UNAUTHORIZED=0 npm_config_strict_ssl=false npm_config_cache=".pnpm-cache" pnpm add <pkg> --registry=https://registry.npmmirror.com`（缓存指向项目内相对路径，写 `AppData\Local\pnpm-cache` 会被沙箱拦截）。
- **MCP server（fetch/time）**：上游仍用旧名 `McpError`，新版 mcp SDK 改名 `MCPError` → ImportError。conf.yaml 必须 `args: [--with, "mcp==1.29.0", mcp-server-xxx]`（2026-08-09 实测根因，勿删）。

## 后端开发约定

- 包根：`backend/src/open_llm_vtuber/`。
- **新路由模式**：写 `init_xxx_route()` 返回 `APIRouter`，在 `server.py` 的 `setup_routes()` 里 `self.app.include_router(init_xxx_route())` 注册（参考 `memory_route.py` / `engine_route.py` / `perf_route.py`）。
- 配置：`backend/conf.yaml`（YAML）。配置类大多启动时读取，**改完需重启后端生效**；字符级写入参考 `translator_route` 的 `_write_engine_fields`（surgical leaf 写入 + 白名单）。
- 测试：`backend/tests/`（unittest 风格），跑法 `cd backend && ./.venv/Scripts/python.exe -m pytest tests/ -x -q --basetemp=.pytest-tmp`（沙箱身份写 `%LOCALAPPDATA%\Temp` 被拒，必须 --basetemp 重定向到项目内；`.pytest-tmp` 已 gitignore）。
- 日志：loguru，输出在 `backend/server_run.log`。
- 依赖清单：`backend/pyproject.toml`（uv 管理，勿手改 lock 之外的依赖）。
- **已知基线失败**（非 task_platform 范围勿顺手修）：`test_smoke.py::TestTtsFilter::test_url_and_decimal_not_mangled`（TTS 过滤器 remove_special_char 清掉 URL 冒号斜杠）。

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
- 翻译引擎：conf.yaml `translate_provider` 实际为 **deeplx**（见「本地引擎」节一键管理）；`llm`（DeepSeek）可切换，前端「跨语音翻译」卡可切换。
- 桌宠默认角色小月：DeepSeek + VOICEVOX（日语引擎），中文回复经翻译引擎（deeplx）翻成日文合成，属正常链路。

## 屏幕感知（screen_awareness，Phase 0~6 已完成）

- 后端包：`backend/src/open_llm_vtuber/screen_awareness/`；API `/api/screen/{status,metrics,analyze,clear,config,feedback}`（localhost-only）；WS 入站 `screen-frame/screen-enable/screen-clear`，出站 `screen-status/screen-context`。
- 配置：conf.yaml `system_config.screen_awareness`（provider/base_url/model/api_key/local_only/阈值/冷却/黑名单/单价）。当前已配 **SiliconFlow Qwen3-VL-8B-Instruct**（视觉识别），key 复用 vector_embedding_api_key；DeepSeek 纯文本不支持视觉 → analyze 返回 None（fail-soft）。
- 前端：`frontend/src/screen/`（useScreenAwareness 编排 + CaptureScheduler + frameDiff pHash + screenContextClient 上传 + privacyRules + screenActions + ScreenEyeIndicator 状态灯 + ScreenAuthModal 授权 + onDemandCapture）；Electron IPC `screen:capture-active-window-v2`。
- 聊天融合：`single_conversation._attach_screen_context` —— 摘要 `[屏幕上下文]` 只进 LLM 不进历史/记忆；关键词（屏幕/报错/这里/这个页面等）命中才附图像。
- 主动陪聊：`conversation_handler` ai-speak-signal 接入 `screen_awareness.policy.decide_proactive_for`（silence 放弃本轮；interrupt/light_chat 注入 hint）；video/reading/game 沉浸静默，冷却 180s；抑制原因统计在 `/api/screen/metrics`。
- 成本/反馈：metrics 按输入/输出 token 分计，`estimated_cost_usd` 按 conf 单价估算；`POST /api/screen/feedback`（useful/disruptive/misrecognition）。
- 测试：`tests/test_screen_awareness.py`（43 用例）；soak：`scripts/screen_soak.py`。文档：`docs/screen-awareness-implementation.md`（交付）+ `docs/screen-awareness-ops.md`（运维/隐私/故障/回滚）。

## 安全红线

- `backend/conf.yaml` 含真实 API key，是本地配置，**永不提交/展示**；不要为"方便"放宽 `.gitignore`。
- 删除操作（文件/进程/目录）前先列清单；杀进程前确认 PID 归属。

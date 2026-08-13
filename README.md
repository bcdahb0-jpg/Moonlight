# Moonlight

Moonlight 是一个 Windows 优先的 AI 桌宠：后端使用 FastAPI，前端使用 React + Electron，包含 Live2D、语音识别、语音合成、翻译、记忆、MCP 和任务制智能体能力。

当前仓库处于“功能快速迭代、发布工程补齐中”阶段。正式上线前必须完成运行时打包、干净环境安装、隐私与权限审查，以及真实设备上的语音和 Live2D 回归。

## 快速启动

后端必须使用项目内 Python 3.12 虚拟环境：

```powershell
cd backend
& .\.venv\Scripts\python.exe run_server.py
```

前端开发环境：

```powershell
cd frontend
$env:MOONLIGHT_USER_DATA = '.electron-user-data'
pnpm dev
```

健康检查：

```powershell
curl http://127.0.0.1:12393/healthz
curl http://127.0.0.1:12393/readyz
```

`/healthz` 只表示后端进程存活；`/readyz` 表示配置和服务上下文已经完成初始化。LLM、ASR、VOICEVOX、DeepLX 的业务可用性由 `/api/readiness` 和控制台接口分别报告。

## 开发校验

```powershell
# 后端
cd backend
& .\.venv\Scripts\python.exe -m pytest tests\ -x -q --basetemp=.pytest-tmp

# 前端
cd ..\frontend
pnpm typecheck
pnpm build
```

发布前可以运行：

```powershell
cd frontend
pnpm check:release
```

脚本会检查敏感配置是否被 Git 跟踪、前端类型检查与构建、后端虚拟环境入口，以及 Electron 打包配置中的后端运行时状态；它不会打印 `backend/conf.yaml` 的内容。

## 目录结构

| 目录 | 作用 |
| --- | --- |
| `backend/src/open_llm_vtuber` | FastAPI、WebSocket、语音、记忆、MCP 和任务平台 |
| `backend/config_templates` | 不含真实密钥的默认配置模板 |
| `frontend/src` | React 渲染进程、状态、设置、Live2D 与屏幕感知 |
| `frontend/electron` | Electron 主进程、IPC、窗口和后端托管 |
| `contracts` | WebSocket 与 HTTP 契约文档 |
| `docs` | 设计、运维和阶段性实现记录 |
| `reference` | 只读借鉴仓库，禁止直接合并代码 |

## 本地引擎

- VOICEVOX：`127.0.0.1:50021`，`GET /version` 返回 200 即在线。
- DeepLX：`127.0.0.1:1188`，`POST /v2/translate` 返回 JSON 即在线。
- MCP：后端初始化成功后监听 `127.0.0.1:12394`，无 Bearer token 返回 401 属正常行为。

本地引擎由用户按需启动。停止后端时只结束后端 PID，不要使用 `taskkill /T`，否则可能误杀 VOICEVOX 子进程。

## 安全边界

- `backend/conf.yaml` 是本机配置，可能包含真实 API key，永不提交、粘贴或展示。
- 默认后端只监听 `127.0.0.1`。只有明确理解风险时才改为局域网监听。
- 设置、插件安装、任务工作目录等高影响接口应保持 localhost-only，并继续使用路径白名单和 zip-slip 防护。
- Electron 生产 CSP 只允许本地后端和应用自身资源；需要新增外部服务时应先审查域名和数据流向。

## 已知发布阻断项

目前还不能把仓库当作“下载后双击即可用”的正式安装包，主要原因是：

1. Electron builder 尚未把经过审查的 Python 后端运行时、依赖和非敏感默认资源组成可复现的发行目录。
2. `backend/.venv` 是本地开发环境，若解释器路径失效需要重新创建，不能直接作为发行运行时假定存在。
3. 仍需在干净 Windows 用户环境验证首次启动、模型下载、音频设备、GPU/软件渲染、升级和卸载。
4. 需要补充端到端冒烟、崩溃恢复、配置迁移、权限拒绝和离线降级测试，并建立版本化发布清单。

建议按“运行时打包 → 干净机安装 → 核心链路回归 → 隐私/安全审查 → 签名发布”的顺序推进。

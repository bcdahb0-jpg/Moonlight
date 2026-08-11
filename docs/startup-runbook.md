# Moonlight 启动 Runbook（AI 快速启动指南）

> 目标读者：**coding agent（Claude Code / Codex / WorkBuddy 等）**。本机是 Windows + Git Bash + WorkBuddy 沙箱环境，本文件包含所有经过实测的坑与处置。
> 人类手动启动也能参考第 2 节，但 VOICEVOX 一节只适用于本机（AI 沙箱身份无权启动）。

---

## 0. 核心原则：先探测，再动手

**不要盲目"重启一切"**——服务往往还活着，重启反而制造问题。标准流程：

1. **探测**三个端口 + Electron 进程（第 1 节命令）
2. **比对**健康标准，找出缺的那一件
3. **只补缺失件**，验证

---

## 1. 三件套与端口速查

| 服务 | 端口 | 启动者 | 启动命令 | 日志 | 健康标准 |
|---|---|---|---|---|---|
| 后端 FastAPI | 12393 | AI | `cd backend && ./.venv/Scripts/python.exe run_server.py` | `backend/server_run.log` | HTTP 200 |
| 前端 Vite + Electron | 5173 | AI | `cd frontend && MOONLIGHT_USER_DATA=.electron-user-data npm run dev` | `frontend/dev.log` | HTTP 200 + electron.exe ≥3 进程 |
| VOICEVOX 引擎 | 50021 | **用户手动** | `cd backend/vendor/voicevox_engine/windows-cpu && ./run.exe` | 引擎控制台 | `/version` HTTP 200 |

**注意**：VOICEVOX 只能用户手动启动（AI 沙箱进程身份写 `%LOCALAPPDATA%\voicevox-engine` 被拒 → 用户词典写文件崩溃），AI 不要尝试，也不要因为没起而报错，只提醒用户。

---

## 2. 快速启动（冷启动完整流程）

### Step 1 — 探测当前状态

```bash
netstat -ano | findstr ":12393 :5173 :50021" | findstr LISTENING
tasklist /FI "IMAGENAME eq electron.exe"   # 期望 3-4 个进程
```

健康检查（**Git Bash 下必须 `-o NUL`，不要 `-o /dev/null`**——后者被沙箱解析成 `C:\dev\null` 触发拦截）：

```bash
curl -s -o NUL -w "backend=%{http_code}\n" http://127.0.0.1:12393
curl -s -o NUL -w "frontend=%{http_code}\n" http://127.0.0.1:5173
curl -s -o NUL -w "voicevox=%{http_code}\n" http://127.0.0.1:50021/version
```

### Step 2 — 按缺口补齐

| 缺口 | 处置 |
|---|---|
| 12393 无监听 或 LISTENING 但 HTTP 000（假死） | 杀掉 12393 对应 PID → 重启后端 |
| 5173 无监听，或 5173 在但 electron.exe 进程数为 0（窗口没开） | 杀掉旧 dev 进程树 → 重启前端 |
| 50021 无监听 | 提醒用户手动启动 VOICEVOX（AI 不要自己试） |

**重启后端**（只杀 12393 的 PID，不带 `/T`，防连坐杀死 VOICEVOX）：

```bash
netstat -ano | findstr ":12393" | findstr LISTENING   # 取最后一列 PID
taskkill /F /PID <pid>
cd backend && ./.venv/Scripts/python.exe run_server.py > server_run.log 2>&1   # 后台运行
```

**重启前端**（杀 dev 进程树要带 `/T`，因为 Vite/esbuild 是 dev.mjs 的子进程；杀掉前先确认 PID 是 node dev 进程）：

```bash
tasklist /FI "IMAGENAME eq node.exe" | head -20    # 找 dev.mjs 主进程（内存最大的 node）
taskkill /F /T /PID <dev.mjs主进程>                # /T 连坐杀 Vite/esbuild，这是期望行为
cd frontend && MOONLIGHT_USER_DATA=.electron-user-data npm run dev > dev.log 2>&1   # 后台运行
```

### Step 3 — 验证全链路

```bash
# 三端口全 200
curl -s -o NUL -w "%{http_code}" http://127.0.0.1:12393
curl -s -o NUL -w "%{http_code}" http://127.0.0.1:5173
curl -s -o NUL -w "%{http_code}" http://127.0.0.1:50021/version
# Electron 窗口进程
tasklist /FI "IMAGENAME eq electron.exe"
# 前后端打通：渲染进程应能从后端加载 Live2D 模型
tail -5 frontend/dev.log   # 期望出现 [live2d] engine=... model=http://127.0.0.1:12393/live2d-models/...
```

---

## 3. AI 沙箱环境专项（每条都是踩过的坑，违反必炸）

1. **Electron 必须设 `MOONLIGHT_USER_DATA=.electron-user-data`**（在 frontend 目录下执行）。否则 Electron 在 `%APPDATA%\moonlight-frontend` 建 SingletonLock 失败（Error code: 5）→ 白屏。用户自己终端启动不需要。
2. **Python 必须用 `backend/.venv`**（系统 Python 3.12.10 创建）。managed Python 3.13 建的 venv 装不上 `pydantic_core` 原生扩展，LangGraph/pydantic v2 必炸。所有 python/uv/pytest 用 `backend/.venv/Scripts/python.exe`，不要裸 `python`。
3. **curl 输出重定向用 `-o NUL`**（Git Bash 的 `/dev/null` 会被沙箱解析成 `C:\dev\null` → SANDBOX EXECUTION REJECTED）。命令本身能执行完，但会被拦一次。
4. **杀进程纪律**：
   - 杀后端进程：`taskkill /F /PID <pid>`，**绝不带 `/T`**（/T 连坐杀死后端名下 VOICEVOX）。
   - 杀前端 dev 进程树：`taskkill /F /T /PID <pid>`，可带 /T（Vite/esbuild 是它的子进程，就是要一起死）。
   - 杀进程前必须 `netstat -ano | findstr <port>` 确认 PID 归属；**禁止**按进程名批量杀 electron.exe（可能误杀 WorkBuddy 自身）。
5. **判活标准**：端口 LISTENING ≠ 活着。HTTP 000 = 假死（端口在但服务已挂），必须杀 PID 重启。
6. **依赖安装**（冷启动缺依赖时才需要，见第 6 节）：代理 TLS 拦截 + 沙箱外网限制，必须带环境变量和镜像，不能裸装。

---

## 4. 故障排查表

| 症状 | 根因 | 处置 |
|---|---|---|
| Electron 窗口白屏 / SingletonLock Error code 5 | 没设 `MOONLIGHT_USER_DATA` | 带环境变量重启前端 dev |
| 5173 在监听，但 electron.exe 0 进程 | dev.mjs 活着但 Electron 没拉起（常见于 watch rebuild 后） | 杀 dev 进程树 → 重启前端 dev |
| 端口 LISTENING 但 curl 000 | 进程假死（Python 崩溃但 socket 未释放等） | 按端口找 PID → `taskkill /F /PID` → 重启对应服务 |
| 后端启动报 `No module named 'pydantic_core'` | 用了 managed 3.13 venv | 改用 `backend/.venv`（3.12），必要时重建 venv |
| VOICEVOX 启动崩溃 `PermissionError: user.dict_csv` | AI 沙箱身份写 `%LOCALAPPDATA%` 被拒 | **只能用户手动启动**，AI 不试 |
| `SANDBOX EXECUTION REJECTED` + 路径 `C:\dev\null` | curl `-o /dev/null` 被沙箱解析 | 改 `-o NUL` |
| 后端 HTTP 200 但前端页空白 / 模型加载失败 | 后端假死或跨端口问题 | 查 `backend/server_run.log` 尾部 + 重启后端 |
| 杀掉 dev 后 5173 还占着 | 杀错了 PID 或残留子进程 | 再 `netstat -ano | findstr :5173` 找残留 PID 杀 |

---

## 5. 安全停止

```bash
# 先找 PID，确认归属，再杀（后端不带 /T，防连坐 VOICEVOX）
netstat -ano | findstr ":12393 :5173" | findstr LISTENING
taskkill /F /PID <后端pid>
taskkill /F /T /PID <前端dev主进程pid>
# VOICEVOX 由用户在自己的终端 Ctrl+C 停止
```

---

## 6. 冷启动缺依赖时的安装命令（实测可用组合）

**后端（uv 走清华镜像，避开 TLS 坑）**：

```bash
cd backend
UV_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple uv sync        # 或 uv add <pkg>
# 大包下载易中断：加 UV_HTTP_TIMEOUT=300，且需 dangerouslyDisableSandbox
```

**前端（pnpm 走阿里镜像 + 关闭 TLS 校验 + 缓存重定向到项目内）**：

```bash
cd frontend
NODE_TLS_REJECT_UNAUTHORIZED=0 npm_config_strict_ssl=false npm_config_cache=".pnpm-cache" pnpm install --registry=https://registry.npmmirror.com
# 装完删除临时的 .pnpm-cache；必须 dangerouslyDisableSandbox（沙箱默认无外网）
```

**注意**：代理端口是 **7897**（非 7890）。GitHub 慢/early EOF：`git -c http.proxy=http://127.0.0.1:7897 pull/clone`。

---

## 7. 一句话总结

> 探测三端口 → 只补缺的 → 后端杀 PID 不带 /T，前端杀进程树带 /T → 前端必须带 MOONLIGHT_USER_DATA → curl 用 -o NUL → VOICEVOX 留给用户起。

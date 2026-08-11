# Moonlight 废弃 / 临时文件清理候选报告

> 生成时间：2026-08-10 20:58 · 扫描模式：**只读**，未删除任何文件
> 范围：项目根目录 / `backend/` / `frontend/`（不含 `reference/`、`backend/vendor/`、`backend/models/`、`frontend/node_modules/` 内部）

---

## 摘要

| 类别 | 含义 | 数量 | 可释放空间 |
|---|---|---|---|
| A. 明确垃圾 | 崩溃转储、测试产物、临时探测文件 | 12 项 | ~0.2 MB |
| B. 运行缓存 | 日志 / `__pycache__` / 构建产物 / 旧 Electron 数据 | 10 项 | ~175 MB |
| C. 任务平台运行时 | 历史任务工作目录 + 检查点 | 1 项（24 个子目录） | ~390 MB |
| D. 历史/重复文件 | 双锁文件、配置备份、一次性导出 | 7 项 | ~1 MB |

**明确保留（勿删）**：`reference/`(2.8G 参考仓库)、`backend/vendor/`(3.7G VOICEVOX)、`backend/models/`(1.1G)、`frontend/node_modules/`(675M)、`backend/.venv/`、`backend/.pi/`(8/10 19:27 仍活跃)、`frontend/.electron-user-data/`(88M，当前 Electron 正在用)。

---

## A 类 · 明确垃圾（删除零风险，建议清）

| # | 路径 | 大小 | 最后修改 | 说明 |
|---|---|---|---|---|
| A1 | `bash.exe.stackdump` | 538 B | 08-08 | Git Bash 崩溃转储，纯垃圾（已被 gitignore） |
| A2 | `.electron_procs.txt` | 3.1 KB | 08-10 09:30 | 某次探测 Electron 进程命令的 stdout 残留文件 |
| A3 | `jina_test.txt` | 5.6 KB | 08-09 | jina.ai 抓取测试产物（内容实为 Cloudflare 拦截页，测试失败） |
| A4 | `tavily_test.json` | 87 B | 08-09 | tavily API 测试产物（Unauthorized 错误响应） |
| A5 | `backend/_list_ref.py` | ~1 KB | 08-08 | Phase 2a 探索 reference 用毕的辅助脚本 |
| A6 | `backend/.pytest_tmp_verify/` | ~0.5 MB | 08-10 | 手动 pytest 验证临时目录（非正式 `.pytest-tmp`） |
| A7 | `backend/server_run.log` | 24 KB | 08-10 | 后端运行日志（根级，正式日志在 `backend/logs/`） |
| A8 | `backend/server.log` | 64 KB | 08-10 | 同上 |
| A9 | `backend/server_run_v66.log` | 104 KB | 08-10 | 旧版本启动日志 |
| A10 | `backend/voicevox_run.log` | ~0 KB | — | VOICEVOX 启动日志 |
| A11 | `frontend/dev.log` | 40 KB | — | 前端运行日志 |
| A12 | `frontend/frontend_dev.log` | 36 KB | — | 前端运行日志（旧命名） |

## B 类 · 运行缓存（删除后下次运行自动重建）

| # | 路径 | 大小 | 说明 |
|---|---|---|---|
| B1 | `backend/logs/`（143 个文件） | 62 MB | loguru 调试日志，`debug_*.log` 为主（单日最大 9.5 MB）。建议**保留最近 3 天**，其余删 |
| B2 | `backend/` 下 `__pycache__/`（405 个目录） | 50.9 MB | Python 字节码缓存，自动重建 |
| B3 | `frontend/.user-data/` | 38 MB | 旧 Electron 用户数据（8/8 后未用；当前用 `.electron-user-data`） |
| B4 | `frontend/.electron-userdata/` | 21 MB | 旧命名变体（8/7 后未用） |
| B5 | `frontend/.moonlight-user-data/` | 20 MB | 中间版本命名（8/9 后未用） |
| B6 | `frontend/node_modules/.vite/` | 9.3 MB | Vite 预构建缓存 |
| B7 | `backend/.pytest-tmp/` | 4.3 MB | pytest 临时目录（gitignore 已列入，可随时清） |
| B8 | `frontend/dist/` | 4.4 MB | 构建产物，`npm run build` 可重建 |
| B9 | `frontend/dist-electron/` | 80 KB | Electron 构建产物，可重建 |
| B10 | `backend/.pytest_cache/` | 47 KB | pytest 缓存 |

> B3/B4/B5 三个旧 Electron 数据目录共 79 MB，只是浏览器缓存（Cache/GPUCache 等），不影响代码；当前在用的 `.electron-user-data`(88M) 不在清理之列。

## C 类 · 任务平台运行时数据（需你决策，默认建议保留）

| 路径 | 大小 | 说明 |
|---|---|---|
| `backend/tasks/` | **388 MB** | 历史任务工作目录（8/8 ~ 8/10，24 个任务），已 gitignore |

其中大户：
- `task-20260809204710/` — **340 MB**：内含 clone 的 `AI-Vtuber` 仓库（163 MB，含 67 MB git pack）+ pi 检查点 `checkpoints.db`（71 MB）
- `task-20260808212317/` — 18 MB
- `task-20260810185556/` — 8.5 MB
- 其余 21 个任务共 ~20 MB（多个为空壳，1 KB）

相关但不建议现在动：
- `backend/.pi/`（8/10 19:27 仍有更新）— **任务平台活跃运行时，勿删**
- `docs/.pi/`（memory.db + tasks，8/10 13:26 更新）— 疑似 pi 运行时残留，不确定用途，**保守保留**
- `backend/task_platform.db`（1.6 MB）— 任务元数据 SQLite，删除 = 清空任务历史

**选项**：① 全保留（等任务平台稳定后再清）；② 只删 8/9 及以前的旧任务（约 370 MB）；③ 全清（任务历史不可追溯）。

## D 类 · 历史/重复文件（你逐个判断）

| # | 路径 | 大小 | 说明 |
|---|---|---|---|
| D1 | `frontend/package-lock.json`（已跟踪） | 304 KB | npm 锁文件；项目标准是 pnpm（`pnpm-lock.yaml` 204K 反而已**未跟踪**，建议统一为 pnpm 并把 pnpm-lock.yaml 提交） |
| D2 | `backend/conf.yaml.bak` | 32 KB | 配置备份（**可能含 API key**，勿提交/外传；是否留作回滚由你定） |
| D3 | `backend/conf.yaml.backup` | 16 KB | 同上 |
| D4 | `docs/java-basics.html` | 4.9 KB | 8/9 生成的 Java 基础笔记页，像学习产物 |
| D5 | `frontend/ui-preview.html` | 48 KB | 8/8 的 UI 一次性预览导出 |
| D6 | `backend/chat_history/` | 889 KB | 聊天历史运行时数据（删=丢历史会话） |
| D7 | `experience-review-pomodoro-task.md` / `task-card-ux-fix-2026-08-10.md` | 12 KB | 复盘文档，多半要留 |

---

## 下一步

在本报告中勾选要清理的条目（或直接说「A 全清」「A+B，B1 留 3 天」等），我会**先列出完整删除清单和预计释放空间，再次跟你确认**，然后按批次执行（进回收站，不直接销毁）。

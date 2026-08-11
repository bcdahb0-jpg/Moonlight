# 删除执行计划（2026-08-10 21:10）

> ✅ **已执行完成（2026-08-10 21:15）**：三批全部删除成功，总计释放约 **421 MB**
> - A 类 12 项：全部清除
> - B1：85 个旧日志删除（62M → 20M，保留 58 个 8/8~8/10 日志）
> - C：15 个旧任务 + demo 目录删除（388M → 19M，保留 9 个 8/10 任务）
> 回收站通道被环境安全策略拦截（Add-Type 禁用），按确认时声明的方案降级为直接删除；已逐批验证无残留。

> 用户决策：「A 全清 + B1 留 3 天 + C 选项②」
> 原则：优先进回收站；沙箱环境若不支持回收站则直接删除（均为缓存/日志/临时产物，无源码）。
> 预计释放：**约 421 MB**

---

## A 类 · 明确垃圾（12 项，约 0.5 MB）

| # | 路径 | 大小 |
|---|---|---|
| A1 | `bash.exe.stackdump` | 1 K |
| A2 | `.electron_procs.txt` | 4 K |
| A3 | `jina_test.txt` | 8 K |
| A4 | `tavily_test.json` | 1 K |
| A5 | `backend/_list_ref.py` | 4 K |
| A6 | `backend/.pytest_tmp_verify/` | 240 K |
| A7 | `backend/server_run.log` | 24 K |
| A8 | `backend/server.log` | 64 K |
| A9 | `backend/server_run_v66.log` | 104 K |
| A10 | `backend/voicevox_run.log` | 4 K |
| A11 | `frontend/dev.log` | 40 K |
| A12 | `frontend/frontend_dev.log` | 36 K |

## B1 · backend/logs/ 保留 8/8~8/10 三天（42.5 MB）

- 删除：**85 个** mtime < 2026-08-08 00:00 的日志文件（完整名单见文末）
- 保留：57 个（`debug_2026-08-07.log` 至 `debug_2026-08-10.log` + 8/8 起的全部 `upgrade_*`、`restart-20260810-1645.log`）

## C 选项② · 删除 8/9 及以前的旧任务（378 MB + 空 demo 目录）

| 目录 | 大小 | 创建时间 |
|---|---|---|
| `task-20260808115317` | 1 MB | 08-08 11:53 |
| `task-20260808150051` | 1 MB | 08-08 15:00 |
| `task-20260808150157` | 1 MB | 08-08 15:01 |
| `task-20260808150158` | 1 MB | 08-08 15:01 |
| `task-20260808150639` | 1 MB | 08-08 15:06 |
| `task-20260808173451` | 3 MB | 08-08 17:34 |
| `task-20260808173501` | 5 MB | 08-08 17:35 |
| `task-20260808212317` | 18 MB | 08-09 00:38 |
| `task-20260809093843` | 1 MB | 08-09 09:38 |
| `task-20260809093852` | 2 MB | 08-09 09:38 |
| `task-20260809130245` | 0 MB | 08-09 13:02 |
| `task-20260809182314` | 2 MB | 08-09 18:23 |
| `task-20260809204710`（含 AI-Vtuber clone 163M + checkpoints.db 71M） | 340 MB | 08-09 21:03 |
| `task-20260809223109` | 1 MB | 08-09 22:31 |
| `task-test-bash` | 1 MB | 08-09 18:38 |
| `demo`（空目录） | — | 08-08 11:53 |

保留：8/10 的 9 个任务（`task-20260810*`，共 23 MB）。

## 明确保留（不在此次删除范围）

- `backend/.pi/`（活跃运行时）、`frontend/.electron-user-data/`（当前 Electron 在用）
- `backend/tasks/task-20260810*`（9 个）、`backend/task_platform.db`、`docs/.pi/`
- reference / vendor / models / node_modules / .venv 等

---

## 附：B1 将删除的 85 个日志文件完整名单

（由 find 按 mtime < 2026-08-08 生成，见下节）

## B1 删除名单（85 个，mtime < 2026-08-08）
- `backend/logs/debug_2026-08-04.log`
- `backend/logs/debug_2026-08-05.log`
- `backend/logs/debug_2026-08-06.2026-08-06_08-17-21_896914.log`
- `backend/logs/debug_2026-08-06.log`
- `backend/logs/debug_2026-08-07.2026-08-07_00-07-26_286223.log`
- `backend/logs/debug_2026-08-07.2026-08-07_09-21-48_157998.log`
- `backend/logs/upgrade_2026-08-04-16-43.log`
- `backend/logs/upgrade_2026-08-04-16-44.log`
- `backend/logs/upgrade_2026-08-04-18-21.log`
- `backend/logs/upgrade_2026-08-04-18-23.log`
- `backend/logs/upgrade_2026-08-04-19-05.log`
- `backend/logs/upgrade_2026-08-04-19-13.log`
- `backend/logs/upgrade_2026-08-04-20-50.log`
- `backend/logs/upgrade_2026-08-04-22-58.log`
- `backend/logs/upgrade_2026-08-04-23-00.log`
- `backend/logs/upgrade_2026-08-04-23-05.log`
- `backend/logs/upgrade_2026-08-04-23-15.log`
- `backend/logs/upgrade_2026-08-04-23-18.log`
- `backend/logs/upgrade_2026-08-04-23-22.log`
- `backend/logs/upgrade_2026-08-05-13-04.log`
- `backend/logs/upgrade_2026-08-05-14-20.log`
- `backend/logs/upgrade_2026-08-05-15-23.log`
- `backend/logs/upgrade_2026-08-05-15-30.log`
- `backend/logs/upgrade_2026-08-05-15-31.log`
- `backend/logs/upgrade_2026-08-05-15-52.log`
- `backend/logs/upgrade_2026-08-05-16-45.log`
- `backend/logs/upgrade_2026-08-05-16-58.log`
- `backend/logs/upgrade_2026-08-05-18-50.log`
- `backend/logs/upgrade_2026-08-05-19-21.log`
- `backend/logs/upgrade_2026-08-05-19-23.log`
- `backend/logs/upgrade_2026-08-05-19-36.log`
- `backend/logs/upgrade_2026-08-05-19-45.log`
- `backend/logs/upgrade_2026-08-05-20-01.log`
- `backend/logs/upgrade_2026-08-05-20-08.log`
- `backend/logs/upgrade_2026-08-06-08-17.log`
- `backend/logs/upgrade_2026-08-06-09-38.log`
- `backend/logs/upgrade_2026-08-06-09-42.log`
- `backend/logs/upgrade_2026-08-06-10-01.log`
- `backend/logs/upgrade_2026-08-06-10-32.log`
- `backend/logs/upgrade_2026-08-06-19-16.log`
- `backend/logs/upgrade_2026-08-06-19-24.log`
- `backend/logs/upgrade_2026-08-06-20-46.log`
- `backend/logs/upgrade_2026-08-06-23-27.log`
- `backend/logs/upgrade_2026-08-07-00-07.log`
- `backend/logs/upgrade_2026-08-07-00-11.log`
- `backend/logs/upgrade_2026-08-07-00-25.log`
- `backend/logs/upgrade_2026-08-07-08-19.log`
- `backend/logs/upgrade_2026-08-07-08-40.log`
- `backend/logs/upgrade_2026-08-07-09-03.log`
- `backend/logs/upgrade_2026-08-07-09-05.log`
- `backend/logs/upgrade_2026-08-07-09-21.log`
- `backend/logs/upgrade_2026-08-07-09-24.log`
- `backend/logs/upgrade_2026-08-07-10-20.log`
- `backend/logs/upgrade_2026-08-07-10-23.log`
- `backend/logs/upgrade_2026-08-07-11-00.log`
- `backend/logs/upgrade_2026-08-07-11-11.log`
- `backend/logs/upgrade_2026-08-07-11-14.log`
- `backend/logs/upgrade_2026-08-07-11-39.log`
- `backend/logs/upgrade_2026-08-07-11-42.log`
- `backend/logs/upgrade_2026-08-07-12-04.log`
- `backend/logs/upgrade_2026-08-07-12-15.log`
- `backend/logs/upgrade_2026-08-07-12-31.log`
- `backend/logs/upgrade_2026-08-07-12-32.log`
- `backend/logs/upgrade_2026-08-07-12-50.log`
- `backend/logs/upgrade_2026-08-07-12-53.log`
- `backend/logs/upgrade_2026-08-07-13-09.log`
- `backend/logs/upgrade_2026-08-07-13-39.log`
- `backend/logs/upgrade_2026-08-07-13-41.log`
- `backend/logs/upgrade_2026-08-07-14-16.log`
- `backend/logs/upgrade_2026-08-07-14-24.log`
- `backend/logs/upgrade_2026-08-07-15-32.log`
- `backend/logs/upgrade_2026-08-07-16-33.log`
- `backend/logs/upgrade_2026-08-07-17-32.log`
- `backend/logs/upgrade_2026-08-07-19-01.log`
- `backend/logs/upgrade_2026-08-07-19-38.log`
- `backend/logs/upgrade_2026-08-07-19-51.log`
- `backend/logs/upgrade_2026-08-07-20-30.log`
- `backend/logs/upgrade_2026-08-07-20-35.log`
- `backend/logs/upgrade_2026-08-07-20-46.log`
- `backend/logs/upgrade_2026-08-07-20-50.log`
- `backend/logs/upgrade_2026-08-07-20-56.log`
- `backend/logs/upgrade_2026-08-07-21-35.log`
- `backend/logs/upgrade_2026-08-07-22-26.log`
- `backend/logs/upgrade_2026-08-07-22-38.log`
- `backend/logs/voicevox_engine.log`

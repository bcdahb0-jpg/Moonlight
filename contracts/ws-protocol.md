# Moonlight 通信契约（WebSocket 协议）

> **单一事实源说明**：本文件定义前后端之间的 WebSocket 消息协议。后端实现位于
> `backend/src/open_llm_vtuber/contracts.py`（pydantic 模型 + `send_message` 统一出站入口），
> 前端类型位于 `frontend/src/types/ws.ts`（discriminated union）。任何消息字段变更
> 必须同步修改这三处。

## 传输层

- 端点：`ws://127.0.0.1:12393/client-ws?uid=<client_uid>`
- 帧格式：JSON 文本帧。所有消息必须携带 `type` 字段（字符串）。
- 心跳：客户端每 25s 发送 `{"type":"heartbeat"}`，服务端回 `{"type":"heartbeat-ack"}`。
- 校验：服务端出站消息经 pydantic schema 校验；入站消息经 `validate_client_message`
  白名单校验，非法消息回 `error`（code=`INVALID_MESSAGE`）且不中断连接。

## 错误码

见 [`error-codes.json`](./error-codes.json)。所有 `error` 消息携带结构化 `code`：

```json
{ "type": "error", "code": "LLM_UNREACHABLE", "message": "无法连接对话模型，请检查 LLM 配置或网络" }
```

前端不得依赖 `message` 文案（可本地化），应依赖 `code` 做分支处理。

---

## Server → Client（出站）

| type | 必填字段 | 可选字段 | 说明 |
|---|---|---|---|
| `set-model-and-conf` | `model_info`, `conf_name`, `conf_uid`, `client_uid` | — | 连接建立 / 切角色后下发 Live2D 模型与角色信息 |
| `full-text` | `text` | `quote: bool` | 纯文本回复；`quote=true` 表示预设台词 |
| `audio` | `audio`(可 null), `volumes`, `slice_length` | `display_text`, `subtitle_text`, `actions`, `forwarded`, `emotion` | 语音+口型数据；`audio=null` 为静默展示 |
| `transcript` | `text` | — | 流式字幕 |
| `user-input-transcription` | `text` | — | 用户语音输入的识别文本 |
| `affection-update` | `affection` | `milestone` | 好感度更新，`milestone` 为升阶引导句 |
| `control` | `text` | — | 控制信号，见下方 `ControlText` |
| `backend-synth-complete` | — | — | 后端合成结束（前端据此判定播放完成） |
| `force-new-message` | — | — | 强制结束当前消息（流式中断/换轮） |
| `error` | `code`, `message` | `recover` | 结构化错误，`code` 见错误码表 |
| `history-list` | `histories` | — | 历史会话列表 |
| `new-history-created` | `history_uid` | — | 新会话创建成功 |
| `history-deleted` | `success`, `history_uid` | — | 会话删除结果 |
| `history-workspace-updated` | `success`, `history_uid`, `workspace` | — | 会话工作目录更新结果 |
| `histories-cleared` | `removed` | — | 当前角色历史会话清理结果 |
| `history-data` | `messages` | — | 单个会话的消息数据 |
| `config-files` | `configs` | — | 可用角色配置列表 |
| `background-files` | `files` | — | 可用背景图列表 |
| `group-update` | `members`, `is_owner` | — | 群聊成员变更 |
| `heartbeat-ack` | — | — | 心跳应答 |
| `config-updated` | — | — | 配置已热重载（PUT /api/config 成功后广播） |
| `config-switched` | `message` | — | 角色配置切换完成 |
| `screen-status` | `enabled`, `capturing` | `last_capture_at`, `last_analyze_at`, `last_window_title`, `last_window_app`, `last_scene`, `last_summary`, `pause_reason`, `pending_frames`, `frames_captured`, `frames_deduped`, `analyze_count`, `last_error` | 屏幕感知运行时状态（设置页/状态灯；收到 `screen-frame`/`screen-enable`/`screen-clear` 后回推） |
| `screen-context` | `snapshot?`, `frame_available`, `reason` | — | 屏幕上下文就绪通知（前端显示「正在看屏幕」） |

### ControlText（`control.text` 字面量）

- `start-mic` — 请求客户端开启麦克风
- `mic-audio-end` — 服务端检测到语音结束
- `interrupt` — 打断当前播报
- `conversation-chain-start` — 会话链开始（前端置 thinking）
- `conversation-chain-end` — 会话链结束

---

## Client → Server（入站）

| type | 必填字段 | 说明 |
|---|---|---|
| `text-input` | `text` | 文字输入；可选 `workspace`（当前会话工作目录，Phase 1 pet-ptt-workflow：透传给 auto-create） |
| `mic-audio-data` | `audio: number[]` | 麦克风 PCM(float32) 分块 |
| `mic-audio-end` | — | 语音输入结束；可选 `workspace`（同上，Phase 1） |
| `raw-audio-data` | `audio: number[]` | 原始音频（VAD 处理） |
| `interrupt-signal` | `text?` | 打断信号，可带已听到文本 |
| `ai-speak-signal` | — | 主动发言请求 |
| `interact` | `zone?` | 养成交互（click/head/body） |
| `audio-play-start` | `display_text?` | 前端开始播放（用于群聊转发） |
| `frontend-playback-complete` | — | 前端播放完成（解除后端等待） |
| `fetch-history-list` | — | 拉取历史会话列表 |
| `fetch-and-set-history` | `history_uid` | 加载指定会话 |
| `create-new-history` | `workspace` | 新建会话（v5：必须绑定工作目录；`workspace` 为绝对路径） |
| `delete-history` | `history_uid` | 删除会话 |
| `fetch-configs` | — | 拉取角色配置列表 |
| `switch-config` | `file` | 切换角色配置 |
| `fetch-backgrounds` | — | 拉取背景图列表 |
| `request-init-config` | — | 请求初始化配置（重连后） |
| `heartbeat` | — | 心跳 |
| `screen-frame` | `frame_id`, `captured_at`, `window` | 屏幕帧上传（独立协议，见下方 §屏幕帧协议）。服务端去重/单飞分析后回 `screen-status` |
| `screen-enable` | — | 启用/停用屏幕感知：`{enabled: bool, reason?: string}`；关闭即清空内存上下文 |
| `screen-clear` | — | 即时清除该客户端内存上下文（图像+摘要+窗口身份） |

### 屏幕帧协议（`screen-frame` 入站）

上传帧**不写进普通聊天消息**（独立协议），服务端只保留短时内存上下文：

```json
{
  "type": "screen-frame",
  "frame_id": "uuid",
  "captured_at": 1710000000,
  "window": { "title": "VS Code", "app": "Code", "pid": 1234, "bounds": [0, 0, 1920, 1080] },
  "image": "data:image/webp;base64,...",
  "image_hash": "sha256-ish",
  "reason": "window_changed|content_changed|user_requested"
}
```

服务端行为：隐私二次校验（阻断时 `0` 张图像离开本机）→ 窗口身份+图像 hash 双重去重 →
单飞视觉分析（同客户端 ≤1 并发，in-flight 期间新帧丢弃）→ 摘要 TTL 过期/窗口切换即失效。
原始图像仅存内存 ≤30s，不落盘、不写日志标题全文、不进四层记忆。
| `add-client-to-group` / `remove-client-from-group` | — | 群聊管理 |
| `request-group-info` | — | 群聊信息 |

---

## 演进规则

1. **新增消息类型**：三处同步（本文件 / contracts.py / types/ws.ts），后端必须加对应
   pydantic 模型，前端必须加联合成员——TS 编译器强制处理所有分支。
2. **字段变更**：出站模型序列化时保留 `null` 字段（与旧协议逐字节兼容），前端不得
   假设字段必填（统一用 `??` 兜底）。
3. **错误必须结构化**：禁止直接 `{"type":"error","message":"<异常字符串>"}`，一律
   `send_error(send_text, ErrorCode.X, message)`。

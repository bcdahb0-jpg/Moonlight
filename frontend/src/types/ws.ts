/**
 * WebSocket protocol types — 契约层（Phase 0）。
 *
 * 单一事实源：contracts/ws-protocol.md + contracts/error-codes.json。
 * 本文件与其保持一致；后端 pydantic 模型见 backend/src/open_llm_vtuber/contracts.py。
 * 任何消息字段变更必须三处同步。
 */

// ------------------------------------------------------------------ //
// 错误码（与 contracts/error-codes.json 保持一致）
// ------------------------------------------------------------------ //

export type ErrorCode =
  | 'UNKNOWN'
  | 'INVALID_MESSAGE'
  | 'PROTOCOL_ERROR'
  | 'ASR_MODEL_MISSING'
  | 'ASR_LOAD_FAILED'
  | 'ASR_TRANSCRIBE_FAILED'
  | 'LLM_UNREACHABLE'
  | 'LLM_INVALID_KEY'
  | 'LLM_TIMEOUT'
  | 'TTS_FAILED'
  | 'VAD_FAILED'
  | 'MIC_PERMISSION_DENIED'
  | 'MODEL_NOT_FOUND'
  | 'CONFIG_INVALID'
  | 'MCP_SERVER_FAILED'
  | 'MEMORY_FAILED'
  | 'HISTORY_FAILED'
  | 'TRANSLATE_FAILED'
  | 'INTERNAL_ERROR';

/** 可选的修复提示（Phase 3 渲染成可操作修复卡）。 */
export interface RecoveryHint {
  action: string;
  target?: string;
}

export interface DisplayText {
  text: string;
  name?: string | null;
  avatar?: string | null;
}

export interface Actions {
  expressions?: Array<string | number> | null;
  pictures?: string[] | null;
  sounds?: string[] | null;
}

// ------------------------------------------------------------------ //
// 出站消息（Client → Server）
// ------------------------------------------------------------------ //

export type ClientMessageType =
  | 'text-input'
  | 'mic-audio-data'
  | 'mic-audio-end'
  | 'raw-audio-data'
  | 'interrupt-signal'
  | 'ai-speak-signal'
  | 'interact'
  | 'audio-play-start'
  | 'frontend-playback-complete'
  | 'fetch-history-list'
  | 'fetch-and-set-history'
  | 'create-new-history'
  | 'delete-history'
  | 'set-history-title'
  | 'set-history-workspace'
  | 'clear-all-histories'
  | 'fetch-configs'
  | 'switch-config'
  | 'fetch-backgrounds'
  | 'request-init-config'
  | 'heartbeat'
  | 'add-client-to-group'
  | 'remove-client-from-group'
  | 'request-group-info'
  // screen_awareness（Phase 0）：独立协议，不混入普通聊天消息。
  | 'screen-frame'
  | 'screen-enable'
  | 'screen-clear';

/** 前台窗口身份（与后端 ScreenWindowInfo 对齐）。 */
export interface ScreenWindowInfo {
  title: string;
  app: string;
  pid?: number;
  /** [x, y, width, height] 物理像素。 */
  bounds?: number[];
}

/** 屏幕帧（screen-frame 入站，独立协议）。 */
export interface ScreenFramePayload {
  type: 'screen-frame';
  frame_id: string;
  captured_at: number;
  window: ScreenWindowInfo;
  image: string;
  image_hash: string;
  reason: 'window_changed' | 'content_changed' | 'user_requested';
}

/** 屏幕感知启用/停用（screen-enable 入站）。 */
export interface ScreenEnablePayload {
  type: 'screen-enable';
  enabled: boolean;
  reason?: string;
}

/** 清除屏幕上下文（screen-clear 入站）。 */
export interface ScreenClearPayload {
  type: 'screen-clear';
}

/** Outgoing message shape (from the frontend to the backend). */
export interface ClientMessage {
  type: ClientMessageType;
  action?: string;
  text?: string;
  audio?: number[];
  images?: string[];
  history_uid?: string;
  title?: string;
  /** v5：会话绑定/移动的工作目录（绝对路径）。 */
  workspace?: string;
  file?: string;
  display_text?: DisplayText;
  request_id?: string;
  /** 养成交互：点击/摸头等互动区域（backend websocket_handler "interact"）。 */
  zone?: string;
  // screen_awareness：screen-frame 载荷（类型收窄见 ScreenFramePayload）。
  frame_id?: string;
  captured_at?: number;
  window?: ScreenWindowInfo;
  image?: string;
  image_hash?: string;
  reason?: string;
  /** screen-enable 的启用标志。 */
  enabled?: boolean;
}

// ------------------------------------------------------------------ //
// 入站消息 union（Server → Client）
// ------------------------------------------------------------------ //

export interface AudioMessage {
  type: 'audio';
  audio: string | null;
  volumes: number[];
  slice_length: number;
  /**
   * 音素级口型数据（可选增强）：每 slice 一个 [a,i,u,e,o] 概率向量，
   * 与 volumes 同粒度。后端 F1/F2 共振峰分析产出；缺省时前端回退 RMS 口型。
   */
  visemes?: number[][] | null;
  /**
   * 情绪强度/时长元数据（Phase 1 面部表情）：
   * {emotion, intensity, duration_ms, source}。emotion 为分类结果 token，
   * 在 actions.expressions 为空时作为表情兜底源（比规则 msg.emotion 更准）。
   */
  emotion_meta?: {
    emotion?: string | null;
    intensity?: number;
    duration_ms?: number;
    source?: string;
  } | null;
  display_text?: DisplayText | null;
  subtitle_text?: string | null;
  actions?: Actions | null;
  forwarded?: boolean;
  emotion?: string | null;
}

export interface FullTextMessage {
  type: 'full-text';
  text: string;
  /** 预设台词（关键词触发/点击互动），不进入记忆流。 */
  quote?: boolean;
}

export interface TranscriptMessage {
  type: 'transcript';
  text: string;
}

export interface UserInputTranscriptionMessage {
  type: 'user-input-transcription';
  text: string;
}

/** control 信号字面量（与后端 contracts.py ControlMessage 同步）。 */
export type ControlText =
  | 'start-mic'
  | 'mic-audio-end'
  | 'interrupt'
  | 'conversation-chain-start'
  | 'conversation-chain-end';

export interface ControlMessage {
  type: 'control';
  text: ControlText;
}

export interface BackendSynthCompleteMessage {
  type: 'backend-synth-complete';
}

export interface ForceNewMessage {
  type: 'force-new-message';
}

export interface ErrorMessage {
  type: 'error';
  code: ErrorCode;
  message: string;
  recover?: RecoveryHint | null;
}

export interface ModelInfo {
  name: string;
  url: string;
  kScale?: number;
  initialXshift?: number;
  initialYshift?: number;
  kXOffset?: number;
  idleMotionGroupName?: string;
  emotionMap?: Record<string, number>;
  tapMotions?: Record<string, Record<string, number>>;
}

export interface SetModelAndConfMessage {
  type: 'set-model-and-conf';
  model_info: ModelInfo;
  conf_name: string;
  character_name?: string | null;
  conf_uid: string;
  client_uid: string;
}

export interface HistoryListMessage {
  type: 'history-list';
  histories: Array<Record<string, unknown>>;
}

export interface HistoryDataMessage {
  type: 'history-data';
  /** Moonlight（2026-08-10 修复）：加载的会话 uid，前端同步 currentHistoryUid 用。 */
  history_uid?: string | null;
  messages: Array<{
    role: string;
    content: string;
    name?: string;
    avatar?: string;
    /** 2026-08-10：消息类别（"task_brief" 等，后端 chat_history kind 字段）。 */
    kind?: string;
    /** 2026-08-10：简报归属任务 id（后端 task_id 字段）。 */
    task_id?: string;
  }>;
}

export interface NewHistoryCreatedMessage {
  type: 'new-history-created';
  history_uid: string;
  /** v5：会话绑定的工作目录。 */
  workspace?: string;
  /** true = 后端在对话中途自动创建的会话（首次真人消息），勿清空当前聊天区；
   *  false / 缺省 = 用户手动新建会话，清空聊天区。 */
  auto?: boolean;
}

export interface HistoryWorkspaceUpdatedMessage {
  type: 'history-workspace-updated';
  success: boolean;
  history_uid: string;
  workspace: string;
}

export interface HistoriesClearedMessage {
  type: 'histories-cleared';
  removed: number;
}

export interface HistoryDeletedMessage {
  type: 'history-deleted';
  success: boolean;
  history_uid: string | null;
}

export interface HistoryTitleUpdatedMessage {
  type: 'history-title-updated';
  success: boolean;
  history_uid: string;
  title: string;
}

export interface ConfigFilesMessage {
  type: 'config-files';
  configs: string[];
}

export interface GroupUpdateMessage {
  type: 'group-update';
  members: string[];
  is_owner: boolean;
}

export interface ConfigSwitchedMessage {
  type: 'config-switched';
  [key: string]: unknown;
}

export interface HeartbeatAckMessage {
  type: 'heartbeat-ack';
}

/** 配置已热重载（Phase 1：PUT /api/config 成功后广播）。 */
export interface ConfigUpdatedMessage {
  type: 'config-updated';
}

/** 好感度摘要（后端 affection.py 的 affection_summary 输出）。 */
export interface AffectionSummary {
  value: number;
  max: number;
  tier: string;
  description: string;
  next: { name: string; threshold: number } | null;
}

export interface AffectionUpdateMessage {
  type: 'affection-update';
  affection: AffectionSummary;
  /** 刚升阶时的角色引导句（可能缺失）。 */
  milestone?: string;
}

/** 聊天 agent 工具执行状态（backend basic_memory_agent 的 tool_call_status 事件）。
 *  text 非空 = 工具执行中（显示状态条）；空串 = 结束/清除。 */
export interface ToolCallStatusMessage {
  type: 'tool_call_status';
  text: string;
  name?: string;
}

/** P5.1 意图副模型出站：勿扰/闲聊/任务 + 情绪（ChatInput 状态条实时显示）。 */
export interface IntentEventMessage {
  type: 'intent-event';
  intent: 'silence' | 'chat' | 'task' | string;
  emotion: string;
  source: string;
  text?: string;
}

/** delegate 任务完整结果直达聊天区（backend single_conversation 的 task_result 事件）。
 *  text 为完整清单/表格，前端直接渲染为 AI 气泡，不等 LLM 逐句复述。 */
export interface TaskResultMessage {
  type: 'task-result';
  text: string;
  name?: string;
  avatar?: string;
  task_id?: string | null;
}

/** 屏幕感知运行时状态（screen_awareness，Phase 0）。 */
export interface ScreenStatusMessage {
  type: 'screen-status';
  enabled: boolean;
  capturing: boolean;
  last_capture_at?: number | null;
  last_analyze_at?: number | null;
  last_window_title?: string;
  last_window_app?: string;
  last_scene?: string;
  last_summary?: string;
  pause_reason?: string;
  pending_frames?: number;
  frames_captured?: number;
  frames_deduped?: number;
  analyze_count?: number;
  last_error?: string;
}

/** 屏幕上下文就绪通知（前端显示「正在看屏幕」）。 */
export interface ScreenContextMessage {
  type: 'screen-context';
  snapshot?: Record<string, unknown> | null;
  frame_available: boolean;
  reason: string;
}

export type ServerMessage =
  | AudioMessage
  | FullTextMessage
  | TranscriptMessage
  | UserInputTranscriptionMessage
  | ControlMessage
  | BackendSynthCompleteMessage
  | ForceNewMessage
  | ErrorMessage
  | SetModelAndConfMessage
  | HistoryListMessage
  | HistoryDataMessage
  | NewHistoryCreatedMessage
  | HistoryDeletedMessage
  | HistoryTitleUpdatedMessage
  | HistoryWorkspaceUpdatedMessage
  | HistoriesClearedMessage
  | ConfigFilesMessage
  | GroupUpdateMessage
  | ConfigSwitchedMessage
  | HeartbeatAckMessage
  | ConfigUpdatedMessage
  | AffectionUpdateMessage
  | ToolCallStatusMessage
  | IntentEventMessage
  | TaskResultMessage
  | ScreenStatusMessage
  | ScreenContextMessage;

/**
 * 运行期守卫：只校验「是带 type 字段的对象」。
 * 具体类型的强校验由后端 contracts.py 负责；TS 联合类型在编译期收窄。
 */
export function isServerMessage(raw: unknown): raw is ServerMessage {
  return typeof raw === 'object' && raw !== null && 'type' in raw;
}

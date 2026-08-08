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
  | 'fetch-configs'
  | 'switch-config'
  | 'fetch-backgrounds'
  | 'request-init-config'
  | 'heartbeat'
  | 'add-client-to-group'
  | 'remove-client-from-group'
  | 'request-group-info';

/** Outgoing message shape (from the frontend to the backend). */
export interface ClientMessage {
  type: ClientMessageType;
  action?: string;
  text?: string;
  audio?: number[];
  images?: string[];
  history_uid?: string;
  title?: string;
  file?: string;
  display_text?: DisplayText;
  request_id?: string;
  /** 养成交互：点击/摸头等互动区域（backend websocket_handler "interact"）。 */
  zone?: string;
}

// ------------------------------------------------------------------ //
// 入站消息 union（Server → Client）
// ------------------------------------------------------------------ //

export interface AudioMessage {
  type: 'audio';
  audio: string | null;
  volumes: number[];
  slice_length: number;
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
  conf_uid: string;
  client_uid: string;
}

export interface HistoryListMessage {
  type: 'history-list';
  histories: Array<Record<string, unknown>>;
}

export interface HistoryDataMessage {
  type: 'history-data';
  messages: Array<{ role: string; content: string; name?: string; avatar?: string }>;
}

export interface NewHistoryCreatedMessage {
  type: 'new-history-created';
  history_uid: string;
  /** true = 后端在对话中途自动创建的会话（首次真人消息），勿清空当前聊天区；
   *  false / 缺省 = 用户手动新建会话，清空聊天区。 */
  auto?: boolean;
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
  | ConfigFilesMessage
  | GroupUpdateMessage
  | ConfigSwitchedMessage
  | HeartbeatAckMessage
  | ConfigUpdatedMessage
  | AffectionUpdateMessage;

/**
 * 运行期守卫：只校验「是带 type 字段的对象」。
 * 具体类型的强校验由后端 contracts.py 负责；TS 联合类型在编译期收窄。
 */
export function isServerMessage(raw: unknown): raw is ServerMessage {
  return typeof raw === 'object' && raw !== null && 'type' in raw;
}

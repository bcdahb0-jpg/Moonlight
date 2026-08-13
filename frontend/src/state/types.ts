import type { AffectionSummary, ErrorCode, ModelInfo, ScreenStatusMessage } from '@/types/ws';

export type ViewMode = 'pet' | 'window';
export type ThemeMode = 'light' | 'dark';
export type ConnStatus = 'idle' | 'connecting' | 'connected' | 'disconnected';

/** Backend emotion keys (emotion_analyzer 的 28+1 情绪集 + Live2D emotionMap 词汇). */
export type Emotion =
  | 'neutral'
  | 'joy'
  | 'amusement'
  | 'affection'
  | 'surprise'
  | 'confusion'
  | 'sad'
  | 'anger'
  | 'fear'
  | 'gratitude'
  | 'admiration'
  | 'annoyance'
  | 'approval'
  | 'caring'
  | 'curiosity'
  | 'desire'
  | 'disappointment'
  | 'disapproval'
  | 'disgust'
  | 'embarrassment'
  | 'excitement'
  | 'grief'
  | 'love'
  | 'nervousness'
  | 'optimism'
  | 'pride'
  | 'realization'
  | 'relief'
  | 'remorse'
  | 'angry'
  | 'smirk';

export type MessageRole = 'user' | 'ai' | 'system';

/**
 * 气泡「再次播放」所需音频数据（2026-08-10）：后端 TTS wav 播放完即删除
 * （tts_manager finally 里 remove_file），前端在收到 audio 消息时把 base64
 * 等存到消息对象，供气泡重播按钮使用。会话内内存保存，刷新即失效。
 */
export interface AudioReplayData {
  base64: string;
  volumes: number[];
  visemes?: number[][] | null;
  sliceLengthMs: number;
  expression?: Array<string | number> | null;
}

export interface ChatMessage {
  id: string;
  role: MessageRole;
  text: string;
  name?: string;
  avatar?: string;
  timestamp: number;
  streaming?: boolean;
  /** v6：该 AI 气泡是否已绑定语音（audio 消息到达后置位）。
   *  流式文本先上屏（full-text 逐句），语音合成好后绑定到气泡播放，
   *  用于避免「同一段文本重复建气泡」。 */
  audioBound?: boolean;
  /** 有语音时保存的音频数据（气泡显示「再次播放」按钮，点击重播）。 */
  audioData?: AudioReplayData;
  /** 显示层字幕翻译（如日文）；仅显示，不进记忆/历史。 */
  subtitle?: string;
  /** 2026-08-10：消息类别（"task_brief" = 任务简报 → 折叠卡片渲染；None = 普通气泡）。
   *  后端 chat_history 存储的 kind 字段透传，旧数据无此字段时前端按内容前缀兜底。 */
  kind?: string;
  /** 2026-08-10：简报归属任务 id（后端 task_id 透传）。 */
  taskId?: string;
}

/** Local-only settings persisted to localStorage. */
export interface LocalSettings {
  theme: ThemeMode;
  screenAwareEnabled: boolean;
  screenPollIntervalSec: number;
  proactiveEnabled: boolean;
  proactiveIdleSec: number;
  autoSpeakOnIdle: boolean;
  /** UX 修复（2026-08-10）：主动找话题仅限桌宠模式触发（窗口模式不打扰）。 */
  proactivePetModeOnly: boolean;
  /** UX 修复（2026-08-10）：双语气泡（字幕翻译）前端渲染闸门，默认关。 */
  subtitleEnabled: boolean;
  // screen_awareness（Phase 1）：隐私黑名单（与后端 conf.yaml 白名单联动）。
  /** 应用黑名单（进程名，小写匹配；命中即不采集）。 */
  screenBlockedApps: string[];
  /** 标题关键词黑名单（小写子串匹配；命中即不采集）。 */
  screenBlockedTitleKeywords: string[];
  /** 画面变化阈值（pHash 归一化差异，0-1；越小越敏感，默认 0.08）。 */
  screenChangeThreshold: number;
  /** 系统空闲多少秒进入「仅监听窗口变更」模式（默认 15）。 */
  screenIdleThresholdSec: number;
  /** Phase 5：仅用户询问时识别（不自动轮询/不主动分析；问「看看屏幕」才采集）。 */
  screenCaptureOnDemand: boolean;
  /** Phase 2（pet-ptt-workflow）：定时屏幕巡检间隔（秒）。0=关闭；300~3600 可调。
   *  与 proactiveIdleSec（空闲主动）相互独立：本开关按固定周期 + 新快照去重触发。 */
  screenProactiveIntervalSec: number;
}

export const DEFAULT_SETTINGS: LocalSettings = {
  theme: 'light',
  // 卖点默认开启（Phase 1）：屏幕感知 = 了解你在用什么应用，主动话题 = 她会偶尔主动搭话。
  // 隐私敏感：屏幕感知会读取当前活动窗口标题；介意可在设置中关闭。
  screenAwareEnabled: false,
  screenPollIntervalSec: 5,
  proactiveEnabled: true,
  proactiveIdleSec: 60,
  autoSpeakOnIdle: true,
  // UX 修复：主动话题仅限桌宠模式（窗口模式等待任务时不被打扰）
  proactivePetModeOnly: true,
  // UX 修复：双语气泡默认关闭（与后端 translate_subtitle 默认一致）
  subtitleEnabled: false,
  // screen_awareness：默认无用户黑名单；阈值用后端默认值。
  screenBlockedApps: [],
  screenBlockedTitleKeywords: [],
  screenChangeThreshold: 0.08,
  screenIdleThresholdSec: 15,
  screenCaptureOnDemand: false,
  // pet-ptt-workflow：定时巡检默认关闭（避免静态画面重复打扰）。
  screenProactiveIntervalSec: 0,
};

export interface ActiveWindowInfo {
  title: string;
  app: string;
  idleTime: number;
  capturedAt: number;
}

export interface HistoryEntry {
  uid?: string;
  history_uid?: string;
  /** v5：会话绑定的工作目录（绝对路径）；旧会话可能缺失。 */
  workspace?: string;
  [key: string]: unknown;
}

export interface AppState {
  mode: ViewMode;
  theme: ThemeMode;
  connStatus: ConnStatus;
  modelUrl: string | null;
  modelInfo: ModelInfo | null;
  confName: string;
  /** 当前角色卡的展示名；confName 仅作为兼容回退。 */
  characterName: string;
  confUid: string;
  clientUid: string;
  messages: ChatMessage[];
  emotion: Emotion;
  /** 情绪强度 0..1（emotion_meta.intensity，Phase 1.5 诊断用）。 */
  emotionIntensity: number | null;
  /** 情绪来源：'rule' | 'llm' | null（emotion_meta.source）。 */
  emotionSource: string | null;
  affection: AffectionSummary | null;
  isThinking: boolean;
  /** Live streaming subtitle from the audio payload. */
  subtitle: string;
  /** Phase 3（pet-ptt-workflow）：桌宠字幕条（最近一条流式 AI full-text）。
   *  独立于 subtitle（翻译副文本）；会话结束/打断/切换时清空。 */
  petSubtitle: string;
  /** 聊天 agent 工具执行状态文案（tool_call_status；空 = 无/已结束）。 */
  toolStatus: string | null;
  activeWindow: ActiveWindowInfo | null;
  /** screen_awareness 运行时状态（后端 screen-status 推送）。 */
  screenStatus: ScreenStatusMessage | null;
  settings: LocalSettings;
  lastError: string | null;
  /** 结构化错误码（契约层 Phase 0，Phase 3 渲染成修复卡）。 */
  errorCode: ErrorCode | null;
  historyList: HistoryEntry[];
  currentHistoryUid: string | null;
}

export function createInitialState(): AppState {
  return {
    mode: 'pet',
    theme: 'light',
    connStatus: 'idle',
    modelUrl: null,
    modelInfo: null,
  confName: '',
  characterName: '',
    confUid: '',
    clientUid: '',
    messages: [],
    emotion: 'neutral',
    emotionIntensity: null,
    emotionSource: null,
    affection: null,
    isThinking: false,
    subtitle: '',
    petSubtitle: '',
    toolStatus: null,
    activeWindow: null,
    screenStatus: null,
    settings: DEFAULT_SETTINGS,
    lastError: null,
    errorCode: null,
    historyList: [],
    currentHistoryUid: null,
  };
}

import type { AffectionSummary, ErrorCode, ModelInfo } from '@/types/ws';

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

export interface ChatMessage {
  id: string;
  role: MessageRole;
  text: string;
  name?: string;
  avatar?: string;
  timestamp: number;
  streaming?: boolean;
  /** 显示层字幕翻译（如日文）；仅显示，不进记忆/历史。 */
  subtitle?: string;
}

/** Local-only settings persisted to localStorage. */
export interface LocalSettings {
  theme: ThemeMode;
  screenAwareEnabled: boolean;
  screenPollIntervalSec: number;
  proactiveEnabled: boolean;
  proactiveIdleSec: number;
  autoSpeakOnIdle: boolean;
}

export const DEFAULT_SETTINGS: LocalSettings = {
  theme: 'dark',
  // 卖点默认开启（Phase 1）：屏幕感知 = 了解你在用什么应用，主动话题 = 她会偶尔主动搭话。
  // 隐私敏感：屏幕感知会读取当前活动窗口标题；介意可在设置中关闭。
  screenAwareEnabled: true,
  screenPollIntervalSec: 5,
  proactiveEnabled: true,
  proactiveIdleSec: 60,
  autoSpeakOnIdle: true,
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
  [key: string]: unknown;
}

export interface AppState {
  mode: ViewMode;
  theme: ThemeMode;
  connStatus: ConnStatus;
  modelUrl: string | null;
  modelInfo: ModelInfo | null;
  confName: string;
  confUid: string;
  clientUid: string;
  messages: ChatMessage[];
  emotion: Emotion;
  affection: AffectionSummary | null;
  isThinking: boolean;
  /** Live streaming subtitle from the audio payload. */
  subtitle: string;
  activeWindow: ActiveWindowInfo | null;
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
    theme: 'dark',
    connStatus: 'idle',
    modelUrl: null,
    modelInfo: null,
    confName: '',
    confUid: '',
    clientUid: '',
    messages: [],
    emotion: 'neutral',
    affection: null,
    isThinking: false,
    subtitle: '',
    activeWindow: null,
    settings: DEFAULT_SETTINGS,
    lastError: null,
    errorCode: null,
    historyList: [],
    currentHistoryUid: null,
  };
}

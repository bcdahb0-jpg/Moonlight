import type { AppState, AudioReplayData, ChatMessage, Emotion, LocalSettings, ViewMode } from './types';
import type { AffectionSummary, ErrorCode } from '@/types/ws';
import type { ModelInfo } from '@/types/ws';

export type Action =
  | { type: 'SET_MODE'; mode: ViewMode }
  | { type: 'SET_THEME'; theme: 'light' | 'dark' }
  | { type: 'SET_CONN_STATUS'; status: AppState['connStatus'] }
  | {
      type: 'SET_MODEL';
      modelUrl: string;
      modelInfo: ModelInfo;
      confName: string;
      confUid: string;
    }
  | { type: 'ADD_MESSAGE'; message: ChatMessage }
  | { type: 'APPEND_MESSAGE_TEXT'; id: string; text: string }
  | { type: 'UPDATE_MESSAGE_TEXT'; id: string; text: string; streaming?: boolean }
  | { type: 'FINALIZE_MESSAGE'; id: string; streaming?: boolean }
  /** v6：语音到达，绑定到该 AI 气泡（streaming 结束 + audioBound 置位）。
   *  语音合成是异步的，文本先流式上屏；audio 消息到达后按序绑定播放。
   *  subtitle：audio 消息带的段落级字幕翻译，绑定后补到气泡（原文流式时无字幕）。
   *  audio：语音数据快照（2026-08-10），气泡「再次播放」用（后端 wav 播完即删）。 */
  | { type: 'BIND_AUDIO_TO_MESSAGE'; id: string; subtitle?: string; audio?: AudioReplayData }
  | { type: 'SET_THINKING'; thinking: boolean }
  | { type: 'SET_TOOL_STATUS'; text: string | null }
  | { type: 'SET_EMOTION'; emotion: Emotion; intensity?: number | null; source?: string | null }
  | { type: 'SET_AFFECTION'; affection: AffectionSummary | null }
  | { type: 'SET_SUBTITLE'; text: string }
  | { type: 'SET_ACTIVE_WINDOW'; info: AppState['activeWindow'] }
  | { type: 'UPDATE_SETTINGS'; settings: Partial<LocalSettings> }
  | { type: 'SET_ERROR'; message: string | null; code?: ErrorCode | null }
  | { type: 'CLEAR_MESSAGES' }
  | { type: 'SET_HISTORY_LIST'; historyList: AppState['historyList'] }
  | { type: 'SET_HISTORY_UID'; uid: string | null };

export function reducer(state: AppState, action: Action): AppState {
  switch (action.type) {
    case 'SET_MODE':
      return { ...state, mode: action.mode };

    case 'SET_THEME':
      return { ...state, theme: action.theme, settings: { ...state.settings, theme: action.theme } };

    case 'SET_CONN_STATUS':
      return { ...state, connStatus: action.status };

    case 'SET_MODEL':
      return {
        ...state,
        modelUrl: action.modelUrl,
        modelInfo: action.modelInfo,
        confName: action.confName,
        confUid: action.confUid,
      };

    case 'ADD_MESSAGE': {
      const messages = [...state.messages, action.message];
      // Audio replay data is large (base64 WAV). Keep a small recent window in
      // React state so long sessions do not retain hundreds of megabytes and
      // trigger increasingly expensive reducer copies/GC cycles.
      const audioIndexes = messages
        .map((m, i) => (m.audioData ? i : -1))
        .filter((i) => i >= 0);
      if (audioIndexes.length > 12) {
        const drop = new Set(audioIndexes.slice(0, audioIndexes.length - 12));
        return {
          ...state,
          messages: messages.map((m, i) => (drop.has(i) ? { ...m, audioData: undefined } : m)),
        };
      }
      return { ...state, messages };
    }

    case 'APPEND_MESSAGE_TEXT':
      return {
        ...state,
        messages: state.messages.map((m) =>
          m.id === action.id ? { ...m, text: m.text + action.text } : m,
        ),
      };

    case 'UPDATE_MESSAGE_TEXT':
      return {
        ...state,
        messages: state.messages.map((m) =>
          m.id === action.id
            ? { ...m, text: action.text, streaming: action.streaming ?? false }
            : m,
        ),
      };

    case 'FINALIZE_MESSAGE':
      return {
        ...state,
        messages: state.messages.map((m) =>
          m.id === action.id ? { ...m, streaming: false } : m,
        ),
      };

    case 'BIND_AUDIO_TO_MESSAGE':
      {
        const messages = state.messages.map((m) =>
          m.id === action.id
            ? {
                ...m,
                streaming: false,
                audioBound: true,
                subtitle: action.subtitle ?? m.subtitle,
                audioData: action.audio ?? m.audioData,
            }
            : m,
        );
        const audioIndexes = messages
          .map((m, i) => (m.audioData ? i : -1))
          .filter((i) => i >= 0);
        const drop = new Set(audioIndexes.slice(0, Math.max(0, audioIndexes.length - 12)));
        return {
          ...state,
          messages: messages.map((m, i) => (drop.has(i) ? { ...m, audioData: undefined } : m)),
        };
      }

    case 'SET_THINKING':
      return { ...state, isThinking: action.thinking };

    case 'SET_TOOL_STATUS':
      return { ...state, toolStatus: action.text };

    case 'SET_EMOTION':
      return {
        ...state,
        emotion: action.emotion,
        emotionIntensity: action.intensity ?? null,
        emotionSource: action.source ?? null,
      };
    case 'SET_AFFECTION':
      return { ...state, affection: action.affection };

    case 'SET_SUBTITLE':
      return { ...state, subtitle: action.text };

    case 'SET_ACTIVE_WINDOW':
      return { ...state, activeWindow: action.info };

    case 'UPDATE_SETTINGS':
      return { ...state, settings: { ...state.settings, ...action.settings } };

    case 'SET_ERROR':
      return { ...state, lastError: action.message, errorCode: action.code ?? null };

    case 'CLEAR_MESSAGES':
      return { ...state, messages: [] };

    case 'SET_HISTORY_LIST':
      return { ...state, historyList: action.historyList };

    case 'SET_HISTORY_UID':
      return { ...state, currentHistoryUid: action.uid };

    default:
      return state;
  }
}

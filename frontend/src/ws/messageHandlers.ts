/**
 * WS 消息分发注册表（Phase 0 契约层）。
 *
 * 把原来 App.tsx 里的巨型 switch 拆成「按消息类型注册 handler」的表驱动分发：
 * - 每个消息类型一个独立 handler，依赖经 deps 显式注入（不闭包组件内部变量）；
 * - TS 的 discriminated union 保证 switch 式穷尽性 —— 新增 ServerMessage 成员时
 *   本文件的 handlers 若不处理，编译期不会报错（有 default 兜底），因此要求
 *   新增类型时必须同步在此注册；未注册类型走 `noop` 并打印警告（仅开发期）。
 */
import type { Dispatch } from 'react';
import type {
  AudioMessage,
  ControlMessage,
  ErrorMessage,
  ServerMessage,
} from '@/types/ws';
import type { Action } from '@/state/reducer';
import type { AppState } from '@/state/types';
import type { WSClient } from '@/api/wsClient';
import type { AudioPlayer } from '@/api/audioPlayer';
import type { Live2DAdapter } from '@/live2d/Live2DAdapter';
import { expressionToEmotion } from '@/emotion/expression';
import { resolveBackendUrl } from '@/env';

/** handler 依赖：由 App 装配，注入时保证引用最新（ref 透传）。 */
export interface WsHandlerDeps {
  dispatch: Dispatch<Action>;
  /** 最新 state（经 stateRef 透传，避免闭包过期）。 */
  getState: () => AppState;
  ws: () => WSClient | null;
  audioPlayer: () => AudioPlayer | null;
  adapter: () => Live2DAdapter | null;
  /** 标记后端合成完成（backend-synth-complete）。 */
  setSynthComplete: (v: boolean) => void;
  /** 调整挂起中的音频条目数（audio 入队 +1 / 播完 -1）。 */
  bumpPendingAudio: (delta: number) => void;
  /** 会话链开始时清零挂起计数（原 App.tsx 行为）。 */
  resetPendingAudio: () => void;
  /** 播放完成判定（内部有 250ms 防抖，见 App）。 */
  maybeCompletePlayback: () => void;
}

function nextId(): string {
  return `${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 8)}`;
}

function addAiMessage(deps: WsHandlerDeps, text: string): void {
  deps.dispatch({
    type: 'ADD_MESSAGE',
    message: {
      id: nextId(),
      role: 'ai',
      text,
      name: deps.getState().confName || 'AI',
      streaming: true,
      timestamp: Date.now(),
    },
  });
}

function finalizeLastAiMessage(deps: WsHandlerDeps): void {
  const messages = deps.getState().messages;
  for (let i = messages.length - 1; i >= 0; i--) {
    if (messages[i].role === 'ai' && messages[i].streaming) {
      deps.dispatch({ type: 'FINALIZE_MESSAGE', id: messages[i].id });
    }
  }
}

// ------------------------------------------------------------------ //
// 各消息类型 handler
// ------------------------------------------------------------------ //

function handleSetModelAndConf(msg: Extract<ServerMessage, { type: 'set-model-and-conf' }>, deps: WsHandlerDeps): void {
  const modelUrl = resolveBackendUrl(msg.model_info.url);
  deps.dispatch({
    type: 'SET_MODEL',
    modelUrl,
    modelInfo: msg.model_info,
    confName: msg.conf_name,
    confUid: msg.conf_uid,
  });
}

function handleFullText(msg: Extract<ServerMessage, { type: 'full-text' }>, deps: WsHandlerDeps): void {
  const text = msg.text ?? '';
  if (text === 'Thinking...' || text.includes('AI wants to speak')) {
    deps.dispatch({ type: 'SET_THINKING', thinking: true });
  } else if (text && text !== 'Connection established') {
    addAiMessage(deps, text);
  }
}

function handleAudio(msg: AudioMessage, deps: WsHandlerDeps): void {
  const displayText = msg.display_text?.text ?? '';
  const expression = msg.actions?.expressions?.[0] ?? null;

  if (displayText) {
    deps.dispatch({
      type: 'ADD_MESSAGE',
      message: {
        id: nextId(),
        role: 'ai',
        text: displayText,
        // 字幕翻译附在消息上作显示层小字（与原文不同才带，避免同语言重复）
        subtitle:
          msg.subtitle_text && msg.subtitle_text !== displayText
            ? msg.subtitle_text
            : undefined,
        name: (msg.display_text?.name ?? deps.getState().confName) || 'AI',
        avatar: msg.display_text?.avatar ?? undefined,
        streaming: true,
        timestamp: Date.now(),
      },
    });
  }

  const emotion = expressionToEmotion(expression, deps.getState().modelInfo);
  if (emotion !== 'neutral') deps.dispatch({ type: 'SET_EMOTION', emotion });

  deps.bumpPendingAudio(1);
  deps.audioPlayer()?.enqueue({
    id: nextId(),
    base64: msg.audio,
    volumes: msg.volumes ?? [],
    sliceLengthMs: msg.slice_length || 20,
    expression: msg.actions?.expressions ?? null,
    displayText: displayText || null,
  });
}

function handleTranscript(msg: Extract<ServerMessage, { type: 'transcript' }>, deps: WsHandlerDeps): void {
  deps.dispatch({ type: 'SET_SUBTITLE', text: msg.text ?? '' });
}

function handleUserInputTranscription(msg: Extract<ServerMessage, { type: 'user-input-transcription' }>, deps: WsHandlerDeps): void {
  const text = (msg.text ?? '').trim();
  if (!text) return;
  // 语音输入的「正在识别…」占位消息：原地更新为识别文本，避免「等 AI 回复后才一起出现」
  const pending = [...deps.getState().messages]
    .reverse()
    .find((m) => m.role === 'user' && m.streaming === true);
  if (pending) {
    deps.dispatch({ type: 'UPDATE_MESSAGE_TEXT', id: pending.id, text });
    return;
  }
  deps.dispatch({
    type: 'ADD_MESSAGE',
    message: {
      id: nextId(),
      role: 'user',
      text,
      timestamp: Date.now(),
    },
  });
}

function handleAffectionUpdate(msg: Extract<ServerMessage, { type: 'affection-update' }>, deps: WsHandlerDeps): void {
  deps.dispatch({ type: 'SET_AFFECTION', affection: msg.affection });
  if (msg.milestone) {
    addAiMessage(deps, msg.milestone);
  }
}

function handleControl(msg: ControlMessage, deps: WsHandlerDeps): void {
  switch (msg.text) {
    case 'conversation-chain-start':
      deps.setSynthComplete(false);
      deps.resetPendingAudio();
      deps.dispatch({ type: 'SET_THINKING', thinking: true });
      break;
    case 'conversation-chain-end':
      deps.dispatch({ type: 'SET_THINKING', thinking: false });
      deps.dispatch({ type: 'SET_SUBTITLE', text: '' });
      break;
    case 'interrupt':
      deps.audioPlayer()?.stop();
      deps.dispatch({ type: 'SET_THINKING', thinking: false });
      break;
    case 'start-mic':
    case 'mic-audio-end':
      // 前端不消费这两个信号（语音链路由 ChatPanel 驱动），no-op。
      break;
    default:
      break;
  }
}

function handleBackendSynthComplete(_msg: Extract<ServerMessage, { type: 'backend-synth-complete' }>, deps: WsHandlerDeps): void {
  deps.setSynthComplete(true);
  deps.maybeCompletePlayback();
}

function handleForceNewMessage(_msg: Extract<ServerMessage, { type: 'force-new-message' }>, deps: WsHandlerDeps): void {
  deps.dispatch({ type: 'SET_THINKING', thinking: false });
  deps.dispatch({ type: 'SET_SUBTITLE', text: '' });
  finalizeLastAiMessage(deps);
}

function handleError(msg: ErrorMessage, deps: WsHandlerDeps): void {
  // Phase 3：错误不再注入聊天流，改为横幅修复卡（ErrorBanner 渲染自
  // state.lastError + errorCode）。message 文案来自后端（用户可读中文）。
  deps.dispatch({ type: 'SET_ERROR', message: msg.message ?? '未知错误', code: msg.code });
  deps.dispatch({ type: 'SET_THINKING', thinking: false });
}

function handleHistoryList(msg: Extract<ServerMessage, { type: 'history-list' }>, deps: WsHandlerDeps): void {
  deps.dispatch({ type: 'SET_HISTORY_LIST', historyList: msg.histories ?? [] });
}

function handleNewHistoryCreated(msg: Extract<ServerMessage, { type: 'new-history-created' }>, deps: WsHandlerDeps): void {
  // 记录当前会话 UID、刷新历史列表。
  deps.dispatch({ type: 'SET_HISTORY_UID', uid: msg.history_uid });
  // 只有「用户手动新建会话」才清空聊天区；后端在对话中途自动创建的会话
  // （auto=true，首次真人消息触发）必须保留现有消息 —— 否则语音输入的
  // user-input-transcription 刚回显就被 CLEAR_MESSAGES 清掉（用户消息"消失"）。
  if (!msg.auto) {
    deps.dispatch({ type: 'CLEAR_MESSAGES' });
  }
  deps.ws()?.sendFetchHistoryList();
}

function handleHistoryDeleted(msg: Extract<ServerMessage, { type: 'history-deleted' }>, deps: WsHandlerDeps): void {
  if (msg.success) {
    const uid = msg.history_uid;
    const list = deps
      .getState()
      .historyList.filter((h) => h.uid !== uid && h.history_uid !== uid);
    deps.dispatch({ type: 'SET_HISTORY_LIST', historyList: list });
    // 删除的是当前会话：重置 UID 并清空聊天区
    if (deps.getState().currentHistoryUid === uid) {
      deps.dispatch({ type: 'SET_HISTORY_UID', uid: null });
      deps.dispatch({ type: 'CLEAR_MESSAGES' });
    }
  }
}

function handleHistoryTitleUpdated(msg: Extract<ServerMessage, { type: 'history-title-updated' }>, deps: WsHandlerDeps): void {
  if (!msg.success || !msg.history_uid) return;
  const uid = msg.history_uid;
  const list = deps
    .getState()
    .historyList.map((h) =>
      String(h.uid ?? h.history_uid ?? '') === uid ? { ...h, title: msg.title } : h,
    );
  deps.dispatch({ type: 'SET_HISTORY_LIST', historyList: list });
}

function handleHistoryData(msg: Extract<ServerMessage, { type: 'history-data' }>, deps: WsHandlerDeps): void {
  const messages = (msg.messages ?? []).map((m) => ({
    id: nextId(),
    role: m.role === 'human' ? ('user' as const) : ('ai' as const),
    text: m.content ?? '',
    name: m.name,
    avatar: m.avatar,
    timestamp: Date.now(),
  }));
  deps.dispatch({ type: 'CLEAR_MESSAGES' });
  messages.forEach((m) => deps.dispatch({ type: 'ADD_MESSAGE', message: m }));
}

/** 后端已定义但前端当前不消费的消息类型 —— 显式登记为 no-op，防漏审。 */
function noop(): void {
  /* 前端不消费：config-files / group-update / config-switched / heartbeat-ack / config-updated */
}

// ------------------------------------------------------------------ //
// 注册表
// ------------------------------------------------------------------ //

export const serverMessageHandlers: Record<ServerMessage['type'], (msg: ServerMessage, deps: WsHandlerDeps) => void> = {
  'set-model-and-conf': handleSetModelAndConf as never,
  'full-text': handleFullText as never,
  'audio': handleAudio as never,
  'transcript': handleTranscript as never,
  'user-input-transcription': handleUserInputTranscription as never,
  'affection-update': handleAffectionUpdate as never,
  'control': handleControl as never,
  'backend-synth-complete': handleBackendSynthComplete as never,
  'force-new-message': handleForceNewMessage as never,
  'error': handleError as never,
  'history-list': handleHistoryList as never,
  'new-history-created': handleNewHistoryCreated as never,
  'history-deleted': handleHistoryDeleted as never,
  'history-title-updated': handleHistoryTitleUpdated as never,
  'history-data': handleHistoryData as never,
  'config-files': noop as never,
  'group-update': noop as never,
  'config-switched': noop as never,
  'heartbeat-ack': noop as never,
  'config-updated': noop as never,
};

/**
 * 统一分发入口：由 WSClient.onMessage 调用。
 * 未注册类型（未来新增、后端提前发布）记开发期警告，不崩、不吞。
 */
export function dispatchServerMessage(msg: ServerMessage, deps: WsHandlerDeps): void {
  const handler = serverMessageHandlers[msg.type as ServerMessage['type']];
  if (handler) {
    handler(msg, deps);
  } else {
    // eslint-disable-next-line no-console
    console.warn('[ws] unhandled message type:', msg.type);
  }
}

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
import { publishIntent } from '@/state/intentBus';
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
      name: deps.getState().characterName || deps.getState().confName,
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
    // conf_name is the selected character-card identity. Older backends may
    // still send a legacy character_name such as 小月 for the hiyori card;
    // never let that legacy field override the card name.
    characterName: msg.conf_name || msg.character_name || '',
    confUid: msg.conf_uid,
  });
}

function handleFullText(msg: Extract<ServerMessage, { type: 'full-text' }>, deps: WsHandlerDeps): void {
  const text = msg.text ?? '';
  if (text === 'Thinking...' || text.includes('AI wants to speak')) {
    deps.dispatch({ type: 'SET_THINKING', thinking: true });
  } else if (text && text !== 'Connection established') {
    if (msg.quote) {
      // 预设台词（关键词触发/点击互动）：完整句子，独立新气泡。
      addAiMessage(deps, text);
      return;
    }
    // Phase 3（pet-ptt-workflow）：桌宠字幕条 = 最近一条流式 AI full-text。
    deps.dispatch({ type: 'SET_PET_SUBTITLE', text });
    // v6 文本流式：LLM 句子逐句到达（后端不再等 TTS 合成完才发文本）。
    // 最后一条 AI 消息仍在流式且未绑定语音 → 追加到同一气泡（打字机效果）；
    // 否则（新回复段/上一段语音已绑定）→ 开新流式气泡。
    const messages = deps.getState().messages;
    const last = messages.length > 0 ? messages[messages.length - 1] : undefined;
    if (last && last.role === 'ai' && last.streaming && !last.audioBound) {
      deps.dispatch({ type: 'APPEND_MESSAGE_TEXT', id: last.id, text });
    } else {
      addAiMessage(deps, text);
    }
  }
}

function handleAudio(msg: AudioMessage, deps: WsHandlerDeps): void {
  const displayText = msg.display_text?.text ?? '';
  // 表情源：优先 LLM 动作标签（actions.expressions）；为空时依次用
  // emotion_meta（Phase 1 LLM/规则分类，最准）→ msg.emotion（规则分析）兜底——
  // 否则 DeepSeek 不带动作标签时表情永远不动（只有口型）。
  const actionExpression = msg.actions?.expressions?.[0] ?? null;
  const metaEmotion =
    msg.emotion_meta?.emotion && msg.emotion_meta.emotion !== 'neutral'
      ? msg.emotion_meta.emotion
      : null;
  const fallbackEmotion =
    msg.emotion && msg.emotion !== 'neutral' ? msg.emotion : null;
  const expression = actionExpression ?? metaEmotion ?? fallbackEmotion;

  // 音频数据快照（2026-08-10）：存到消息供气泡「再次播放」——
  // 后端 TTS wav 播完即删（tts_manager finally remove_file），前端必须自己留一份。
  // msg.audio 为 null（静默 payload）时不留，气泡不显示重播。
  const audioReplay: import('@/state/types').AudioReplayData | null = msg.audio
    ? {
        base64: msg.audio,
        volumes: msg.volumes ?? [],
        visemes: msg.visemes ?? null,
        sliceLengthMs: msg.slice_length || 20,
        expression: msg.actions?.expressions ?? null,
      }
    : null;
  const replayPayload = audioReplay ?? undefined;

  if (displayText) {
    // v6 语音绑定：文本已由 full-text 流式上屏，audio 到达后绑定到气泡播放，
    // 不再「语音+文本一起出现」、也不为同一段文本重复建气泡。
    const messages = deps.getState().messages;
    // 1) 有未绑定语音的 AI 气泡（流式文本刚显示完）→ 绑定（结束流式 + 补字幕）。
    // 2026-08-10 修复：排除已有音频快照的消息（audioData）——纯 audio 播报气泡
    // （外壳播报/主动搭话）自带音频，不应再成为后续 audio 的绑定目标，否则任务
    // 完成的 run_end 播报会被「吞」进 run_start 的「好呀主人」气泡里不可见。
    const pending = [...messages]
      .reverse()
      .find((m) => m.role === 'ai' && !m.audioBound && !m.audioData);
    if (pending) {
      deps.dispatch({
        type: 'BIND_AUDIO_TO_MESSAGE',
        id: pending.id,
        subtitle:
          msg.subtitle_text && msg.subtitle_text !== displayText
            ? msg.subtitle_text
            : undefined,
        audio: replayPayload,
      });
    } else {
      // 2) 多段回复的后续段落：该段文本已包含在已有气泡里 → 只播放，不重复建气泡。
      const tail = messages[messages.length - 1];
      const alreadyShown = !!tail && tail.role === 'ai' && tail.text.includes(displayText);
      if (alreadyShown) {
        deps.dispatch({
          type: 'BIND_AUDIO_TO_MESSAGE',
          id: tail.id,
          audio: replayPayload,
        });
      } else {
        // 3) 纯 audio 路径（无 full-text 先行，如主动搭话/外壳播报）：按旧行为新建气泡。
        // 2026-08-10：新建即标 audioBound:true——该气泡的音频已作为 audioData 快照
        // 保存（供「再次播放」），后续 audio 消息不应再绑定到它（多段播报各自成气泡）。
        deps.dispatch({
          type: 'ADD_MESSAGE',
          message: {
            id: nextId(),
            role: 'ai',
            text: displayText,
            subtitle:
              msg.subtitle_text && msg.subtitle_text !== displayText
                ? msg.subtitle_text
                : undefined,
            // Audio payloads can carry a stale name from an older backend or
            // queued response. The current selected card is authoritative.
            name: deps.getState().characterName || deps.getState().confName,
            avatar: msg.display_text?.avatar ?? undefined,
            streaming: false,
            audioBound: true,
            timestamp: Date.now(),
            audioData: replayPayload,
          },
        });
      }
    }
  }

  const emotion = expressionToEmotion(expression, deps.getState().modelInfo);
  if (emotion !== 'neutral') {
    deps.dispatch({
      type: 'SET_EMOTION',
      emotion,
      intensity: msg.emotion_meta?.intensity ?? null,
      source: msg.emotion_meta?.source ?? null,
    });
  }

  deps.bumpPendingAudio(1);
  deps.audioPlayer()?.enqueue({
    id: nextId(),
    base64: msg.audio,
    volumes: msg.volumes ?? [],
    visemes: msg.visemes ?? null,
    emotionMeta: msg.emotion_meta ?? null,
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
      deps.dispatch({ type: 'SET_TOOL_STATUS', text: null });
      // Phase 3：会话链结束，桌宠字幕保留最后一段由组件淡出，这里清空数据源。
      deps.dispatch({ type: 'SET_PET_SUBTITLE', text: '' });
      // 对话结束刷新历史列表：新会话标题/最后一条消息此刻已落盘，侧边栏立即更新。
      deps.ws()?.sendFetchHistoryList();
      break;
    case 'interrupt':
      deps.audioPlayer()?.stop();
      deps.dispatch({ type: 'SET_THINKING', thinking: false });
      deps.dispatch({ type: 'SET_TOOL_STATUS', text: null });
      deps.dispatch({ type: 'SET_PET_SUBTITLE', text: '' });
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
  deps.dispatch({ type: 'SET_TOOL_STATUS', text: null });
  deps.dispatch({ type: 'SET_PET_SUBTITLE', text: '' });
  finalizeLastAiMessage(deps);
}

function handleError(msg: ErrorMessage, deps: WsHandlerDeps): void {
  // Phase 3：错误不再注入聊天流，改为横幅修复卡（ErrorBanner 渲染自
  // state.lastError + errorCode）。message 文案来自后端（用户可读中文）。
  deps.dispatch({ type: 'SET_ERROR', message: msg.message ?? '未知错误', code: msg.code });
  deps.dispatch({ type: 'SET_THINKING', thinking: false });
  deps.dispatch({ type: 'SET_TOOL_STATUS', text: null });
  deps.dispatch({ type: 'SET_PET_SUBTITLE', text: '' });
}

function handleHistoryList(msg: Extract<ServerMessage, { type: 'history-list' }>, deps: WsHandlerDeps): void {
  deps.dispatch({ type: 'SET_HISTORY_LIST', historyList: msg.histories ?? [] });
}

function handleNewHistoryCreated(msg: Extract<ServerMessage, { type: 'new-history-created' }>, deps: WsHandlerDeps): void {
  // 记录当前会话 UID、刷新历史列表。
  deps.dispatch({ type: 'SET_HISTORY_UID', uid: msg.history_uid });
  // v5 修复：后端 new-history-created 携带 workspace；但空会话（尚无消息）不会进
  // history-list（后端 get_history_list 只返回有消息的会话），若不同步插入占位条目，
  // currentWorkspace 解析不到 → 聊天界面「选择工作目录」引导一直显示。
  if (msg.workspace) {
    const list = deps.getState().historyList;
    const exists = list.some((h) => String(h.uid ?? h.history_uid ?? '') === msg.history_uid);
    if (!exists) {
      deps.dispatch({
        type: 'SET_HISTORY_LIST',
        historyList: [{ uid: msg.history_uid, workspace: msg.workspace }, ...list],
      });
    }
  }
  // 只有「用户手动新建会话」才清空聊天区；后端在对话中途自动创建的会话
  // （auto=true，首次真人消息触发）必须保留现有消息 —— 否则语音输入的
  // user-input-transcription 刚回显就被 CLEAR_MESSAGES 清掉（用户消息"消失"）。
  if (!msg.auto) {
    deps.dispatch({ type: 'CLEAR_MESSAGES' });
    // Phase 3：手动切换会话 → 清空桌宠字幕。
    deps.dispatch({ type: 'SET_PET_SUBTITLE', text: '' });
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

/** v5：会话移动到另一个工作目录 → 更新本地列表（分组随刷新重排）。 */
function handleHistoryWorkspaceUpdated(
  msg: Extract<ServerMessage, { type: 'history-workspace-updated' }>,
  deps: WsHandlerDeps,
): void {
  if (!msg.success || !msg.history_uid) return;
  const uid = msg.history_uid;
  const list = deps
    .getState()
    .historyList.map((h) =>
      String(h.uid ?? h.history_uid ?? '') === uid ? { ...h, workspace: msg.workspace } : h,
    );
  deps.dispatch({ type: 'SET_HISTORY_LIST', historyList: list });
}

/** v5：存量会话全部清空 → 重置列表与当前会话。 */
function handleHistoriesCleared(_msg: Extract<ServerMessage, { type: 'histories-cleared' }>, deps: WsHandlerDeps): void {
  deps.dispatch({ type: 'SET_HISTORY_LIST', historyList: [] });
  deps.dispatch({ type: 'SET_HISTORY_UID', uid: null });
  deps.dispatch({ type: 'CLEAR_MESSAGES' });
}

function handleHistoryData(msg: Extract<ServerMessage, { type: 'history-data' }>, deps: WsHandlerDeps): void {
  // A user can click sessions quickly. Ignore a late response for an older
  // request; otherwise it clears the current transcript and makes messages
  // appear to disappear until the next refresh.
  const requestedUid = deps.ws()?.latestRequestedHistoryUid;
  if (requestedUid && msg.history_uid && requestedUid !== msg.history_uid) return;
  // Moonlight（2026-08-10 修复）：点击旧会话加载后必须同步 currentHistoryUid——
  // 后端 _handle_fetch_history 会设置 context.history_uid，但前端 state 之前
  // 从未更新，导致输入时 !currentHistoryUid 误弹"选择工作目录"。
  if (msg.history_uid) {
    deps.dispatch({ type: 'SET_HISTORY_UID', uid: msg.history_uid });
  }
  // 任务消息属于任务卡/任务状态流，不属于普通聊天记录。
  // 兼容旧历史：内部委托指令过去没有 kind，但它总是紧跟 task_shell；
  // 只按这个结构隐藏，避免误删用户真正发出的“帮我做……”消息。
  const visibleHistoryMessages: typeof msg.messages = [];
  let previousWasTaskShell = false;
  for (const message of msg.messages ?? []) {
    const content = String(message.content ?? '').trim();
    const isTaggedTaskMessage =
      message.kind === 'task_brief' ||
      message.kind === 'task_result' ||
      message.kind === 'task_shell';
    const isLegacyTaskMessage =
      content.startsWith('【任务简报】') || content.startsWith('【任务结果】');
    const isLegacyInternalPrompt = message.role === 'human' && previousWasTaskShell;

    if (!isTaggedTaskMessage && !isLegacyTaskMessage && !isLegacyInternalPrompt) {
      visibleHistoryMessages.push(message);
    }
    previousWasTaskShell = message.kind === 'task_shell';
  }

  const currentCharacterName = deps.getState().characterName || deps.getState().confName;
  const messages = visibleHistoryMessages.map((m) => ({
    id: nextId(),
    role: m.role === 'human' ? ('user' as const) : ('ai' as const),
    text: m.content ?? '',
    // 历史只保存内容，不沿用旧角色名（例如 AI / hiyori / 小月）。
    name: m.role === 'human' ? undefined : currentCharacterName,
    avatar: m.avatar,
    timestamp: Date.now(),
  }));
  deps.dispatch({ type: 'CLEAR_MESSAGES' });
  deps.dispatch({ type: 'SET_PET_SUBTITLE', text: '' }); // Phase 3：切换 history 清字幕
  messages.forEach((m) => deps.dispatch({ type: 'ADD_MESSAGE', message: m }));
}

function handleToolCallStatus(msg: Extract<ServerMessage, { type: 'tool_call_status' }>, deps: WsHandlerDeps): void {
  // text 非空 = 工具执行中（聊天区显示状态条）；空串 = 结束清除。
  const text = (msg.text ?? '').trim();
  deps.dispatch({ type: 'SET_TOOL_STATUS', text: text || null });
  if (text) {
    deps.dispatch({ type: 'SET_THINKING', thinking: false });
  }
}

/** P5.1 intent-event：意图副模型出站 → 总线（ChatInput 徽标实时显示）。 */
function handleIntentEvent(msg: Extract<ServerMessage, { type: 'intent-event' }>, deps: WsHandlerDeps): void {
  void deps;
  try {
    publishIntent({
      intent: msg.intent ?? 'chat',
      emotion: msg.emotion ?? 'neutral',
      source: msg.source ?? 'rule',
      text: msg.text,
      ts: Date.now(),
    });
  } catch {
    /* bus errors never break WS loop */
  }
}

/** 2026-08-09：delegate 任务完整结果直达聊天区 —— 直接渲染为 AI 气泡。
 *  不等 LLM 逐句复述，任务清单/表格立刻可见；文本带 Markdown 由 ChatBubble 渲染。 */
function handleTaskResult(_msg: Extract<ServerMessage, { type: 'task-result' }>, deps: WsHandlerDeps): void {
  // Structured task output is rendered by the task run card, not as a second
  // assistant bubble in the ordinary conversation.
  // 任务结果已直达展示，把可能残留的"思考中"状态清掉。
  deps.dispatch({ type: 'SET_THINKING', thinking: false });
  deps.dispatch({ type: 'SET_TOOL_STATUS', text: null });
}

/** 后端已定义但前端当前不消费的消息类型 —— 显式登记为 no-op，防漏审。 */
function noop(): void {
  /* 前端不消费：config-files / group-update / config-switched / heartbeat-ack / config-updated */
}

/** screen_awareness：后端推送屏幕状态 → 全局 state（设置页/状态灯数据源）。 */
function handleScreenStatus(
  msg: Extract<ServerMessage, { type: 'screen-status' }>,
  deps: WsHandlerDeps,
): void {
  deps.dispatch({ type: 'SET_SCREEN_STATUS', status: msg });
}

/** screen_awareness：屏幕上下文就绪通知（前端可据此显示「正在看屏幕」）。 */
function handleScreenContext(
  msg: Extract<ServerMessage, { type: 'screen-context' }>,
  deps: WsHandlerDeps,
): void {
  if (msg.snapshot) {
    deps.dispatch({ type: 'SET_SCREEN_STATUS', status: msg.snapshot as never });
  }
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
  'history-workspace-updated': handleHistoryWorkspaceUpdated as never,
  'histories-cleared': handleHistoriesCleared as never,
  'history-data': handleHistoryData as never,
  'config-files': noop as never,
  'group-update': noop as never,
  'config-switched': noop as never,
  'heartbeat-ack': noop as never,
  'config-updated': noop as never,
  'tool_call_status': handleToolCallStatus as never,
  'intent-event': handleIntentEvent as never,
  'task-result': handleTaskResult as never,
  'screen-status': handleScreenStatus as never,
  'screen-context': handleScreenContext as never,
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

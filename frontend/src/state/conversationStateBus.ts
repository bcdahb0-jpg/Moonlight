/**
 * 对话状态事件总线（P1.5 对话状态机实时化）。
 *
 * useAppShell 把 isThinking / audioPlaying 归约为三态并发布；
 * 控制台内 ConversationStateMachine 订阅——绕开「WSClient 构造时回调
 * 不可动态订阅」的限制，任何 React 组件都能拿到实时状态。
 */
export type ConversationState = 'idle' | 'thinking' | 'speaking';

let current: ConversationState = 'idle';
const listeners = new Set<(s: ConversationState) => void>();

export function getConversationState(): ConversationState {
  return current;
}

export function setConversationState(state: ConversationState): void {
  if (state === current) return;
  current = state;
  for (const cb of listeners) {
    try {
      cb(state);
    } catch {
      /* listener errors never break the bus */
    }
  }
}

/** 订阅状态变化；返回退订函数。 */
export function subscribeConversationState(cb: (s: ConversationState) => void): () => void {
  listeners.add(cb);
  return () => {
    listeners.delete(cb);
  };
}

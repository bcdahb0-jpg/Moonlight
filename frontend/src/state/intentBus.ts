/**
 * 意图事件总线（P5.1）：后端 intent-event 出站 → 聊天输入条徽标实时显示。
 *
 * messageHandlers 收到 intent-event 后 publish；ChatInput 订阅展示
 * 「意图: 勿扰/闲聊/任务 · 情绪」chip。绕开 WSClient 构造时回调限制
 * （与 conversationStateBus 同款模式）。
 */

export interface IntentSignal {
  intent: string; // silence | chat | task
  emotion: string;
  source: string;
  text?: string;
  ts: number;
}

let current: IntentSignal | null = null;
const listeners = new Set<(s: IntentSignal) => void>();

export function getIntentSignal(): IntentSignal | null {
  return current;
}

export function publishIntent(signal: IntentSignal): void {
  current = signal;
  for (const cb of listeners) {
    try {
      cb(signal);
    } catch {
      /* listener errors never break the bus */
    }
  }
}

/** 订阅意图信号；返回退订函数。 */
export function subscribeIntent(cb: (s: IntentSignal) => void): () => void {
  listeners.add(cb);
  return () => {
    listeners.delete(cb);
  };
}

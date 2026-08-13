/**
 * pttReducer — 桌宠对讲（PTT）纯函数状态机（Phase 0）。
 *
 * 单一事实源：docs/pet-ptt-workflow-plan.md §3.1。
 *
 * 状态：
 * - idle：AI 未生成且未播放 → 按住说话；
 * - recording：按住录音，松开发送；
 * - ai_speaking：AI 生成/播放中 → 短按打断；按住超过长按阈值 = 打断 + 立即录音。
 *
 * 纯函数：不持有 timer / 不碰录音 / 不碰 WS。副作用由 PttButton 根据返回的
 * command 执行（录音走 VoiceRecorder 边录边发，与窗口模式 ChatInput 一致）。
 */
import type { PointerEvent as ReactPointerEvent } from 'react';

/** 长按阈值：ai_speaking 按住超过此时长 = 抢先发言（打断 + 录音）。 */
export const PTT_LONG_PRESS_MS = 350;
/** 最短有效录音：低于此时长按「短按」处理，丢弃空音频不发送。 */
export const PTT_MIN_RECORD_MS = 250;

export type PttPhase = 'idle' | 'recording' | 'ai_speaking';

export interface PttState {
  phase: PttPhase;
  /** 指针是否仍按住（pointer capture 持有中）。 */
  holding: boolean;
  /** recording 实际开始时间（monotonic ms；用于最小时长判定）。 */
  startedAt: number | null;
  /** 最近一次麦克风错误（权限拒绝等），展示后由组件清空。 */
  micError: string | null;
}

export type PttCommand =
  | { kind: 'start_mic' }
  | { kind: 'stop_and_send'; discard: boolean }
  | { kind: 'cancel_recording' }
  | { kind: 'interrupt' }
  | { kind: 'interrupt_then_record' }
  | { kind: 'clear_mic_error' }
  | { kind: 'none' };

export type PttEvent =
  | { type: 'POINTER_DOWN'; now: number; aiSpeaking: boolean }
  | { type: 'POINTER_UP'; now: number }
  | { type: 'POINTER_CANCEL' }
  /** ai_speaking 下按住超过长按阈值（组件 timer 到期派发）。 */
  | { type: 'LONG_PRESS'; now: number }
  /** getUserMedia 成功，录音实际开始。 */
  | { type: 'MIC_STARTED'; now: number }
  | { type: 'MIC_START_FAILED'; error: string }
  /** 录音停止完成（onEnd 回调；命令已在 POINTER_UP 时执行过，此事件只清状态）。 */
  | { type: 'MIC_STOPPED' }
  /** AI 状态变化：isThinking || audioPlayer.isPlaying。 */
  | { type: 'AI_STATE_CHANGE'; speaking: boolean };

export function createInitialPttState(): PttState {
  return { phase: 'idle', holding: false, startedAt: null, micError: null };
}

export function pttReducer(state: PttState, event: PttEvent): { state: PttState; command: PttCommand } {
  switch (event.type) {
    // ------------------------------------------------------------------ //
    // 按下
    // ------------------------------------------------------------------ //
    case 'POINTER_DOWN': {
      if (state.holding) return { state, command: { kind: 'none' } };
      if (event.aiSpeaking) {
        // AI 说话中：按住不放 = 可能抢先发言；先保持 ai_speaking，
        // 组件启动长按 timer（到期派发 LONG_PRESS）。
        // 显式切到 ai_speaking（即使 AI_STATE_CHANGE 事件尚未处理，也不留 idle 态）。
        return {
          state: { ...state, phase: 'ai_speaking', holding: true },
          command: { kind: 'none' },
        };
      }
      // 空闲：直接开始录音。
      return {
        state: {
          phase: 'recording',
          holding: true,
          startedAt: event.now,
          micError: null,
        },
        command: { kind: 'start_mic' },
      };
    }

    // ------------------------------------------------------------------ //
    // AI 说话中按住 → 长按到期：打断 + 立即录音（抢先发言）
    // ------------------------------------------------------------------ //
    case 'LONG_PRESS': {
      if (state.phase !== 'ai_speaking' || !state.holding) {
        return { state, command: { kind: 'none' } };
      }
      return {
        state: {
          phase: 'recording',
          holding: true,
          startedAt: event.now,
          micError: null,
        },
        command: { kind: 'interrupt_then_record' },
      };
    }

    // ------------------------------------------------------------------ //
    // 松开
    // ------------------------------------------------------------------ //
    case 'POINTER_UP': {
      // 录音中松开：时长低于最小阈值按「短按」丢弃空音频。
      if (state.phase === 'recording' && state.holding) {
        const discard =
          state.startedAt !== null && event.now - state.startedAt < PTT_MIN_RECORD_MS;
        return {
          state: { ...createInitialPttState(), phase: 'idle' },
          command: discard
            ? { kind: 'stop_and_send', discard: true }
            : { kind: 'stop_and_send', discard: false },
        };
      }
      // AI 说话中短按释放（未过长按阈值）：只打断，不启动录音。
      if (state.phase === 'ai_speaking' && state.holding) {
        return {
          state: createInitialPttState(),
          command: { kind: 'interrupt' },
        };
      }
      return { state: { ...state, holding: false }, command: { kind: 'none' } };
    }

    // ------------------------------------------------------------------ //
    // 取消（pointercancel / 指针逃逸 / 窗口失焦）：不发送，丢弃缓冲
    // ------------------------------------------------------------------ //
    case 'POINTER_CANCEL': {
      if (state.phase === 'recording' && state.holding) {
        return {
          state: createInitialPttState(),
          command: { kind: 'cancel_recording' },
        };
      }
      if (state.phase === 'ai_speaking' && state.holding) {
        // 长按 timer 由组件清理；不发送打断（取消 = 放弃手势）。
        return {
          state: { ...createInitialPttState(), phase: 'ai_speaking' },
          command: { kind: 'none' },
        };
      }
      return { state: { ...state, holding: false }, command: { kind: 'none' } };
    }

    // ------------------------------------------------------------------ //
    // 麦克风异步确认
    // ------------------------------------------------------------------ //
    case 'MIC_STARTED': {
      if (state.phase !== 'recording') return { state, command: { kind: 'none' } };
      return { state, command: { kind: 'none' } };
    }

    case 'MIC_START_FAILED': {
      return {
        state: { ...createInitialPttState(), micError: event.error },
        command: { kind: 'none' },
      };
    }

    case 'MIC_STOPPED': {
      if (state.phase === 'recording') {
        return { state: createInitialPttState(), command: { kind: 'none' } };
      }
      return { state, command: { kind: 'none' } };
    }

    // ------------------------------------------------------------------ //
    // AI 状态切换（仅 idle ↔ ai_speaking；录音中不被打断）
    // ------------------------------------------------------------------ //
    case 'AI_STATE_CHANGE': {
      if (state.phase === 'recording') return { state, command: { kind: 'none' } };
      if (state.phase === 'idle' && event.speaking) {
        return { state: { ...state, phase: 'ai_speaking' }, command: { kind: 'none' } };
      }
      if (state.phase === 'ai_speaking' && !event.speaking && !state.holding) {
        return { state: { ...state, phase: 'idle' }, command: { kind: 'none' } };
      }
      return { state, command: { kind: 'none' } };
    }

    default:
      return { state, command: { kind: 'none' } };
  }
}

/** 按钮点击区域事件 → 仅转发 pointer 语义（避免在组件里塞 DOM 判断）。 */
export function pointerButton(e: ReactPointerEvent<HTMLElement>): { down: boolean; id: number } {
  return { down: e.type === 'pointerdown', id: e.pointerId };
}

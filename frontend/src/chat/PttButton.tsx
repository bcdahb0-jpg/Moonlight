/**
 * PttButton — 桌宠对讲按钮（Phase 0 + 修复 2026-08-11）。
 *
 * 用 pttReducer 状态机统一判定短按/长按，避免「click=打断」与「pointerdown=录音」
 * 两条冲突路径。职责：
 * - pointer capture：按住期间指针逃逸不丢事件；
 * - 长按 timer：ai_speaking 按住超过阈值 → 打断 + 立即录音（抢先发言）；
 * - 失焦 / pointercancel → 取消录音，不发送；
 * - 录音音量条 + 倾听动画 + 麦克风错误提示。
 *
 * 录音链路（修复 2026-08-11）：与窗口模式 ChatInput **完全一致** ——
 * 直接持有 VoiceRecorder，每个 PCM chunk 实时回调（边录边发），不缓冲、
 * 不依赖 drain 时序；短按丢弃（discard）只抑制「结束信号」，已发出的 chunk
 * 由后端在下次 mic-audio-end 时清空，不影响链路。
 *
 * 稳定性：所有外部回调经 ref 转发，transition 保持引用稳定（useCallback([])），
 * 避免每次渲染重建导致长按 timer / 录音生命周期竞态。
 */
import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type PointerEvent as ReactPointerEvent,
  type ReactElement,
} from 'react';
import {
  createInitialPttState,
  PTT_LONG_PRESS_MS,
  pttReducer,
  type PttEvent,
  type PttState,
} from '@/chat/pttReducer';
import { VoiceRecorder, chunkVolume } from '@/chat/VoiceRecorder';
import { Icon } from '@/ui/icons';

export interface PttButtonProps {
  /** AI 正在生成或播放语音（isThinking || audioPlayer.isPlaying）。 */
  aiSpeaking: boolean;
  connected: boolean;
  /** 打断：停止本地播放 + interrupt 入 WS 发送队列。 */
  onInterrupt: () => void;
  /** 每个 PCM chunk 实时回调（边录边发；与窗口模式 ChatInput 一致）。 */
  onChunk: (chunk: Float32Array) => void;
  /** 录音结束：发送结束信号（App 层确保会话后 flush + mic-audio-end）。 */
  onEnd: () => void;
  /** 麦克风权限/启动错误（App 层提示）。 */
  onMicError?: (error: Error) => void;
}

export function PttButton({
  aiSpeaking,
  connected,
  onInterrupt,
  onChunk,
  onEnd,
  onMicError,
}: PttButtonProps): ReactElement {
  const [ptt, setPtt] = useState<PttState>(createInitialPttState);
  const pttRef = useRef(ptt);
  pttRef.current = ptt;
  const [volume, setVolume] = useState(0);
  const longPressTimerRef = useRef<number | null>(null);
  const recorderRef = useRef<VoiceRecorder | null>(null);
  /** stop 时置 true → VoiceRecorder onEnd 不发结束信号（短按丢弃/取消）。 */
  const discardRef = useRef(false);

  // 最新 props 经 ref 转发（transition 稳定，避免 stale closure）。
  const onInterruptRef = useRef(onInterrupt);
  onInterruptRef.current = onInterrupt;
  const onChunkRef = useRef(onChunk);
  onChunkRef.current = onChunk;
  const onEndRef = useRef(onEnd);
  onEndRef.current = onEnd;
  const onMicErrorRef = useRef(onMicError);
  onMicErrorRef.current = onMicError;
  const aiSpeakingRef = useRef(aiSpeaking);
  aiSpeakingRef.current = aiSpeaking;

  /** 开始录音：与窗口模式 ChatInput.startRecording 相同（边录边发）。 */
  const startRecording = useCallback(async (): Promise<void> => {
    if (recorderRef.current) return;
    discardRef.current = false;
    const recorder = new VoiceRecorder(
      (chunk) => {
        setVolume(chunkVolume(chunk));
        onChunkRef.current(chunk);
      },
      () => {
        setVolume(0);
        const wasDiscarded = discardRef.current;
        recorderRef.current = null;
        if (!wasDiscarded) onEndRef.current();
      },
      (error) => {
        setVolume(0);
        recorderRef.current = null;
        onMicErrorRef.current?.(error);
        transition({ type: 'MIC_START_FAILED', error: error.message });
      },
    );
    recorderRef.current = recorder;
    await recorder.start();
  }, []);

  /** 停止录音；discard=true 丢弃（不发结束信号）。 */
  const stopRecording = useCallback((discard = false): void => {
    discardRef.current = discard;
    recorderRef.current?.stop();
    recorderRef.current = null;
  }, []);

  /** 派发事件并执行 reducer 返回的命令。 */
  const transition = useCallback((event: PttEvent): void => {
    const { state: next, command } = pttReducer(pttRef.current, event);
    pttRef.current = next;
    setPtt(next);
    switch (command.kind) {
      case 'start_mic':
      case 'interrupt_then_record': {
        if (command.kind === 'interrupt_then_record') onInterruptRef.current();
        void startRecording().then(
          () => {
            if (pttRef.current.phase === 'recording') {
              transition({ type: 'MIC_STARTED', now: performance.now() });
            }
          },
          () => {
            /* 错误已由 onError 回调派发 MIC_START_FAILED */
          },
        );
        break;
      }
      case 'stop_and_send':
        stopRecording(command.discard);
        break;
      case 'cancel_recording':
        stopRecording(true);
        break;
      case 'interrupt':
        onInterruptRef.current();
        break;
      case 'clear_mic_error':
      case 'none':
        break;
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // AI 状态变化 → 状态机（idle ↔ ai_speaking）。
  useEffect(() => {
    transition({ type: 'AI_STATE_CHANGE', speaking: aiSpeaking });
  }, [aiSpeaking, transition]);

  // 失焦（窗口 blur）：取消录音 / 放弃长按手势，防止状态卡死。
  useEffect(() => {
    const onBlur = (): void => {
      clearLongPressTimer();
      if (pttRef.current.holding) transition({ type: 'POINTER_CANCEL' });
    };
    window.addEventListener('blur', onBlur);
    return () => window.removeEventListener('blur', onBlur);
  }, [transition]);

  // 卸载清理。
  useEffect(() => {
    return () => {
      clearLongPressTimer();
      discardRef.current = true;
      recorderRef.current?.dispose();
      recorderRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const clearLongPressTimer = (): void => {
    if (longPressTimerRef.current !== null) {
      window.clearTimeout(longPressTimerRef.current);
      longPressTimerRef.current = null;
    }
  };

  const handlePointerDown = (e: ReactPointerEvent<HTMLButtonElement>): void => {
    if (!connected) return;
    try {
      e.currentTarget.setPointerCapture(e.pointerId);
    } catch {
      /* unsupported */
    }
    const wasSpeaking = aiSpeakingRef.current;
    transition({ type: 'POINTER_DOWN', now: performance.now(), aiSpeaking: wasSpeaking });
    if (wasSpeaking) {
      // AI 说话中按住：启动长按 timer（到期 = 抢先发言）。
      clearLongPressTimer();
      longPressTimerRef.current = window.setTimeout(() => {
        longPressTimerRef.current = null;
        transition({ type: 'LONG_PRESS', now: performance.now() });
      }, PTT_LONG_PRESS_MS);
    }
  };

  const handlePointerUp = (e: ReactPointerEvent<HTMLButtonElement>): void => {
    try {
      if (e.currentTarget.hasPointerCapture(e.pointerId)) {
        e.currentTarget.releasePointerCapture(e.pointerId);
      }
    } catch {
      /* unsupported */
    }
    clearLongPressTimer();
    transition({ type: 'POINTER_UP', now: performance.now() });
  };

  const handlePointerCancel = (): void => {
    clearLongPressTimer();
    if (pttRef.current.holding) transition({ type: 'POINTER_CANCEL' });
  };

  const recording = ptt.phase === 'recording';
  const micError = ptt.micError;

  return (
    <button
      type="button"
      className={`ptt-btn ${recording ? 'recording' : ''} ${ptt.phase === 'ai_speaking' ? 'ai-speaking' : ''}`}
      onPointerDown={handlePointerDown}
      onPointerUp={handlePointerUp}
      onPointerCancel={handlePointerCancel}
      onLostPointerCapture={handlePointerCancel}
      disabled={!connected}
      aria-pressed={recording}
      aria-label={
        micError
          ? `麦克风不可用：${micError}`
          : recording
            ? '录音中，松开发送'
            : aiSpeaking
              ? '按住打断并抢先发言，短按只打断'
              : '按住说话'
      }
      title={
        micError
          ? `麦克风不可用：${micError}`
          : recording
            ? '松开结束录音'
            : aiSpeaking
              ? '短按打断 · 按住抢先发言'
              : '按住说话'
      }
    >
      {recording && (
        <span className="ptt-volume" aria-hidden>
          <span
            className="ptt-volume-fill"
            style={{ height: `${Math.max(8, Math.round(volume * 100))}%` }}
          />
        </span>
      )}
      <Icon name={recording ? 'mic' : aiSpeaking ? 'stop' : 'mic'} size={18} />
    </button>
  );
}

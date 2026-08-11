import { useEffect, useRef, useState, type ReactElement } from 'react';
import { VoiceRecorder } from './VoiceRecorder';
import { Icon } from '@/ui/icons';
import type { UseTaskMode } from '@/task/useTaskMode';

export interface ChatInputProps {
  onSend: (text: string) => void;
  onAudioChunk: (chunk: Float32Array) => void;
  onAudioEnd: () => void;
  onInterrupt: () => void;
  canSend: boolean;
  /** AI 正在回复输出中：发送按钮临时变为打断按钮。 */
  isReplying: boolean;
  /** 任务控制器：提供运行中打断。仅窗口模式传入。 */
  taskMode?: UseTaskMode;
}

export function ChatInput({
  onSend,
  onAudioChunk,
  onAudioEnd,
  onInterrupt,
  canSend,
  isReplying,
  taskMode,
}: ChatInputProps): ReactElement {
  const [text, setText] = useState('');
  const [recording, setRecording] = useState(false);
  const [intentVisible, setIntentVisible] = useState(false);
  const intentTimerRef = useRef<number | null>(null);
  const recorderRef = useRef<VoiceRecorder | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);

  // 2026-08-09 统一输入：不分模式，永远「默认模式」。
  // 发送路由仍在 onSend（上层先 resolveSend 意图路由：闲聊→聊天 / 指令→任务内核）。
  const taskRunning = taskMode?.isRunning ?? false;
  const showInterrupt = taskRunning || isReplying;
  // 意图反馈：最近一次分类为 task → 显示「已作为任务执行」chip（5s 自动消失）。
  const lastIntent = taskMode?.lastIntent ?? null;
  useEffect(() => {
    if (lastIntent !== 'task') return;
    setIntentVisible(true);
    if (intentTimerRef.current !== null) window.clearTimeout(intentTimerRef.current);
    intentTimerRef.current = window.setTimeout(() => setIntentVisible(false), 5000);
    return () => {
      if (intentTimerRef.current !== null) window.clearTimeout(intentTimerRef.current);
    };
  }, [lastIntent]);

  // Auto-grow：先复位到 auto（消除上一次多行残留的高度），再按内容展开，
  // 夹在 40–110px。空内容时 scrollHeight≈内容高，受 CSS min-height:40px 约束回到紧凑态。
  useEffect(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = 'auto';
    const target = Math.min(Math.max(el.scrollHeight, 40), 110);
    el.style.height = `${target}px`;
  }, [text]);

  const submit = (): void => {
    const trimmed = text.trim();
    if (!trimmed) return;
    onSend(trimmed);
    setText('');
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>): void => {
    if (e.key !== 'Enter') return;
    if (e.shiftKey) return; // Shift+Enter 换行
    e.preventDefault();
    if (taskRunning) return; // 任务运行中回车不触发发送
    submit();
  };

  const startRecording = async (): Promise<void> => {
    if (recorderRef.current) return;
    const recorder = new VoiceRecorder(
      (chunk) => onAudioChunk(chunk),
      () => {
        setRecording(false);
        recorderRef.current = null;
        onAudioEnd();
      },
      () => {
        setRecording(false);
        recorderRef.current = null;
      },
    );
    recorderRef.current = recorder;
    setRecording(true);
    await recorder.start();
  };

  const stopRecording = (): void => {
    recorderRef.current?.stop();
  };

  return (
    <div className="input-bar">
      {/* 意图反馈 chip：分类为任务时短暂提示 */}
      {intentVisible && (
        <div className="intent-chip">
          <span className="intent-ic">🛠</span>
          <span className="intent-txt">已作为任务执行（智能体带 skill / agents 全能力）</span>
        </div>
      )}
      <div className="input-wrap">
        <textarea
          ref={textareaRef}
          className="input-box"
          value={text}
          placeholder="和小月聊天，或直接下达任务指令…"
          onChange={(e) => setText(e.target.value)}
          onKeyDown={handleKeyDown}
          disabled={!canSend}
          rows={1}
        />

        {/* 内嵌工具栏：语音 / 发送 */}
        <div className="input-toolbar">
          <div className="tb-spacer" />

          {/* 语音：长按录音（聊天 / 任务统一可用） */}
          <button
            type="button"
            className={`tool-ic ${recording ? 'recording' : ''}`}
            onPointerDown={() => void startRecording()}
            onPointerUp={stopRecording}
            onPointerLeave={recording ? stopRecording : undefined}
            disabled={!canSend}
            title={recording ? '松开结束录音' : '按住录音'}
          >
            <Icon name={recording ? 'micOff' : 'mic'} size={16} />
          </button>

          {/* 发送 / 打断（AI 回复输出或任务运行中变红色打断） */}
          <button
            type="button"
            className={`send ${showInterrupt ? 'interrupt' : ''}`}
            onClick={
              showInterrupt
                ? taskRunning && taskMode
                  ? () => void taskMode.interrupt()
                  : onInterrupt
                : submit
            }
            disabled={!canSend || (!showInterrupt && !text.trim())}
            title={showInterrupt ? (taskRunning ? '中断任务' : '打断 AI 回复') : '发送'}
          >
            <Icon name={showInterrupt ? 'stop' : 'send'} size={18} />
          </button>
        </div>
      </div>

    </div>
  );
}

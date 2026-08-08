import { useRef, useState, type ReactElement } from 'react';
import { VoiceRecorder } from './VoiceRecorder';
import { Icon } from '@/ui/icons';

export interface ChatInputProps {
  onSend: (text: string) => void;
  onAudioChunk: (chunk: Float32Array) => void;
  onAudioEnd: () => void;
  onInterrupt: () => void;
  canSend: boolean;
  /** AI 正在回复输出中：发送按钮临时变为打断按钮。 */
  isReplying: boolean;
}

export function ChatInput({
  onSend,
  onAudioChunk,
  onAudioEnd,
  onInterrupt,
  canSend,
  isReplying,
}: ChatInputProps): ReactElement {
  const [text, setText] = useState('');
  const [recording, setRecording] = useState(false);
  const recorderRef = useRef<VoiceRecorder | null>(null);

  const submit = (): void => {
    const trimmed = text.trim();
    if (!trimmed) return;
    onSend(trimmed);
    setText('');
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLInputElement>): void => {
    if (e.key === 'Enter') submit();
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
    <div className="chat-input-bar">
      <input
        className="chat-input"
        type="text"
        value={text}
        placeholder="输入消息…"
        onChange={(e) => setText(e.target.value)}
        onKeyDown={handleKeyDown}
        disabled={!canSend}
      />

      {/* 动作组：语音 → 发送（AI 回复输出时发送按钮临时变为打断） */}
      <div className="chat-actions">
        <button
          className={`chat-btn mic ${recording ? 'recording' : ''}`}
          onPointerDown={() => void startRecording()}
          onPointerUp={stopRecording}
          onPointerLeave={recording ? stopRecording : undefined}
          disabled={!canSend}
          title={recording ? '松开结束录音' : '按住录音'}
        >
          <Icon name={recording ? 'micOff' : 'mic'} size={16} />
        </button>
        <button
          className={`chat-btn send ${isReplying ? 'interrupt' : ''}`}
          onClick={isReplying ? onInterrupt : submit}
          disabled={!canSend || (!isReplying && !text.trim())}
          title={isReplying ? '打断 AI 回复' : '发送'}
        >
          <Icon name={isReplying ? 'stop' : 'send'} size={16} />
        </button>
      </div>
    </div>
  );
}

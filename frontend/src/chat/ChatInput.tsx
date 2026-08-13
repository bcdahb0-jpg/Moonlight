import { useEffect, useRef, useState, type PointerEvent, type ReactElement } from 'react';
import { VoiceRecorder } from './VoiceRecorder';
import { Icon } from '@/ui/icons';
import type { UseTaskMode } from '@/task/useTaskMode';
import { attachmentsApi, type AttachmentResult } from '@/api/rest';
import { subscribeIntent, type IntentSignal } from '@/state/intentBus';

export interface ChatInputProps {
  onSend: (text: string) => void;
  onAudioChunk: (chunk: Float32Array) => void;
  onAudioEnd: () => void;
  onInterrupt: () => void;
  canSend: boolean;
  workspaceReady?: boolean;
  workspaceHint?: string;
  onPickWorkspace?: () => void;
  /** AI 正在回复输出中：发送按钮临时变为打断按钮。 */
  isReplying: boolean;
  /** 任务控制器：提供运行中打断。仅窗口模式传入。 */
  taskMode?: UseTaskMode;
  /** 功能快捷按钮：打开控制台对应分区（点歌/直播/陪玩/插件/全部）。 */
  onOpenSection?: (section: string) => void;
}

/** 聊天框功能快捷按钮组（P0–P6 常用功能，点击打开控制台对应分区）。 */
const INPUT_FUNCS: Array<{ id: string; icon: 'music' | 'broadcast' | 'gamepad' | 'puzzle' | 'monitor'; label: string; section: string }> = [
  { id: 'sing', icon: 'music', label: '点歌', section: 'entertainment' },
  { id: 'live', icon: 'broadcast', label: '直播', section: 'entertainment' },
  { id: 'playmate', icon: 'gamepad', label: '陪玩', section: 'entertainment' },
  { id: 'plugin', icon: 'puzzle', label: '插件', section: 'task' },
  { id: 'all', icon: 'monitor', label: '全部', section: 'home' },
];

export function ChatInput({
  onSend,
  onAudioChunk,
  onAudioEnd,
  onInterrupt,
  canSend,
  workspaceReady = true,
  workspaceHint,
  onPickWorkspace,
  isReplying,
  taskMode,
  onOpenSection,
}: ChatInputProps): ReactElement {
  const [text, setText] = useState('');
  const [recording, setRecording] = useState(false);
  const [isSending, setIsSending] = useState(false);
  const [intentVisible, setIntentVisible] = useState(false);
  const intentTimerRef = useRef<number | null>(null);
  const recorderRef = useRef<VoiceRecorder | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);
  const sendTimerRef = useRef<number | null>(null);
  const fileRef = useRef<HTMLInputElement | null>(null);

  // P5.1 聊天附件：待发送的附件摘要（上传后展示 chip，可移除）。
  const [pendingAttachments, setPendingAttachments] = useState<{ name: string; kind: string; summary: string }[]>([]);
  const [uploading, setUploading] = useState(false);
  // P5.1 意图徽标：intent-event → 「意图: 勿扰 · 情绪: anger」chip（2.5s 消失）。
  const [intentSignal, setIntentSignal] = useState<IntentSignal | null>(null);
  const intentChipTimerRef = useRef<number | null>(null);

  useEffect(() => {
    const unsub = subscribeIntent((sig) => {
      setIntentSignal(sig);
      if (intentChipTimerRef.current !== null) window.clearTimeout(intentChipTimerRef.current);
      intentChipTimerRef.current = window.setTimeout(() => setIntentSignal(null), 2500);
    });
    return () => {
      unsub();
      if (intentChipTimerRef.current !== null) window.clearTimeout(intentChipTimerRef.current);
    };
  }, []);

  useEffect(() => {
    const stopOnBlur = (): void => {
      if (recorderRef.current) stopRecording();
    };
    window.addEventListener('blur', stopOnBlur);
    return () => {
      window.removeEventListener('blur', stopOnBlur);
      if (sendTimerRef.current !== null) window.clearTimeout(sendTimerRef.current);
    };
  }, []);

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
  // 夹在 40–110px。空内容时直接回紧凑态——不读 scrollHeight：
  // 浏览器在 value 为空时会把 placeholder 的渲染高度算进 scrollHeight
  // （长 placeholder 在首帧宽度未定时会换行），曾被撑到 max-height 110px，
  // 输入一个字符后 placeholder 消失又突然缩回，表现为“首次打开 UI 异常变大”。
  useEffect(() => {
    const el = textareaRef.current;
    if (!el) return;
    if (!text) {
      el.style.height = '40px';
      return;
    }
    el.style.height = 'auto';
    const target = Math.min(Math.max(el.scrollHeight, 40), 110);
    el.style.height = `${target}px`;
  }, [text]);

  const submit = (): void => {
    const trimmed = text.trim();
    if ((!trimmed && pendingAttachments.length === 0) || isSending || taskRunning || !workspaceReady) return;
    setIsSending(true);
    // P5.1 附件：把已处理摘要文本拼入消息（PDF 抽取内容 / 图片描述）——
    // 附件内容作为上下文进对话，conversations 零改动（优雅降级闭环）。
    const attachBlock = pendingAttachments.map((a) => a.summary).join('\n\n');
    const finalText = attachBlock ? `${attachBlock}\n\n${trimmed}` : trimmed;
    onSend(finalText);
    setText('');
    setPendingAttachments([]);
    // onSend is intentionally void (it routes through the websocket state
    // machine). A short lock still prevents double-clicks before that state
    // machine can publish its next state.
    sendTimerRef.current = window.setTimeout(() => setIsSending(false), 350);
    textareaRef.current?.focus();
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>): void => {
    if (e.key !== 'Enter') return;
    if (e.shiftKey) return; // Shift+Enter 换行
    e.preventDefault();
    if (taskRunning) return; // 任务运行中回车不触发发送
    submit();
  };

  const startRecording = async (): Promise<void> => {
    if (recorderRef.current || !canSend || isSending) return;
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

  // P5.1 附件：上传 → 后端降级处理（PDF 抽取 / 图片描述）→ chip 展示。
  const handleFiles = (files: FileList | File[]): void => {
    const list = Array.from(files).slice(0, 5);
    for (const file of list) {
      setUploading(true);
      void attachmentsApi
        .upload(file)
        .then((res: AttachmentResult) => {
          setPendingAttachments((prev) => [
            ...prev,
            { name: res.name, kind: res.kind, summary: res.summary },
          ]);
        })
        .catch(() =>
          setPendingAttachments((prev) => [
            ...prev,
            { name: file.name, kind: 'error', summary: `【附件「${file.name}」】上传/处理失败（后端未重启？）` },
          ]),
        )
        .finally(() => setUploading(false));
    }
  };

  const onDrop = (e: React.DragEvent): void => {
    e.preventDefault();
    if (!canSend || !workspaceReady) return;
    handleFiles(e.dataTransfer.files);
  };

  const capturePointer = (event: PointerEvent<HTMLButtonElement>): void => {
    try { event.currentTarget.setPointerCapture(event.pointerId); } catch { /* unsupported */ }
    void startRecording();
  };

  const releasePointer = (event: PointerEvent<HTMLButtonElement>): void => {
    try {
      if (event.currentTarget.hasPointerCapture(event.pointerId)) {
        event.currentTarget.releasePointerCapture(event.pointerId);
      }
    } catch { /* unsupported */ }
    stopRecording();
  };

  return (
    <div className="input-bar">
      {!workspaceReady && workspaceHint && (
        <div className="input-workspace-hint" role="status">
          <span>{workspaceHint}</span>
          {onPickWorkspace && <button type="button" onClick={onPickWorkspace}>选择工作目录</button>}
        </div>
      )}
      {/* 意图反馈 chip：分类为任务时短暂提示 */}
      {intentVisible && (
        <div className="intent-chip">
          <span className="intent-ic">🛠</span>
          <span className="intent-txt">已作为任务执行（智能体带 skill / agents 全能力）</span>
        </div>
      )}
      {/* P5.1 意图徽标：intent-event 实时显示（勿扰/闲聊/任务 + 情绪） */}
      {intentSignal && (
        <div className="intent-chip" style={{ borderColor: intentSignal.intent === 'silence' ? 'rgba(251,191,36,0.5)' : undefined }}>
          <span className="intent-ic">
            {intentSignal.intent === 'silence' ? '🤫' : intentSignal.intent === 'task' ? '🛠' : '💬'}
          </span>
          <span className="intent-txt">
            意图: {intentSignal.intent === 'silence' ? '勿扰' : intentSignal.intent === 'task' ? '任务' : '闲聊'} · 情绪: {intentSignal.emotion}
          </span>
        </div>
      )}
      {/* P5.1 附件 chip：上传处理后的摘要，发送时拼入消息 */}
      {pendingAttachments.length > 0 && (
        <div className="attach-row">
          {pendingAttachments.map((a, i) => (
            <span key={`${a.name}-${i}`} className="attach-chip" title={a.summary}>
              {a.kind === 'pdf' ? '📄' : a.kind === 'image' ? '🖼️' : '📎'} {a.name}
              <button
                type="button"
                className="attach-remove"
                aria-label="移除附件"
                onClick={() => setPendingAttachments((prev) => prev.filter((_, j) => j !== i))}
              >
                ×
              </button>
            </span>
          ))}
          {uploading && <span className="attach-chip attach-loading">处理中…</span>}
        </div>
      )}
      <div className="input-wrap" onDrop={onDrop} onDragOver={(e) => e.preventDefault()}>
        <div className="input-route-hint" role="note">
          <span className="input-route-dot" aria-hidden />
          <span>消息将自动识别为聊天或任务</span>
          {taskRunning && <strong>当前任务运行中 · 发送键用于停止</strong>}
        </div>
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

        {/* 内嵌工具栏：功能快捷 / 附件 / 语音 / 发送 */}
        <div className="input-toolbar">
          {/* P6 UI 重构：功能组件快捷入口（点歌/直播/陪玩/插件/全部） */}
          <div className="tb-funcs">
            {INPUT_FUNCS.map((f) => (
              <button
                type="button"
                key={f.id}
                className="tool-ic tb-func"
                onClick={() => onOpenSection?.(f.section)}
                title={f.label}
              >
                <Icon name={f.icon} size={15} />
              </button>
            ))}
          </div>
          <div className="tb-spacer" />

          {/* P5.1 附件：图片 / PDF / 音频（上传后降级处理进上下文） */}
          <button
            type="button"
            className="tool-ic"
            onClick={() => fileRef.current?.click()}
            disabled={!canSend || !workspaceReady || uploading}
            title="添加附件（图片 / PDF / 音频）"
          >
            <Icon name="attach" size={16} />
          </button>
          <input
            ref={fileRef}
            type="file"
            accept=".pdf,image/*,audio/*"
            multiple
            style={{ display: 'none' }}
            onChange={(e) => {
              if (e.target.files) handleFiles(e.target.files);
              e.target.value = '';
            }}
          />

          {/* 语音：长按录音（聊天 / 任务统一可用） */}
          <button
            type="button"
            className={`tool-ic ${recording ? 'recording' : ''}`}
            onPointerDown={capturePointer}
            onPointerUp={releasePointer}
            onPointerCancel={releasePointer}
            onLostPointerCapture={stopRecording}
            disabled={!canSend || !workspaceReady || isSending}
            aria-pressed={recording}
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
            disabled={!canSend || !workspaceReady || (!showInterrupt && (!text.trim() || isSending))}
            title={showInterrupt ? (taskRunning ? '中断任务' : '打断 AI 回复') : '发送'}
          >
            <Icon name={showInterrupt ? 'stop' : 'send'} size={18} />
          </button>
        </div>
      </div>

    </div>
  );
}

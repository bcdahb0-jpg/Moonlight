import type { ReactElement } from 'react';
import type { ChatMessage } from '@/state/types';
import type { ErrorCode } from '@/types/ws';
import { MessageList, type TaskRunPlacement } from './MessageList';
import { ChatInput } from './ChatInput';
import { ErrorBanner } from '@/components/ErrorBanner';
import type { UseTaskMode } from '@/task/useTaskMode';

function IconMoon(): ReactElement {
  return (
    <svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
      <path d="M20.5 14.5A8.5 8.5 0 1 1 9.5 3.5a7 7 0 0 0 11 11z" />
    </svg>
  );
}

function IconFolder(): ReactElement {
  return (
    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <path d="M3 6.5A1.5 1.5 0 0 1 4.5 5h4l2 2.5h9A1.5 1.5 0 0 1 21 9v8.5a1.5 1.5 0 0 1-1.5 1.5h-15A1.5 1.5 0 0 1 3 17.5z" />
    </svg>
  );
}

export interface ChatPanelProps {
  messages: ChatMessage[];
  isThinking: boolean;
  subtitle: string;
  /** 聊天 agent 工具执行状态文案（tool_call_status；空 = 无）。 */
  toolStatus?: string | null;
  connected: boolean;
  /** 结构化错误修复卡（Phase 3）：错误不再注入聊天流，改横幅。 */
  lastError: string | null;
  errorCode: ErrorCode | null;
  onDismissError: () => void;
  onOpenSettingsSection?: (section: string) => void;
  onSend: (text: string) => void;
  onAudioChunk: (chunk: Float32Array) => void;
  onAudioEnd: () => void;
  onInterrupt: () => void;
  /** 任务模式：透传给 MessageList（卡片）与 ChatInput（下拉/打断）。 */
  taskMode?: UseTaskMode;
  /** 任务执行卡（按 run 分散内联到各自锚点消息之后；anchorId=null → 末尾兜底）。 */
  taskRuns?: TaskRunPlacement[];
  /** v5：无激活会话时显示「选择工作目录」引导空态（替代聊天空态）。 */
  showWorkspaceGuide?: boolean;
  onPickWorkspace?: () => void;
  /** 气泡「再次播放」回调（2026-08-10）：透传给 MessageList。 */
  onReplayAudio?: (message: ChatMessage) => void;
}

export function ChatPanel({
  messages,
  isThinking,
  subtitle,
  toolStatus = null,
  connected,
  lastError,
  errorCode,
  onDismissError,
  onOpenSettingsSection,
  onSend,
  onAudioChunk,
  onAudioEnd,
  onInterrupt,
  taskMode,
  taskRuns,
  showWorkspaceGuide = false,
  onPickWorkspace,
  onReplayAudio,
}: ChatPanelProps): ReactElement {
  const showGuide = showWorkspaceGuide && messages.length === 0;
  return (
    <div className="chat-panel">
      <ErrorBanner
        message={lastError}
        code={errorCode}
        onDismiss={onDismissError}
        onOpenSection={onOpenSettingsSection}
      />
      {taskMode?.error && (
        <div className="task-error">{taskMode.error}</div>
      )}
      {showGuide ? (
        <div className="ws-guide">
          <div className="ws-guide-mark">
            <IconMoon />
          </div>
          <div className="ws-guide-title">选择工作目录，开始第一个会话</div>
          <div className="ws-guide-sub">会话与工作目录绑定 · 任务将直接在目录下执行</div>
          <button className="ws-guide-btn" onClick={onPickWorkspace}>
            <IconFolder />
            选择工作目录
          </button>
        </div>
      ) : (
        <MessageList
          messages={messages}
          isThinking={isThinking}
          subtitle={subtitle}
          toolStatus={toolStatus}
          taskRuns={taskRuns}
          // UX 修复：任务执行卡在跑时隐藏聊天 agent 工具状态条（避免双「执行中」）
          hideToolStatus={taskMode?.isRunning ?? false}
          onReplayAudio={onReplayAudio}
        />
      )}
      <ChatInput
        onSend={onSend}
        onAudioChunk={onAudioChunk}
        onAudioEnd={onAudioEnd}
        onInterrupt={onInterrupt}
        canSend={connected}
        isReplying={isThinking}
        taskMode={taskMode}
      />
    </div>
  );
}

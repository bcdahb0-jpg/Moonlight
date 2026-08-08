import type { ReactElement } from 'react';
import type { ChatMessage } from '@/state/types';
import type { ErrorCode } from '@/types/ws';
import { MessageList } from './MessageList';
import { ChatInput } from './ChatInput';
import { ErrorBanner } from '@/components/ErrorBanner';

export interface ChatPanelProps {
  messages: ChatMessage[];
  isThinking: boolean;
  subtitle: string;
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
}

export function ChatPanel({
  messages,
  isThinking,
  subtitle,
  connected,
  lastError,
  errorCode,
  onDismissError,
  onOpenSettingsSection,
  onSend,
  onAudioChunk,
  onAudioEnd,
  onInterrupt,
}: ChatPanelProps): ReactElement {
  return (
    <div className="chat-panel">
      <ErrorBanner
        message={lastError}
        code={errorCode}
        onDismiss={onDismissError}
        onOpenSection={onOpenSettingsSection}
      />
      <MessageList messages={messages} isThinking={isThinking} subtitle={subtitle} />
      <ChatInput
        onSend={onSend}
        onAudioChunk={onAudioChunk}
        onAudioEnd={onAudioEnd}
        onInterrupt={onInterrupt}
        canSend={connected}
        isReplying={isThinking}
      />
    </div>
  );
}

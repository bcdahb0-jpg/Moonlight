import type { ReactElement } from 'react';
import type { ChatMessage } from '@/state/types';
import { useTypewriter } from './useTypewriter';
import { Icon } from '@/ui/icons';

export interface ChatBubbleProps {
  message: ChatMessage;
}

export function ChatBubble({ message }: ChatBubbleProps): ReactElement {
  const isUser = message.role === 'user';
  const isStreaming = message.streaming ?? false;
  const { visible, done } = useTypewriter(isStreaming ? message.text : '', { speedMs: 14 });

  const content = isStreaming && !done ? visible : message.text;
  const avatar = message.avatar ? (
    <img className="chat-avatar-img" src={message.avatar} alt="" />
  ) : (
    <Icon name="moon" size={16} />
  );

  return (
    <div className={`chat-bubble-row ${isUser ? 'user' : 'ai'}`}>
      {!isUser && <div className="chat-avatar">{avatar}</div>}
      <div className={`chat-bubble ${isUser ? 'user' : 'ai'}`}>
        {!isUser && message.name && <div className="chat-bubble-name">{message.name}</div>}
        <div className="chat-bubble-text">
          {content}
          {isStreaming && !done && <span className="chat-caret" />}
        </div>
        {/* 字幕翻译：显示层小字（原文下方），不影响记忆/历史 */}
        {!isUser && message.subtitle && (
          <div className="chat-bubble-subtitle">{message.subtitle}</div>
        )}
      </div>
      {isUser && (
        <div className="chat-avatar user">
          <Icon name="user" size={16} />
        </div>
      )}
    </div>
  );
}

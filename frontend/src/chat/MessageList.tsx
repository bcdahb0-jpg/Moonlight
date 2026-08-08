import { useEffect, useRef, type ReactElement } from 'react';
import type { ChatMessage } from '@/state/types';
import { ChatBubble } from './ChatBubble';
import { Icon } from '@/ui/icons';

export interface MessageListProps {
  messages: ChatMessage[];
  isThinking: boolean;
  subtitle: string;
}

export function MessageList({ messages, isThinking, subtitle }: MessageListProps): ReactElement {
  const scrollRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [messages, isThinking, subtitle]);

  const empty = messages.length === 0 && !isThinking && !subtitle;

  return (
    <div className="message-list" ref={scrollRef}>
      {empty && <div className="chat-empty">和 Moonlight 打个招呼吧～</div>}
      {messages.map((msg) => (
        <ChatBubble key={msg.id} message={msg} />
      ))}
      {isThinking && (
        <div className="chat-bubble-row ai">
          <div className="chat-avatar">
            <Icon name="moon" size={16} />
          </div>
          <div className="chat-bubble ai">
            <div className="chat-bubble-name">Moonlight</div>
            <div className="chat-thinking">
              <span className="dot" />
              <span className="dot" />
              <span className="dot" />
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

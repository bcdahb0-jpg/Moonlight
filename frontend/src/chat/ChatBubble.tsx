import type { ReactElement } from 'react';
import type { ChatMessage } from '@/state/types';
import { useAppState } from '@/state/AppStateContext';
import { useTypewriter } from './useTypewriter';
import { Icon } from '@/ui/icons';
import { Markdown } from '@/components/Markdown';
import { TaskBriefCard } from './TaskBriefCard';

export interface ChatBubbleProps {
  message: ChatMessage;
  /** 气泡「再次播放」回调（2026-08-10）：消息带语音数据时显示重播按钮。 */
  onReplayAudio?: (message: ChatMessage) => void;
}

export function ChatBubble({ message, onReplayAudio }: ChatBubbleProps): ReactElement {
  const isUser = message.role === 'user';
  const isStreaming = message.streaming ?? false;
  // UX 修复（2026-08-10）：双语气泡开关必须是前端渲染闸门——消息里带了
  // subtitle 不等于要显示，开关关掉就一律不渲染（修复"有些有有些没有"）。
  const { state } = useAppState();
  const showSubtitle = state.settings.subtitleEnabled;
  const { visible, done } = useTypewriter(isStreaming ? message.text : '', { speedMs: 14 });

  // 2026-08-10：任务简报 → 折叠卡片渲染，不占用普通气泡的语音/字幕/打字机链路。
  // 后端写入带 kind="task_brief"；旧数据无该字段时按内容前缀兜底识别。
  // 注意：所有 hooks 已在上方调用完毕，此处 early return 不违反 hooks 顺序规则。
  const isBrief =
    message.kind === 'task_brief' || message.text.startsWith('【任务简报】');
  if (isBrief) {
    return (
      <div className="chat-bubble-row brief">
        <div className="chat-bubble brief">
          <TaskBriefCard message={message} />
        </div>
      </div>
    );
  }

  // P3 展示统一：AI 气泡渲染 Markdown（代码/列表/表格）。流式输出中保持
  // 纯文本打字机（避免半截 Markdown 语法闪烁），播完后切 Markdown 渲染。
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
          {!isUser && !isStreaming ? (
            <Markdown content={content} />
          ) : (
            <>
              {content}
              {isStreaming && !done && <span className="chat-caret" />}
            </>
          )}
        </div>
        {/* 字幕翻译：显示层小字（原文下方），不影响记忆/历史。
            前端渲染闸门：settings.subtitleEnabled 为 false 时一律不显示，
            保证开关状态全链路一致（开则全有、关则全无）。 */}
        {!isUser && showSubtitle && message.subtitle && (
          <div className="chat-bubble-subtitle">{message.subtitle}</div>
        )}
        {/* 再次播放：消息带语音数据时显示（默认隐藏，hover 气泡出现）。
            位置：气泡框右侧中部（垂直居中、不占内容行，避免右下角贴空行）。
            后端 wav 播完即删，前端保存的 base64 快照用于重播。 */}
        {!isUser && message.audioData && onReplayAudio && (
          <button
            type="button"
            className="chat-bubble-replay"
            title="再次播放语音"
            aria-label="再次播放语音"
            onClick={() => onReplayAudio(message)}
          >
            <Icon name="volume" size={13} />
          </button>
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

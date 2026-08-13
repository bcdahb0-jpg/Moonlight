import { Fragment, useEffect, useMemo, useRef, useState, type ReactElement } from 'react';
import type { ChatMessage } from '@/state/types';
import { useAppState } from '@/state/AppStateContext';
import { ChatBubble } from './ChatBubble';
import { Icon } from '@/ui/icons';

/** 任务执行卡的就位信息：每个 run 一张卡，插到各自 anchorId 消息之后。 */
export interface TaskRunPlacement {
  runId: string;
  /** 卡片内联到该消息之后；null = 渲染在消息流末尾兜底（Modal 新建/锚点已消失）。 */
  anchorId: string | null;
  card: ReactElement;
}

export interface MessageListProps {
  messages: ChatMessage[];
  isThinking: boolean;
  subtitle: string;
  /** 聊天 agent 工具执行状态文案（tool_call_status；空 = 无）。 */
  toolStatus?: string | null;
  /** 任务执行卡（2026-08-10 v5：按 run 分散内联到各自锚点消息之后，
   *  历史 run 的卡片不会跟随新 run 移动——修复"两张任务卡堆一起"）。 */
  taskRuns?: TaskRunPlacement[];
  /** UX 修复（2026-08-10）：任务执行卡在跑时隐藏 tool-status-row，
   *  避免两个「执行中」指示并存。由上层按任务运行状态传入。 */
  hideToolStatus?: boolean;
  /** 气泡「再次播放」回调（2026-08-10）：透传给 ChatBubble。 */
  onReplayAudio?: (message: ChatMessage) => void;
}

export function MessageList({
  messages,
  isThinking,
  subtitle,
  toolStatus = null,
  taskRuns,
  hideToolStatus = false,
  onReplayAudio,
}: MessageListProps): ReactElement {
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const [showJumpLatest, setShowJumpLatest] = useState(false);
  // UX 修复（2026-08-10）：thinking 气泡名字跟随角色（与 AI 消息的
  // confName 来源一致），不再硬编码产品名，消除「到底谁在说话」的困惑。
  const { state } = useAppState();
  const thinkingName = state.characterName || state.confName || '角色';

  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    // Respect manual scrolling. Auto-follow only while the user is already
    // near the bottom, and coalesce rapid stream updates into one layout pass.
    const nearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 96;
    setShowJumpLatest(!nearBottom);
    if (!nearBottom) return;
    const id = window.requestAnimationFrame(() => {
      if (scrollRef.current) scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    });
    return () => window.cancelAnimationFrame(id);
  }, [messages, isThinking, subtitle, toolStatus, taskRuns]);

  const { byAnchor, orphanRuns } = useMemo(() => {
    const ids = new Set(messages.map((m) => m.id));
    const byAnchor = new Map<string, TaskRunPlacement[]>();
    const orphanRuns: TaskRunPlacement[] = [];
    for (const run of taskRuns ?? []) {
      if (run.anchorId && ids.has(run.anchorId)) {
        const list = byAnchor.get(run.anchorId) ?? [];
        list.push(run);
        byAnchor.set(run.anchorId, list);
      } else orphanRuns.push(run);
    }
    return { byAnchor, orphanRuns };
  }, [messages, taskRuns]);

  const hasCards = (taskRuns?.length ?? 0) > 0;
  // 锚点无效的 run（null / 消息已不在本会话流中）→ 渲染在消息流末尾兜底。
  const showToolStatus = toolStatus && !(hideToolStatus && hasCards);

  const empty =
    messages.length === 0 &&
    !isThinking &&
    !subtitle &&
    !showToolStatus &&
    !hasCards;

  const renderCard = (r: TaskRunPlacement): ReactElement => (
    <div className="chat-bubble-row task" key={r.runId}>
      {r.card}
    </div>
  );

  return (
    <div className="message-list" ref={scrollRef} onScroll={(e) => {
      const el = e.currentTarget;
      setShowJumpLatest(el.scrollHeight - el.scrollTop - el.clientHeight >= 96);
    }}>
      {empty && <div className="chat-empty">和 Moonlight 打个招呼吧～</div>}
      {messages.map((msg) => (
        <Fragment key={msg.id}>
          <ChatBubble message={msg} onReplayAudio={onReplayAudio} subtitleEnabled={state.settings.subtitleEnabled} />
          {/* 2026-08-10 v5：按 run 分散内联到各自锚点消息之后（与请求保持上下文） */}
          {(byAnchor.get(msg.id) ?? []).map(renderCard)}
        </Fragment>
      ))}
      {/* 无锚点（Modal 新建/刷新恢复）或锚点消息已不在本会话 → 末尾兜底 */}
      {orphanRuns.map(renderCard)}
      {showToolStatus && (
        <div className="tool-status-row">
          <span className="tool-status-spinner" />
          <span>{toolStatus}</span>
        </div>
      )}
      {isThinking && (
        <div className="chat-bubble-row ai">
          <div className="chat-avatar">
            <Icon name="moon" size={16} />
          </div>
          <div className="chat-bubble ai">
            <div className="chat-bubble-name">{thinkingName}</div>
            <div className="chat-thinking">
              <span className="dot" />
              <span className="dot" />
              <span className="dot" />
            </div>
          </div>
        </div>
      )}
      {showJumpLatest && (
        <button type="button" className="jump-latest" onClick={() => {
          const el = scrollRef.current;
          if (!el) return;
          el.scrollTo({ top: el.scrollHeight, behavior: 'smooth' });
          setShowJumpLatest(false);
        }}>
          <Icon name="chevronDown" size={14} /> 最新消息
        </button>
      )}
    </div>
  );
}

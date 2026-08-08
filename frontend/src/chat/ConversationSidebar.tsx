/**
 * ConversationSidebar — 窗口模式的会话管理侧边栏（暗夜月光 · 玻璃）。
 *
 * 设计参考：ChatGPT Next Web / LobeChat 的可折叠会话列表范式——
 * 展开 232px 玻璃面板（标题 + 新建 + 列表 + 折叠），收起后左上角保留悬浮展开按钮。
 * 折叠偏好写入 localStorage（moonlight.sidebar.collapsed），下次启动保持。
 *
 * 会话标题：
 * - 默认取「首条用户消息前 10 字」（后端自动生成，见 chat_history_manager）；
 * - 可自定义：hover 出现「编辑」按钮或双击条目 → 行内输入框 → Enter/失焦保存、Esc 取消；
 * - 显示长度随容器宽度自适应（ellipsis 截断），完整标题见 tooltip。
 */
import { useEffect, useRef, useState, type ReactElement } from 'react';
import type { HistoryEntry } from '@/state/types';
import { Icon } from '@/ui/icons';

export interface ConversationSidebarProps {
  historyList: HistoryEntry[];
  currentHistoryUid: string | null;
  onFetchHistory: () => void;
  onLoadHistory: (uid: string) => void;
  onNewHistory: () => void;
  onDeleteHistory: (uid: string) => void;
  /** 自定义会话标题。 */
  onRename: (uid: string, title: string) => void;
}

const COLLAPSED_KEY = 'moonlight.sidebar.collapsed';

export function ConversationSidebar({
  historyList,
  currentHistoryUid,
  onFetchHistory,
  onLoadHistory,
  onNewHistory,
  onDeleteHistory,
  onRename,
}: ConversationSidebarProps): ReactElement {
  const [open, setOpen] = useState<boolean>(() => {
    try {
      return localStorage.getItem(COLLAPSED_KEY) !== '1';
    } catch {
      return true;
    }
  });

  // 行内重命名状态：正在编辑的 uid + 草稿
  const [editingUid, setEditingUid] = useState<string | null>(null);
  const [draft, setDraft] = useState('');
  const inputRef = useRef<HTMLInputElement | null>(null);

  // 折叠偏好持久化
  useEffect(() => {
    try {
      localStorage.setItem(COLLAPSED_KEY, open ? '0' : '1');
    } catch {
      /* localStorage 不可用时静默 */
    }
  }, [open]);

  // 展开时拉取最新会话列表
  useEffect(() => {
    if (open) onFetchHistory();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  const startRename = (uid: string, current: string): void => {
    setEditingUid(uid);
    setDraft(current);
    requestAnimationFrame(() => inputRef.current?.select());
  };

  const cancelRename = (): void => {
    setEditingUid(null);
    setDraft('');
  };

  const commitRename = (uid: string, original: string): void => {
    const trimmed = draft.trim();
    setEditingUid(null);
    setDraft('');
    if (trimmed && trimmed !== original) onRename(uid, trimmed);
  };

  return (
    <div className={`conv-sidebar-wrap ${open ? 'open' : 'closed'}`}>
      <aside className="conv-sidebar" aria-label="会话列表">
        <header className="conv-sidebar-header">
          <span className="conv-sidebar-title">
            <Icon name="history" size={14} />
            会话
          </span>
          <div className="conv-sidebar-actions">
            <button className="conv-icon-btn" onClick={onNewHistory} title="新建对话">
              <Icon name="plus" size={15} />
            </button>
            <button className="conv-icon-btn" onClick={() => setOpen(false)} title="收起侧边栏">
              <Icon name="panelLeftClose" size={15} />
            </button>
          </div>
        </header>

        <div className="conv-sidebar-list">
          {historyList.length === 0 ? (
            <div className="conv-sidebar-empty">还没有历史会话</div>
          ) : (
            historyList.map((h) => {
              const uid = historyUid(h);
              const label = historyTitle(h);
              const active = uid === currentHistoryUid;
              if (editingUid === uid) {
                return (
                  <div
                    key={uid}
                    className="conv-item conv-item-editing"
                    onClick={(e) => e.stopPropagation()}
                  >
                    <input
                      ref={inputRef}
                      className="conv-item-edit-input"
                      value={draft}
                      maxLength={30}
                      placeholder="会话名称"
                      onChange={(e) => setDraft(e.target.value)}
                      onKeyDown={(e) => {
                        if (e.key === 'Enter') commitRename(uid, label);
                        else if (e.key === 'Escape') cancelRename();
                      }}
                      onBlur={() => commitRename(uid, label)}
                      onClick={(e) => e.stopPropagation()}
                    />
                  </div>
                );
              }
              return (
                <div
                  key={uid}
                  className={`conv-item ${active ? 'active' : ''}`}
                  onClick={() => onLoadHistory(uid)}
                  onDoubleClick={() => startRename(uid, label)}
                  title={label}
                >
                  <span className="conv-item-title">{label}</span>
                  <button
                    className="conv-item-edit"
                    onClick={(e) => {
                      e.stopPropagation();
                      startRename(uid, label);
                    }}
                    title="重命名会话"
                  >
                    <Icon name="edit" size={12} />
                  </button>
                  <button
                    className="conv-item-del"
                    onClick={(e) => {
                      e.stopPropagation();
                      onDeleteHistory(uid);
                    }}
                    title="删除会话"
                  >
                    <Icon name="trash" size={13} />
                  </button>
                </div>
              );
            })
          )}
        </div>
      </aside>

      {!open && (
        <button className="conv-rail" onClick={() => setOpen(true)} title="展开会话侧边栏">
          <Icon name="panelLeftOpen" size={16} />
        </button>
      )}
    </div>
  );
}

function historyUid(entry: HistoryEntry): string {
  return String(entry.uid ?? entry.history_uid ?? '');
}

/** 标题优先取后端 title（自定义或自动生成），缺失时回退时间戳/uid。 */
function historyTitle(entry: HistoryEntry): string {
  const title = entry.title as string | undefined;
  if (title && title.trim()) return title;
  return formatHistoryLabel(entry);
}

function formatHistoryLabel(entry: HistoryEntry): string {
  const name = (entry as { name?: string }).name;
  const ts = entry.created_at as number | string | undefined;
  if (name) return String(name);
  if (typeof ts === 'number') return new Date(ts).toLocaleString();
  return historyUid(entry);
}

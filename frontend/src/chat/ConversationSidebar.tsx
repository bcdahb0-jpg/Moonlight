/**
 * ConversationSidebar — 窗口模式的会话管理侧边栏（重设计 v5 · 工作区模型）。
 *
 * 会话不再平铺，而是按「工作目录」分组（一级目录组 → 二级会话）：
 *   ┌ 工作区 ──────────────┐
 *   │ ＋ 新建会话（选目录）   │
 *   │ 📁 Moonlight      (3) │   ← 组头：目录末段 + 会话数 + hover「＋」直接在此新建
 *   │   设置界面重设计      │   ← 会话条目（切换/重命名/删除/移动）
 *   │   今晚吃什么          │
 *   │ 📁 downloads      (1) │
 *   │   ...
 *   │ ⚠ 清理 N 个旧版会话    │   ← 存量无目录会话的一次性清理条（用户已确认清空策略）
 *   └─────────────────────┘
 *
 * 新建会话：顶部 ＋ → 目录选择弹层（输入路径 / 最近目录 / Electron 原生浏览）。
 * 移动会话：条目 hover 出现「移动」→ 同一个目录选择弹层。
 * 折叠偏好写入 localStorage（moonlight.sidebar.collapsed），组展开态默认全开。
 */
import { useEffect, useMemo, useRef, useState, type ReactElement } from 'react';
import type { HistoryEntry } from '@/state/types';
import { Icon } from '@/ui/icons';

export interface ConversationSidebarProps {
  historyList: HistoryEntry[];
  currentHistoryUid: string | null;
  onFetchHistory: () => void;
  onLoadHistory: (uid: string) => void;
  /** v5：新建会话必须绑定工作目录。 */
  onCreateHistory: (workspace: string) => void;
  onDeleteHistory: (uid: string) => void;
  /** 自定义会话标题。 */
  onRename: (uid: string, title: string) => void;
  /** v5：把会话移动到另一个工作目录。 */
  onMoveHistory: (uid: string, workspace: string) => void;
  /** v5：清空全部存量会话（调用方负责确认弹窗）。 */
  onClearAllHistories: () => void;
}

const COLLAPSED_KEY = 'moonlight.sidebar.collapsed';
/** 最近使用的工作目录（localStorage，新建/移动成功后追加）。 */
const RECENT_WORKSPACES_KEY = 'moonlight.workspaces.recent';

/** 任务 chip 图标（内联 SVG：扳手轮廓） */
function TaskChipIcon(): ReactElement {
  return (
    <svg width="9" height="9" viewBox="0 0 10 10" aria-hidden="true">
      <path
        d="M5.6 1.3a3 3 0 0 1 3.6 3.6l-1.2-1.2-1.3 1.3 1.2 1.2a3 3 0 0 1-3.6 3.6l1-1.6L3.2 7l-1-1.6z"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.1"
        strokeLinejoin="round"
      />
    </svg>
  );
}

/** 失败 chip 图标（内联 SVG：警告三角） */
function ErrorChipIcon(): ReactElement {
  return (
    <svg width="9" height="9" viewBox="0 0 10 10" aria-hidden="true">
      <path d="M5 1.2L9.2 8.4H.8Z" fill="none" stroke="currentColor" strokeWidth="1.1" strokeLinejoin="round" />
      <path d="M5 3.8v2.1" fill="none" stroke="currentColor" strokeWidth="1.1" strokeLinecap="round" />
      <circle cx="5" cy="7.2" r="0.7" fill="currentColor" />
    </svg>
  );
}

/** 会话条目状态 chips：有任务/失败计数才渲染；纯聊不显示（视觉零噪声）。 */
function HistoryChips({ entry }: { entry: HistoryEntry }): ReactElement | null {
  const taskCount = Number(entry.task_count ?? 0);
  const failedCount = Number(entry.failed_count ?? 0);
  if (taskCount <= 0 && failedCount <= 0) return null;
  return (
    <span className="conv-item-chips">
      {taskCount > 0 && (
        <span className="conv-chip task" title={`${taskCount} 个任务`}>
          <TaskChipIcon />
          {taskCount}
        </span>
      )}
      {failedCount > 0 && (
        <span className="conv-chip error" title={`${failedCount} 个失败任务`}>
          <ErrorChipIcon />
          {failedCount}
        </span>
      )}
    </span>
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

/** 目录末段（\ 与 / 均支持）；根路径显示原样。 */
function baseName(workspace: string): string {
  const parts = workspace.split(/[\\/]/).filter(Boolean);
  return parts.length > 1 ? parts[parts.length - 1] : workspace;
}

export function ConversationSidebar({
  historyList,
  currentHistoryUid,
  onFetchHistory,
  onLoadHistory,
  onCreateHistory,
  onDeleteHistory,
  onRename,
  onMoveHistory,
  onClearAllHistories,
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

  // v5：目录选择弹层（mode = 新建 / 移动某个会话）
  const [picker, setPicker] = useState<{ mode: 'create'; recent: string[] } | { mode: 'move'; uid: string; recent: string[] } | null>(null);

  // 组展开态（默认全开，仅内存态）
  const [collapsedGroups, setCollapsedGroups] = useState<Set<string>>(new Set());

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

  // v5：两级分组 —— 目录组（含「未绑定」兜底）+ 组内会话（时间倒序）
  const groups = useMemo(() => {
    const map = new Map<string, HistoryEntry[]>();
    for (const h of historyList) {
      const ws = String(h.workspace ?? '').trim();
      const key = ws || '\u0000unbound';
      if (!map.has(key)) map.set(key, []);
      map.get(key)!.push(h);
    }
    const entries = Array.from(map.entries())
      .map(([key, items]) => ({
        key,
        workspace: key === '\u0000unbound' ? '' : key,
        items: items.sort((a, b) => {
          const ta = Number(a.timestamp ?? a.created_at ?? 0);
          const tb = Number(b.timestamp ?? b.created_at ?? 0);
          return tb - ta;
        }),
      }))
      .sort((a, b) => {
        // 未绑定组永远沉底
        if (!a.workspace && !b.workspace) return 0;
        if (!a.workspace) return 1;
        if (!b.workspace) return -1;
        return baseName(a.workspace).localeCompare(baseName(b.workspace), 'zh-CN');
      });
    return entries;
  }, [historyList]);

  const unboundCount = groups.find((g) => !g.workspace)?.items.length ?? 0;
  const recentWorkspaces = useMemo(() => {
    const set = new Set<string>();
    for (const g of groups) if (g.workspace) set.add(g.workspace);
    try {
      const saved = localStorage.getItem(RECENT_WORKSPACES_KEY);
      if (saved) JSON.parse(saved).forEach((w: string) => set.add(w));
    } catch {
      /* ignore */
    }
    return Array.from(set).slice(0, 8);
  }, [groups]);

  /** 记录一次成功使用的目录（新建/移动后）。 */
  const rememberWorkspace = (workspace: string): void => {
    try {
      const next = [workspace, ...recentWorkspaces.filter((w) => w !== workspace)].slice(0, 8);
      localStorage.setItem(RECENT_WORKSPACES_KEY, JSON.stringify(next));
    } catch {
      /* ignore */
    }
  };

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

  const openPickerCreate = (): void => {
    setPicker({ mode: 'create', recent: recentWorkspaces });
  };

  const openPickerMove = (uid: string): void => {
    setPicker({ mode: 'move', uid, recent: recentWorkspaces });
  };

  const pickerConfirm = (workspace: string): void => {
    const ws = workspace.trim();
    if (!ws || !picker) return;
    if (picker.mode === 'create') {
      onCreateHistory(ws);
    } else {
      onMoveHistory(picker.uid, ws);
    }
    rememberWorkspace(ws);
    setPicker(null);
  };

  const toggleGroup = (key: string): void => {
    setCollapsedGroups((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  };

  const renderItem = (h: HistoryEntry): ReactElement => {
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
        <HistoryChips entry={h} />
        <button
          className="conv-item-move"
          onClick={(e) => {
            e.stopPropagation();
            openPickerMove(uid);
          }}
          title="移动到工作目录"
        >
          <Icon name="folder" size={12} />
        </button>
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
  };

  return (
    <div className={`conv-sidebar-wrap ${open ? 'open' : 'closed'}`}>
      <aside className="conv-sidebar" aria-label="会话列表">
        <header className="conv-sidebar-header">
          <span className="conv-sidebar-title">
            <Icon name="history" size={14} />
            工作区
          </span>
          <div className="conv-sidebar-actions">
            <button className="conv-icon-btn" onClick={openPickerCreate} title="新建会话（选择工作目录）">
              <Icon name="plus" size={15} />
            </button>
            <button className="conv-icon-btn" onClick={() => setOpen(false)} title="收起侧边栏">
              <Icon name="panelLeftClose" size={15} />
            </button>
          </div>
        </header>

        <div className="conv-sidebar-list">
          {groups.length === 0 ? (
            <div className="conv-sidebar-empty">
              还没有会话
              <br />
              点 ＋ 选择工作目录开始
            </div>
          ) : (
            groups.map((g) => {
              const collapsed = collapsedGroups.has(g.key);
              return (
                <div key={g.key} className="ws-group">
                  <div className="ws-group-head" onClick={() => toggleGroup(g.key)} title={g.workspace || '未绑定目录'}>
                    <span className={`ws-caret ${collapsed ? '' : 'open'}`}>▸</span>
                    <span className="ws-folder">
                      <Icon name="folder" size={13} />
                    </span>
                    <span className="ws-name">{g.workspace ? baseName(g.workspace) : '未绑定目录'}</span>
                    <span className="ws-count">{g.items.length}</span>
                    {g.workspace ? (
                      <button
                        className="ws-add"
                        title="在此目录新建会话"
                        onClick={(e) => {
                          e.stopPropagation();
                          onCreateHistory(g.workspace);
                          rememberWorkspace(g.workspace);
                        }}
                      >
                        <Icon name="plus" size={13} />
                      </button>
                    ) : null}
                  </div>
                  {!collapsed && g.items.map(renderItem)}
                </div>
              );
            })
          )}

          {/* 存量无目录会话的一次性清理（用户决策：全部删除） */}
          {unboundCount > 0 && (
            <div className="ws-cleanup">
              <span>检测到 {unboundCount} 个旧版无目录会话</span>
              <button
                className="ws-cleanup-btn"
                onClick={() => {
                  if (window.confirm(`将永久删除 ${unboundCount} 个旧会话记录（工作区模型升级前的历史）。确定？`)) {
                    onClearAllHistories();
                  }
                }}
              >
                全部清空
              </button>
            </div>
          )}
        </div>
      </aside>

      {!open && (
        <button className="conv-rail" onClick={() => setOpen(true)} title="展开会话侧边栏">
          <Icon name="panelLeftOpen" size={16} />
        </button>
      )}

      {/* 目录选择弹层（新建 / 移动共用） */}
      {picker && (
        <WorkspacePicker
          title={picker.mode === 'create' ? '新建会话 · 选择工作目录' : '移动会话 · 选择工作目录'}
          recent={picker.recent}
          onConfirm={pickerConfirm}
          onClose={() => setPicker(null)}
        />
      )}
    </div>
  );
}

// ------------------------------------------------------------------ //
// 目录选择弹层（导出：侧栏内新建/移动 + WindowModeView 无会话发送引导共用）
// ------------------------------------------------------------------ //
interface WorkspacePickerProps {
  title: string;
  recent: string[];
  onConfirm: (workspace: string) => void;
  onClose: () => void;
}

export function WorkspacePicker({ title, recent, onConfirm, onClose }: WorkspacePickerProps): ReactElement {
  const [value, setValue] = useState('');
  const inputRef = useRef<HTMLInputElement | null>(null);

  useEffect(() => {
    requestAnimationFrame(() => inputRef.current?.focus());
  }, []);

  const browse = async (): Promise<void> => {
    try {
      const dir = await window.moonlight?.selectDirectory();
      if (dir) setValue(dir);
    } catch {
      /* 浏览器环境（dev 预览）无原生对话框，忽略 */
    }
  };

  return (
    <div className="ws-picker-mask" onClick={onClose}>
      <div className="ws-picker" onClick={(e) => e.stopPropagation()}>
        <div className="ws-picker-title">{title}</div>
        <div className="ws-picker-row">
          <input
            ref={inputRef}
            className="ws-picker-input"
            value={value}
            onChange={(e) => setValue(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && value.trim()) onConfirm(value);
              else if (e.key === 'Escape') onClose();
            }}
            placeholder="输入工作目录路径…"
          />
          <button className="ws-picker-browse" onClick={() => void browse()}>
            浏览…
          </button>
        </div>
        {recent.length > 0 && (
          <div className="ws-picker-recent">
            <span className="ws-picker-recent-label">最近使用</span>
            <div className="ws-picker-chips">
              {recent.map((w) => (
                <button key={w} className="ws-picker-chip" onClick={() => onConfirm(w)} title={w}>
                  {baseName(w)}
                </button>
              ))}
            </div>
          </div>
        )}
        <div className="ws-picker-actions">
          <button className="btn btn-ghost" onClick={onClose}>
            取消
          </button>
          <button
            className="btn btn-primary"
            disabled={!value.trim()}
            onClick={() => onConfirm(value)}
          >
            确定
          </button>
        </div>
      </div>
    </div>
  );
}

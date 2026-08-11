/**
 * TaskCreateModal — 新建任务 Modal（重设计 v5 · 工作区模型）。
 * 字段：标题（默认取输入框内容）、目标。
 * 工作目录不再让用户选择：任务自动在当前会话绑定的工作目录下执行
 * （workspace 由 useTaskMode 从当前会话注入；会话无目录时后端用默认根自动创建）。
 */
import { useEffect, useState, type ReactElement } from 'react';

export interface TaskCreateModalProps {
  open: boolean;
  defaultTitle?: string;
  defaultGoal?: string;
  onClose: () => void;
  onConfirm: (fields: { title: string; goal: string }) => void;
}

export function TaskCreateModal({
  open,
  defaultTitle = '',
  defaultGoal = '',
  onClose,
  onConfirm,
}: TaskCreateModalProps): ReactElement {
  const [title, setTitle] = useState(defaultTitle);
  const [goal, setGoal] = useState(defaultGoal);

  // 打开时同步默认值
  useEffect(() => {
    if (!open) return;
    setTitle(defaultTitle);
    setGoal(defaultGoal);
  }, [open, defaultTitle, defaultGoal]);

  if (!open) return <div />;

  const submit = (): void => {
    onConfirm({ title: title.trim(), goal: goal.trim() });
  };

  return (
    <div className="mask open" onClick={(e) => e.target === e.currentTarget && onClose()}>
      <div className="modal" role="dialog" aria-label="新建任务">
        <div className="m-title">新建任务</div>
        <div className="m-sub">任务智能体将在当前工作目录内读写文件、执行命令（路径白名单）</div>

        <div className="m-field">
          <label className="m-label">标题</label>
          <input className="m-input" value={title} onChange={(e) => setTitle(e.target.value)} placeholder="留空则由 AI 自动命名" />
        </div>
        <div className="m-field">
          <label className="m-label">目标</label>
          <textarea
            className="m-textarea"
            value={goal}
            onChange={(e) => setGoal(e.target.value)}
            placeholder="描述你想让任务智能体完成什么…"
          />
        </div>

        <div className="m-actions">
          <button type="button" className="m-btn" onClick={onClose}>
            取消
          </button>
          <button type="button" className="m-btn primary" onClick={submit}>
            创建并执行
          </button>
        </div>
      </div>
    </div>
  );
}

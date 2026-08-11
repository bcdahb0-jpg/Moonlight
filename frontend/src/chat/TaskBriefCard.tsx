/**
 * TaskBriefCard — 聊天流中的「任务简报」折叠卡片（2026-08-10）。
 *
 * 背景：任务平台 run_end(completed) 会把一条【任务简报】回注会话历史（P2 上下文桥，
 * 让角色"记得"任务、能回答"文件在哪"）。此前它被当作普通 AI 气泡整段渲染，
 * 8000 字符技术独白直接刷屏历史会话 —— 本组件把它折叠成卡片：
 *   - 头部：完成徽标 + 任务标题（单行截断）+ 展开箭头
 *   - 折叠态：工作目录 / 生成文件数（关键信息一眼可见）
 *   - 展开态：完整摘要（Markdown）+ 工作目录 + 生成文件
 * 后端已按 task_id 去重（同一任务只留最新一条），旧数据无 kind 字段时按内容前缀兜底。
 */
import { useMemo, useState, type ReactElement } from 'react';
import type { ChatMessage } from '@/state/types';
import { Markdown } from '@/components/Markdown';
import { Icon } from '@/ui/icons';

interface ParsedBrief {
  title: string;
  summary: string;
  workspace: string;
  files: string[];
}

/** 解析「【任务简报】{标题}：{摘要}\n工作目录：…\n生成文件：…」结构。 */
export function parseBrief(text: string): ParsedBrief {
  const lines = text.split('\n');
  const first = (lines[0] ?? '').replace(/^【任务简报】/, '').trim();
  let title = first;
  let summary = '';
  const sep = first.indexOf('：');
  if (sep > 0 && sep < first.length - 1) {
    title = first.slice(0, sep).trim();
    summary = first.slice(sep + 1).trim();
  }
  let workspace = '';
  const files: string[] = [];
  for (const ln of lines.slice(1)) {
    const w = ln.match(/^工作目录[:：]\s*(.+)$/);
    if (w) {
      workspace = w[1].trim();
      continue;
    }
    const f = ln.match(/^生成文件[:：]\s*(.+)$/);
    if (f) {
      files.push(
        ...f[1]
          .split(/[、,，;；]/)
          .map((s) => s.trim())
          .filter(Boolean),
      );
    }
  }
  return { title, summary, workspace, files };
}

export function TaskBriefCard({ message }: { message: ChatMessage }): ReactElement {
  const [open, setOpen] = useState(false);
  const { title, summary, workspace, files } = useMemo(
    () => parseBrief(message.text),
    [message.text],
  );

  return (
    <div className="chat-brief">
      <button
        type="button"
        className="chat-brief-head"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        title={title}
      >
        <span className="chat-brief-tag">
          <Icon name="check" size={13} />
          任务简报
        </span>
        <span className="chat-brief-title">{title}</span>
        <span className={`chat-brief-chev ${open ? 'open' : ''}`}>
          <Icon name="chevronDown" size={14} />
        </span>
      </button>

      {open ? (
        <div className="chat-brief-body">
          {summary && <Markdown content={summary} />}
          {workspace && (
            <div className="chat-brief-line">
              <Icon name="folder" size={12} />
              <span>{workspace}</span>
            </div>
          )}
          {files.length > 0 && (
            <div className="chat-brief-line">生成文件：{files.join('、')}</div>
          )}
        </div>
      ) : (
        <div className="chat-brief-meta">
          {workspace && (
            <span className="chat-brief-line">
              <Icon name="folder" size={12} />
              <span>{workspace}</span>
            </span>
          )}
          {files.length > 0 && (
            <span className="chat-brief-line">生成文件 {files.length} 个</span>
          )}
        </div>
      )}
    </div>
  );
}

/**
 * Markdown — 任务输出的统一 Markdown 渲染收敛层（UX 改造 M1）。
 *
 * 职责：
 *  - 用 react-markdown + remark-gfm 渲染 LLM/agent 输出的格式化文本
 *    （标题 / 列表 / 表格 / 代码块 / 引用 / 链接），消灭"Markdown 源码当纯文本"；
 *  - 链接一律 target=_blank + rel=noopener（Electron 内不劫持窗口）；
 *  - 块级代码包 <pre>，行内代码独立样式；
 *  - 样式收敛在 `.md-body`（global.css），全站唯一 Markdown 入口。
 */
import { type ReactElement } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';

export interface MarkdownProps {
  /** Markdown 源文本（可能为空串）。 */
  content: string;
  /** 覆盖容器 class（默认 .md-body）。 */
  className?: string;
}

export function Markdown({ content, className }: MarkdownProps): ReactElement {
  return (
    <div className={className ?? 'md-body'}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          a: ({ node: _node, ...props }) => (
            <a {...props} target="_blank" rel="noopener noreferrer" />
          ),
          code: ({ node: _node, className: codeClassName, children, ...props }) => {
            const isBlock = /language-/.test(codeClassName ?? '');
            return isBlock ? (
              <pre className="md-pre">
                <code className={codeClassName} {...props}>
                  {children}
                </code>
              </pre>
            ) : (
              <code className="md-inline" {...props}>
                {children}
              </code>
            );
          },
        }}
      >
        {content}
      </ReactMarkdown>
    </div>
  );
}

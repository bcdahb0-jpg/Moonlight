/**
 * ToolCallCard — 单次工具调用行（可折叠，plan §5.5 / prototype .tool-row）。
 * badge 按工具名着色：bash 蓝 / read 紫 / write 金 / grep 绿；正文显示
 * 调用参数摘要，展开显示工具输出（tool_result 内容）。
 */
import { useState, type ReactElement } from 'react';
import type { ToolCallPayload, ToolResultPayload } from './types';

export interface ToolCallCardProps {
  call: ToolCallPayload;
  /** 已收到 result 时传入；未完成显示「执行中…」。 */
  result?: ToolResultPayload;
  /** 默认展开。 */
  defaultOpen?: boolean;
}

function badgeClass(name: string): string {
  if (name === 'bash') return 'bash';
  if (name.startsWith('read')) return 'read';
  if (name.startsWith('write')) return 'write';
  if (name === 'grep' || name === 'glob' || name === 'ls') return 'grep';
  return 'other';
}

/** 从参数生成简短命令摘要（bash 显示命令；文件类显示路径）。 */
function commandSummary(name: string, args: Record<string, unknown>): string {
  if (name === 'bash' && typeof args.command === 'string') return args.command;
  if (typeof args.path === 'string') return `${name} ${args.path}`;
  if (typeof args.pattern === 'string') return `${name} ${args.pattern}`;
  if (typeof args.name === 'string') return `${name} ${args.name}`;
  const entries = Object.entries(args);
  if (entries.length === 0) return name;
  return `${name} ${entries
    .map(([k, v]) => `${k}=${String(v).slice(0, 40)}`)
    .join(' ')}`;
}

export function ToolCallCard({ call, result, defaultOpen = false }: ToolCallCardProps): ReactElement {
  const [open, setOpen] = useState(defaultOpen);
  const done = result !== undefined;
  const isError = result?.is_error ?? false;
  // 2026-08-10：bash 命令「exit code 非 0」在业务上往往是探针/期望失败
  // （如 findstr 没匹配到 = 确认旧代码没有某特征），但用户看着像失败——
  // 单独以 warning 色呈现，与真错误（is_error）区分开。
  const isWarn = !isError && /exit code [1-9]\d*/.test(String(result?.result ?? ''));
  const statusText = !done ? '执行中…' : `✓ ${String(result?.result ?? 'ok').slice(0, 60)}`;
  const statusClass = !done ? 'run' : isError ? 'err' : isWarn ? 'warn' : 'ok';

  return (
    <div className={`tool-row ${open ? 'open' : ''}`}>
      <div className="tool-row-head" onClick={() => setOpen((v) => !v)}>
        <span className={`tool-badge ${badgeClass(call.name)}`}>{call.name}</span>
        <span className="tool-cmd" title={JSON.stringify(call.arguments)}>
          {commandSummary(call.name, call.arguments)}
        </span>
        <span className={`tool-status ${statusClass}`}>{statusText}</span>
      </div>
      {result !== undefined ? (
        <div className="tool-body">{String(result.result) || '（无输出）'}</div>
      ) : (
        <div className="tool-body dim">{isError ? '（调用失败）' : '（等待执行结果…）'}</div>
      )}
    </div>
  );
}

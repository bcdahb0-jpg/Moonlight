/**
 * TaskRunCard — 一次任务执行的执行记录卡片（可折叠，plan §5.5 / prototype .run-card）。
 * 由该 run 的 SSE 事件驱动：
 * - run_start  → 卡片出现（running 琥珀脉冲点）
 * - tool_call / tool_result → ToolCallCard 行（按 tool_call_id 配对）
 * - message(role=assistant, origin=core) → 内核输出行
 * - run_end    → 完成（绿色实心点）+ 摘要 chips
 *
 * 2026-08-10（UX 改进）：
 * - 解析 write_file/str_replace/browser_screenshot 成功写盘的文件路径，
 *   完成态展示「生成文件」列表（点击在系统文件管理器中定位）。
 * - 新增「打开工作目录」按钮（调 POST /api/tasks/{id}/open，后端 explorer 打开）。
 */
import { useMemo, useState, type ReactElement } from 'react';
import { taskApi } from '@/api/tasks';
import type { Task, TaskEvent, ToolCallPayload, ToolResultPayload } from './types';
import { ToolCallCard } from './ToolCallCard';

export interface TaskRunCardProps {
  task: Task;
  runId: string;
  /** 该 run 的完整事件（已按 seq 升序）。 */
  events: TaskEvent[];
  defaultOpen?: boolean;
}

function fmtPath(workspace: string): string {
  const parts = workspace.split(/[\\/]/).filter(Boolean);
  return parts.length > 1 ? parts[parts.length - 1] : workspace;
}

/** SSE payload 运行时校验：后端改动字段名时静默降级而非类型造假。 */
function isToolCallPayload(p: unknown): p is ToolCallPayload {
  const o = p as Record<string, unknown> | null;
  return !!o && typeof o.name === 'string' && typeof o.tool_call_id === 'string';
}

function isToolResultPayload(p: unknown): p is ToolResultPayload {
  const o = p as Record<string, unknown> | null;
  return !!o && typeof o.name === 'string' && typeof o.tool_call_id === 'string';
}

/** 关注会产出文件的工具（与后端 task_route._collect_task_artifacts 对齐）。 */
const ARTIFACT_TOOLS = new Set(['write_file', 'str_replace', 'browser_screenshot']);

/** 从工具参数提取文件路径（相对/绝对均可；带 ~ 展开由后端处理）。 */
function pathFromArgs(args: unknown): string {
  if (typeof args !== 'object' || args === null) return '';
  const o = args as Record<string, unknown>;
  if (typeof o.path === 'string') return o.path.trim();
  if (typeof o.filename === 'string') return o.filename.trim();
  return '';
}

/** 展示用：工作目录内的产物显示相对路径，外部保持绝对路径。 */
function displayPath(workspace: string, p: string): string {
  if (!p) return p;
  const normWs = workspace.replace(/[\\/]+/g, '/').replace(/\/$/, '');
  const normP = p.replace(/[\\/]+/g, '/');
  if (normP.startsWith(normWs + '/')) return normP.slice(normWs.length + 1);
  return p;
}

export function TaskRunCard({ task, runId, events, defaultOpen = true }: TaskRunCardProps): ReactElement {
  const [open, setOpen] = useState(defaultOpen);
  /** 打开目录/定位文件的状态：'' 空闲 | 'opening' 中 | 错误文案 */
  const [openState, setOpenState] = useState('');

  const { running, error, outputLines, toolRows, runEndMeta, artifacts } = useMemo(() => {
    const byTool = new Map<string, { call: ToolCallPayload; result?: ToolResultPayload }>();
    const failedCalls = new Set<string>();
    const lines: string[] = [];
    let running = false;
    let error = '';
    let runEndMeta = '';

    for (const ev of events) {
      switch (ev.event_type) {
        case 'run_start':
          running = true;
          break;
        case 'tool_call': {
          if (!isToolCallPayload(ev.payload)) break;
          byTool.set(ev.payload.tool_call_id, { call: ev.payload });
          break;
        }
        case 'tool_result': {
          if (!isToolResultPayload(ev.payload)) break;
          const row = byTool.get(ev.payload.tool_call_id);
          if (row) byTool.set(ev.payload.tool_call_id, { ...row, result: ev.payload });
          if (ev.payload.is_error) failedCalls.add(ev.payload.tool_call_id);
          break;
        }
        case 'message': {
          const p = ev.payload as { role?: string; content?: string };
          if (p.role === 'assistant' && typeof p.content === 'string' && p.content) {
            lines.push(p.content);
          }
          break;
        }
        case 'run_end': {
          running = false;
          const s = ev.payload.status as string | undefined;
          if (s === 'error' || s === 'interrupted') error = s;
          runEndMeta = String(ev.payload.status ?? '');
          break;
        }
        case 'run_error': {
          running = false;
          error = String(ev.payload.error ?? ev.payload.message ?? '');
          break;
        }
        default:
          break;
      }
    }

    const toolRows = [...byTool.values()].map((r) => (
      <ToolCallCard key={r.call.tool_call_id} call={r.call} result={r.result} />
    ));

    // 收集成功写盘的文件（write 类工具 + 非 error 的 tool_result 配对）
    const artifacts: string[] = [];
    const seen = new Set<string>();
    for (const [callId, row] of byTool) {
      if (failedCalls.has(callId)) continue;
      if (!ARTIFACT_TOOLS.has(row.call.name)) continue;
      const p = pathFromArgs(row.call.arguments);
      if (p && !seen.has(p)) {
        seen.add(p);
        artifacts.push(p);
      }
    }

    return { running, error, outputLines: lines, toolRows, runEndMeta, artifacts };
  }, [events]);

  const stateClass = running ? 'running' : error ? 'error' : 'done';

  /** 打开工作目录；path 非空则定位该文件。失败展示内联错误。 */
  const handleOpen = async (path?: string): Promise<void> => {
    if (openState === 'opening') return;
    setOpenState('opening');
    try {
      await taskApi.openTaskDir(task.id, path);
      setOpenState('');
    } catch (err) {
      setOpenState(err instanceof Error ? err.message : '打开失败');
    }
  };

  const openBtn = (
    <button
      type="button"
      className="s-btn"
      disabled={openState === 'opening'}
      onClick={(e) => {
        e.stopPropagation();
        void handleOpen();
      }}
    >
      {openState === 'opening' ? '打开中…' : '📂 打开工作目录'}
    </button>
  );

  return (
    <div className={`task-run-card ${open ? 'open' : ''}`}>
      <div className="run-head" onClick={() => setOpen((v) => !v)}>
        <span className={`run-state ${stateClass}`} />
        <span className="run-title">
          任务执行记录 · <b>{task.title || task.goal}</b>
        </span>
        <span className="run-meta">
          {fmtPath(task.workspace)} · run-{runId.slice(0, 8)}
        </span>
        <span className="run-arrow">▾</span>
      </div>
      <div className="run-body">
        {outputLines.length > 0 && (
          <div className="run-output">
            {outputLines.map((line, i) => (
              <div key={i} className="run-output-line">
                {line}
              </div>
            ))}
          </div>
        )}
        {toolRows.length > 0 && <div className="tool-list">{toolRows}</div>}
        {/* 生成文件：成功写盘的产物，点击可在资源管理器中定位 */}
        {!running && artifacts.length > 0 && (
          <div className="run-artifacts">
            <div className="run-artifacts-title">生成文件</div>
            <ul className="run-artifacts-list">
              {artifacts.map((p, i) => (
                <li key={i}>
                  <button
                    type="button"
                    className="artifact-link"
                    title={p}
                    onClick={() => void handleOpen(p)}
                  >
                    {displayPath(task.workspace, p)}
                  </button>
                </li>
              ))}
            </ul>
          </div>
        )}
        {!running && (
          <div className="run-summary">
            <span className="s-chip">{error ? `⚠ ${error || '异常终止'}` : `✓ ${runEndMeta || '完成'}`}</span>
            {toolRows.length > 0 && <span className="s-chip">⚙ {toolRows.length} 次工具调用</span>}
            {!error && openBtn}
            {openState && <span className="s-chip err">{openState}</span>}
          </div>
        )}
      </div>
    </div>
  );
}

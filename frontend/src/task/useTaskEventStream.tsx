/**
 * useTaskEventStream — 把某个 run 的 SSE 事件流聚合成可渲染的视图数据。
 *
 * 由 TaskDetailPanel（右栏）与 TaskStreamPanel（消息流内执行过程卡）共享，
 * 保证两处展示逻辑一致。聚合规则：
 *  - tool_call / tool_result 按 tool_call_id 配对 → ToolCallCard 列表（保持首次出现顺序）；
 *  - message(role=assistant) → 内核输出行；
 *  - run_start / run_end / run_error → 状态机（running / done / error）+ 耗时。
 */
import { useMemo, type ReactElement } from 'react';
import type { Task, TaskEvent, ToolCallPayload, ToolResultPayload } from './types';
import { ToolCallCard } from './ToolCallCard';

export interface TaskEventStreamView {
  state: 'running' | 'done' | 'error';
  statusText: string;
  outputLines: string[];
  toolRows: ReactElement[];
  toolCount: number;
  runEndMeta: string;
  duration: string;
  /** 2026-08-10：成功写盘的产物文件路径（write_file/str_replace/browser_screenshot）。 */
  artifacts: string[];
  /** 2026-08-10：run_start 携带的本次触发指令（续跑摘要展示用）。 */
  runMessage: string;
  /** 2026-08-10：该任务第几次执行（1 起，来自 run_start.run_number）。 */
  runNumber: number;
  /** 2026-08-10：执行中失败的 tool_result 数（外层弱提示「N 次失败重试」）。 */
  failedCount: number;
}

/** 关注会产出文件的工具（与后端 task_route._collect_task_artifacts 对齐）。 */
const ARTIFACT_TOOLS = new Set(['write_file', 'str_replace', 'browser_screenshot']);

/** 从工具参数提取文件路径。 */
export function pathFromArgs(args: unknown): string {
  if (typeof args !== 'object' || args === null) return '';
  const o = args as Record<string, unknown>;
  if (typeof o.path === 'string') return o.path.trim();
  if (typeof o.filename === 'string') return o.filename.trim();
  return '';
}

/** 展示用：工作目录内的产物显示相对路径，外部保持绝对路径。 */
export function displayArtifactPath(workspace: string, p: string): string {
  if (!p) return p;
  const normWs = workspace.replace(/[\\/]+/g, '/').replace(/\/$/, '');
  const normP = p.replace(/[\\/]+/g, '/');
  if (normP.startsWith(normWs + '/')) return normP.slice(normWs.length + 1);
  return p;
}

function tsOf(ev: TaskEvent): number {
  const t = ev.created_at;
  if (typeof t === 'number') return t;
  if (typeof t === 'string') return Date.parse(t);
  return NaN;
}

function fmtDuration(ms: number): string {
  if (!Number.isFinite(ms) || ms < 0) return '';
  const s = ms / 1000;
  if (s < 60) return `${s.toFixed(1)}s`;
  const m = Math.floor(s / 60);
  const rs = Math.round(s % 60);
  return `${m}m ${String(rs).padStart(2, '0')}s`;
}

function isToolCallPayload(p: unknown): p is ToolCallPayload {
  const o = p as Record<string, unknown> | null;
  return !!o && typeof o.name === 'string' && typeof o.tool_call_id === 'string';
}

function isToolResultPayload(p: unknown): p is ToolResultPayload {
  const o = p as Record<string, unknown> | null;
  return !!o && typeof o.name === 'string' && typeof o.tool_call_id === 'string';
}

export function useTaskEventStream(
  task: Task | null,
  runId: string,
  events: TaskEvent[],
): TaskEventStreamView {
  return useMemo<TaskEventStreamView>(() => {
    const byTool = new Map<string, { call: ToolCallPayload; result?: ToolResultPayload }>();
    const failedCalls = new Set<string>();
    const lines: string[] = [];
    let running = false;
    let error = '';
    let runEndMeta = '';
    let startTs = NaN;
    let endTs = NaN;
    // 2026-08-10：run_start 的 message / run_number（续跑时与任务标题不同）。
    let runMessage = '';
    let runNumber = 0;

    for (const ev of events) {
      switch (ev.event_type) {
        case 'run_start':
          running = true;
          startTs = tsOf(ev);
          {
            const p = ev.payload as { message?: string; run_number?: number };
            if (typeof p.message === 'string' && p.message.trim()) runMessage = p.message.trim();
            if (typeof p.run_number === 'number' && p.run_number > 0) runNumber = p.run_number;
          }
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
          endTs = tsOf(ev);
          break;
        }
        case 'run_error': {
          running = false;
          error = String(ev.payload.error ?? ev.payload.message ?? '');
          endTs = tsOf(ev);
          break;
        }
        default:
          break;
      }
    }

    const toolRows = [...byTool.values()].map((r) => (
      <ToolCallCard key={r.call.tool_call_id} call={r.call} result={r.result} defaultOpen={running} />
    ));

    // 2026-08-10：收集成功写盘的产物文件（write 类工具 + 非 error 的 tool_result 配对）
    // 2026-08-10 修复：过滤虚拟/越界路径——LLM 可能用 /workspace/... 虚拟容器路径
    // （sandbox to_container 掩码），或 write_file 被沙箱拒绝但 is_error 修复前
    // 被判成功。只保留相对路径或 workspace 内绝对路径，避免「生成文件」列表混入
    // 点不开的假产物（点击报 path 不在任务工作目录内）。
    const wsNorm = String(task?.workspace ?? '').replace(/[\\/]+/g, '/').replace(/\/$/, '');
    const isUsableArtifact = (p: string): boolean => {
      if (!p) return false;
      const np = p.replace(/[\\/]+/g, '/');
      const isPosixAbs = np.startsWith('/');
      const isWinAbs = /^[A-Za-z]:\//.test(np) || np.startsWith('//');
      if (!isPosixAbs && !isWinAbs) return true; // 相对路径（LLM 以 cwd=workspace 写盘）
      return !!wsNorm && np.startsWith(wsNorm + '/'); // 绝对路径必须在 workspace 内
    };
    const artifacts: string[] = [];
    const seen = new Set<string>();
    for (const [callId, row] of byTool) {
      if (failedCalls.has(callId)) continue;
      if (!ARTIFACT_TOOLS.has(row.call.name)) continue;
      const p = pathFromArgs(row.call.arguments);
      if (p && isUsableArtifact(p) && !seen.has(p)) {
        seen.add(p);
        artifacts.push(p);
      }
    }

    const state = error ? 'error' : running ? 'running' : 'done';
    return {
      state,
      statusText: running ? '执行中' : error ? '失败' : '完成',
      outputLines: lines,
      toolRows,
      toolCount: byTool.size,
      runEndMeta: error || runEndMeta,
      duration: Number.isFinite(endTs - startTs) ? fmtDuration(endTs - startTs) : '',
      artifacts,
      runMessage,
      runNumber,
      failedCount: failedCalls.size,
    };
    // task 仅用于依赖追踪（事件流归 run 所有）
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [task, runId, events]);
}

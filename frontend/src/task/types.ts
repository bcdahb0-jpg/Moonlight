/**
 * task/types.ts — 任务领域类型（对齐后端 task_platform/models.py 的
 * Task / Run / TaskEvent pydantic 模型 + SSE 事件流契约，plan §5）。
 *
 * origin 区分 G7 双层交互：core（任务内核事件）→ TaskRunCard；
 * shell（人设外壳转述）→ 角色气泡。当前后端 SSE 事件 origin 均为 core。
 */

export interface Task {
  id: string;
  title: string;
  workspace: string;
  goal: string;
  status: string;
  created_at: string;
  updated_at: string;
  last_run_id: string | null;
  /** P2 上下文桥：任务锁定的发起会话 uid（简报回注聊天历史用）。 */
  conversation_uid?: string | null;
}

export interface Run {
  id: string;
  task_id: string;
  status: string;
  started_at: string;
  ended_at: string | null;
  error: string | null;
  summary: string | null;
}

export type TaskEventType =
  | 'run_start'
  | 'message'
  | 'tool_call'
  | 'tool_result'
  | 'status'
  | 'clarify_requested'
  | 'progress'
  | 'run_end'
  | 'run_error'
  | 'error';

export interface TaskEvent {
  seq: number;
  event_type: TaskEventType;
  task_id: string;
  run_id: string;
  category: string;
  origin: string;
  payload: Record<string, unknown>;
  created_at: string;
}

/** tool_call 事件 payload（hooks.py emit_tool_call）。 */
export interface ToolCallPayload {
  name: string;
  arguments: Record<string, unknown>;
  tool_call_id: string;
}

/** tool_result 事件 payload（hooks.py emit_tool_result）。 */
export interface ToolResultPayload {
  name: string;
  result: string;
  is_error: boolean;
  tool_call_id: string;
}

/** message 事件 payload（hooks.py emit_message）。 */
export interface MessagePayload {
  role: string;
  content: string;
  delta?: boolean;
}

/** run_start 事件 payload（hooks.py emit_run_start）。 */
export interface RunStartPayload {
  goal?: string;
  /** 2026-08-10：本次 run 的触发指令（续跑时与任务标题不同，用于「第 N 次执行」摘要）。 */
  message?: string;
  /** 2026-08-10：该任务第几次执行（1 起）。 */
  run_number?: number;
}

/** clarify_requested 事件 payload（hooks.py emit_clarify）。 */
export interface ClarifyPayload {
  question: string;
  options?: string[];
}

/** GET /api/workspaces/scan 目录树节点。 */
export interface WorkspaceNode {
  name: string;
  path: string;
  children?: WorkspaceNode[];
}

export interface TaskDetail {
  task: Task;
  messages: unknown[];
  events: TaskEvent[];
  runs: Run[];
}

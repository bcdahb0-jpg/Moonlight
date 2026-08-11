/**
 * tasks.ts — 任务平台 REST + SSE 客户端（plan §6 Phase 1/2/5）。
 * 对齐后端 task_route.py 路由：
 *   GET/POST /api/tasks, GET/PATCH/DELETE /api/tasks/{id},
 *   GET /api/workspaces/root, GET /api/workspaces/scan,
 *   POST /api/tasks/{id}/runs, POST /api/tasks/{id}/interrupt,
 *   GET /api/tasks/{id}/runs/stream (SSE), GET /api/tasks/tools, GET /api/skills.
 */
import { API_BASE, ApiError } from './rest';
import type {
  Run,
  Task,
  TaskDetail,
  TaskEvent,
  WorkspaceNode,
} from '@/task/types';

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE}${path}`, {
      ...init,
      headers: { 'Content-Type': 'application/json', ...(init?.headers ?? {}) },
    });
  } catch {
    throw new ApiError(0, '无法连接后端服务，请确认后端已启动 (127.0.0.1:12393)');
  }
  if (!response.ok) {
    let message = `HTTP ${response.status}`;
    try {
      const body = (await response.json()) as { error?: string };
      if (body.error) message = body.error;
    } catch {
      // non-JSON error body; keep the status message
    }
    throw new ApiError(response.status, message);
  }
  return (await response.json()) as T;
}

const get = <T>(path: string): Promise<T> => request<T>(path, { method: 'GET' });
const post = <T>(path: string, body?: unknown): Promise<T> =>
  request<T>(path, { method: 'POST', body: JSON.stringify(body ?? {}) });
const patch = <T>(path: string, body?: unknown): Promise<T> =>
  request<T>(path, { method: 'PATCH', body: JSON.stringify(body ?? {}) });
const del = <T>(path: string): Promise<T> => request<T>(path, { method: 'DELETE' });

interface ListTasksResp {
  ok: boolean;
  tasks: Task[];
}

interface CreateTaskResp {
  ok: boolean;
  task: Task;
  auto_created_workspace?: boolean;
}

interface StartRunResp {
  ok: boolean;
  run: Run;
  busy?: boolean;
}

/** POST /api/intent/classify 响应（意图路由 P1）。 */
export interface IntentClassifyResp {
  ok: boolean;
  kind: 'chat' | 'task';
  confidence: number;
  reason: string;
  source?: string;
}

interface ScanResp {
  ok: boolean;
  base: string;
  tree: WorkspaceNode[];
}

export const taskApi = {
  listTasks: (): Promise<ListTasksResp> => get<ListTasksResp>('/api/tasks'),
  createTask: (
    title: string,
    goal = '',
    workspace?: string,
    conversationUid?: string,
  ): Promise<CreateTaskResp> =>
    post<CreateTaskResp>('/api/tasks', { title, goal, workspace, conversation_uid: conversationUid }),
  getTask: (id: string): Promise<TaskDetail> => get<TaskDetail>(`/api/tasks/${id}`),
  patchTask: (
    id: string,
    fields: { title?: string; goal?: string; status?: string },
  ): Promise<{ task: Task }> => patch<{ task: Task }>(`/api/tasks/${id}`, fields),
  deleteTask: (id: string): Promise<{ ok: boolean }> => del<{ ok: boolean }>(`/api/tasks/${id}`),

  workspacesRoot: (): Promise<{ root: string }> => get<{ root: string }>('/api/workspaces/root'),
  scanWorkspaces: (path?: string): Promise<ScanResp> =>
    get<ScanResp>(`/api/workspaces/scan?path=${encodeURIComponent(path ?? '')}`),

  startRun: (taskId: string, message: string): Promise<StartRunResp> =>
    post<StartRunResp>(`/api/tasks/${taskId}/runs`, { message }),
  interruptRun: (taskId: string): Promise<{ ok: boolean }> =>
    post<{ ok: boolean }>(`/api/tasks/${taskId}/interrupt`),
  /** 在系统文件管理器中打开任务工作目录；传 path 则在文件夹中定位该文件。 */
  openTaskDir: (taskId: string, path?: string): Promise<{ ok: boolean; opened?: string; revealed?: string }> =>
    post<{ ok: boolean; opened?: string; revealed?: string }>(
      `/api/tasks/${taskId}/open`,
      path ? { path } : {},
    ),
  /** 意图路由（P1）：输入 → chat|task 分类。失败返回 chat（fail-soft）。 */
  classifyIntent: async (text: string): Promise<IntentClassifyResp> => {
    try {
      return await post<IntentClassifyResp>('/api/intent/classify', { text });
    } catch {
      return { ok: false, kind: 'chat', confidence: 0, reason: '分类不可用', source: 'fallback' };
    }
  },
};

/** SSE 帧解析（后端 `data: {json}\n\n` + `: keep-alive` 注释帧）。 */
function parseFrames(buf: string, onEvent: (ev: TaskEvent) => void): string {
  let rest = buf;
  let idx: number;
  while ((idx = rest.indexOf('\n\n')) !== -1) {
    const frame = rest.slice(0, idx);
    rest = rest.slice(idx + 2);
    for (const line of frame.split('\n')) {
      if (!line.startsWith('data: ')) continue;
      const data = line.slice('data: '.length);
      if (!data) continue;
      try {
        onEvent(JSON.parse(data) as TaskEvent);
      } catch {
        // 坏行跳过（plan §9 风险 8：JSONL/SSE 解析容错）
      }
    }
  }
  return rest;
}

export interface TaskStreamHandle {
  close: () => void;
  /** 首帧或连接结束前 resolve；拒绝 = 连接失败。 */
  ready: Promise<void>;
}

/**
 * 订阅任务 SSE 事件流。断线/刷新后传 after_seq 回放 seq>N（SQLite 锚点）。
 * 返回句柄；调用方在卸载/切换任务时 close()。
 */
export function streamTaskEvents(
  taskId: string,
  afterSeq: number,
  onEvent: (ev: TaskEvent) => void,
  onClose?: () => void,
): TaskStreamHandle {
  const controller = new AbortController();
  const url = `${API_BASE}/api/tasks/${encodeURIComponent(taskId)}/runs/stream?after_seq=${afterSeq}`;

  const ready = (async () => {
    let resp: Response;
    try {
      resp = await fetch(url, { signal: controller.signal });
    } catch (err) {
      if ((err as Error).name === 'AbortError') return;
      throw new ApiError(0, '任务事件流连接失败');
    }
    if (!resp.ok || !resp.body) {
      // 触发 onClose，让调用方走自动重连（HTTP 层失败同样要重连，而非静默死等）
      onClose?.();
      throw new ApiError(resp.status, `SSE HTTP ${resp.status}`);
    }
    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buf = '';
    try {
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        buf = parseFrames(buf + decoder.decode(value, { stream: true }), onEvent);
      }
    } catch (err) {
      if ((err as Error).name !== 'AbortError') throw err;
    } finally {
      onClose?.();
    }
  })();

  return { close: () => controller.abort(), ready };
}

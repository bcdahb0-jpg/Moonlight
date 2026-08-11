/**
 * useTaskMode — 任务控制器（plan §5.4 / 2026-08-09 统一输入）。
 *
 * 职责（Codex 式单输入框，2026-08-09 定稿）：
 * - **不分模式**：无模式下拉，输入永远走意图路由 resolveSend(text)——
 *   闲聊 → 聊天链路（人设语音）；任务指令 → 任务内核（skill/agents/MCP 全能力）。
 * - 首发送 → 无活动任务时自动创建任务（Codex 式输入即执行）；
 * - 续接发送 → 直接 startRun，已连接的流实时推新 run 事件；
 * - 断线自动重连（seq 锚点：永远从已知最大 seq 续拉）；
 * - 刷新持久：localStorage 存 activeTaskId，挂载时 getTask 恢复历史事件
 *   + 从恢复出的最大 seq 续连流。
 *
 * 事件按 run_id 分组进 eventsByRun，每个 run 一张 TaskRunCard。
 * G7 分流：core 事件在这里落地；shell 汇报通过 onShellEvent 回调外发给上层。
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { taskApi, streamTaskEvents, type TaskStreamHandle } from '@/api/tasks';
import type { Task, TaskEvent } from './types';

const ACTIVE_TASK_KEY = 'moonlight.activeTaskId';
/** 任务所属会话 uid（方案 C：任务锁定发起它的会话，摘要卡只在该会话显示）。 */
const TASK_CONV_KEY = 'moonlight.taskConvUid';
/** 断线重连退避：2s 起，每失败翻倍，封顶 30s（防后端不可用时无限空烧）。 */
const RECONNECT_BASE_MS = 2000;
const RECONNECT_MAX_MS = 30000;

/**
 * 2026-08-10 修复：任务已完成时，匹配以下句式的输入视为「新任务」请求，
 * 创建独立新任务而非续跑旧任务——否则「再做一个计数器」会复用旧任务
 * 「帮我做一个番茄钟」，任务标题/goal/上下文全部错乱（LLM 还停在旧任务语境）。
 * 不匹配的输入（如「把按钮改成红色」「加个暂停功能」）仍是续跑旧任务。
 * 句式：开头为「再/重新/新/另/麻烦再/请再（可选 帮我/给我）」或
 * 「帮我/给我/麻烦帮我/请帮我（可选 再）」+ 做一个/做个/弄一个/弄个/来一个/整个/生成一个。
 */
const NEW_TASK_REQUEST_RE =
  /^(?:(?:再|重新|新|另|麻烦再|请再)(?:帮我|给我)?|(?:帮我|给我|麻烦帮我|请帮我)(?:再)?)[做一个做个弄一个弄个来一个整个生成一个]/;

export interface UseTaskModeOptions {
  /** 每条事件外发（用于角色外壳气泡：run_start / run_end 汇报）。 */
  onShellEvent?: (ev: TaskEvent) => void;
  /** 当前会话 uid：任务创建时锁定，用于「摘要卡只出现在发起会话」。 */
  currentHistoryUid?: string | null;
  /** v5：当前会话绑定的工作目录（绝对路径）。任务直接在此目录下创建执行，不再让用户选择。 */
  currentWorkspace?: string | null;
}

export interface CreateTaskFields {
  title: string;
  goal: string;
}

/** 任务名称自动生成：输入压缩空白后取前 30 字（Codex 式「输入即执行」，无需手动命名）。 */
export function autoTaskTitle(text: string): string {
  const compact = text.replace(/\s+/g, ' ').trim();
  if (!compact) return '未命名任务';
  return compact.length > 30 ? `${compact.slice(0, 30)}…` : compact;
}

export interface UseTaskMode {
  activeTask: Task | null;
  activeRunId: string | null;
  /** 任务锁定所属的会话 uid（null = 旧任务无记录，降级为所有会话可显示）。 */
  conversationUid: string | null;
  /** 每个 run 的事件列表（TaskRunCard 按 run_id 取）。 */
  eventsByRun: Record<string, TaskEvent[]>;
  isRunning: boolean;
  error: string;
  createOpen: boolean;
  pendingText: string;
  /** 最近一次意图路由结果（P1）：供输入框 chip 反馈「已作为任务执行」。 */
  lastIntent: 'chat' | 'task' | null;
  send: (text: string) => Promise<void>;
  /** 意图路由（2026-08-09 定稿）：调 /api/intent/classify → 'chat'|'task'。
   *  闲聊→聊天链路（人设语音）；任务指令→任务内核（skill/agents 全能力）。 */
  resolveSend: (text: string) => Promise<'chat' | 'task'>;
  /** 显式打开「新建任务」弹窗（不依赖首条消息）。 */
  openCreate: () => void;
  createAndRun: (fields: CreateTaskFields) => Promise<void>;
  interrupt: () => Promise<void>;
  closeCreate: () => void;
}

export function useTaskMode(options: UseTaskModeOptions = {}): UseTaskMode {
  const [activeTask, setActiveTask] = useState<Task | null>(null);
  const [activeRunId, setActiveRunId] = useState<string | null>(null);
  const [eventsByRun, setEventsByRun] = useState<Record<string, TaskEvent[]>>({});
  const [error, setError] = useState('');
  const [createOpen, setCreateOpen] = useState(false);
  const [pendingText, setPendingText] = useState('');
  const [conversationUid, setConversationUid] = useState<string | null>(() => {
    try {
      return localStorage.getItem(TASK_CONV_KEY);
    } catch {
      return null;
    }
  });
  /** 最近一次意图路由结果：供输入框 chip 反馈「已作为任务执行」。 */
  const [lastIntent, setLastIntent] = useState<'chat' | 'task' | null>(null);

  const onShellEventRef = useRef(options.onShellEvent);
  onShellEventRef.current = options.onShellEvent;
  // 当前会话 uid 用 ref 读取：避免把变化中的 uid 塞进 useCallback 依赖，
  // 导致每个会话切换都重建 send/createAndRun 引用。
  const convUidRef = useRef(options.currentHistoryUid);
  convUidRef.current = options.currentHistoryUid ?? null;
  // 任务锁定的会话 uid 的 ref 版（send 里防串场判断用，避免依赖 conversationUid state）。
  const conversationUidRef = useRef(conversationUid);
  conversationUidRef.current = conversationUid;
  // v5：当前会话工作目录用 ref 读取（同 uid 的理由：不重建 createAndRun 引用）。
  const workspaceRef = useRef(options.currentWorkspace);
  workspaceRef.current = options.currentWorkspace ?? null;

  /** 意图路由（2026-08-09 定稿）：调 /api/intent/classify → 'chat'|'task'。
   *  永远不抛错：分类失败返回 'chat'（fail-soft，聊天比误触任务安全）。
   *  记录 lastIntent 供输入框 chip 反馈。 */
  const resolveSend = useCallback(async (text: string): Promise<'chat' | 'task'> => {
    try {
      const res = await taskApi.classifyIntent(text);
      const kind = res.ok && res.kind === 'task' ? 'task' : 'chat';
      setLastIntent(kind);
      return kind;
    } catch {
      setLastIntent('chat');
      return 'chat';
    }
  }, []);

  const lastSeqRef = useRef(0);
  const taskIdRef = useRef<string | null>(null);
  const closingRef = useRef(false);
  const reconnectTimerRef = useRef<number | null>(null);
  const reconnectDelayRef = useRef(RECONNECT_BASE_MS);
  const streamRef = useRef<TaskStreamHandle | null>(null);

  const handleEvent = useCallback((ev: TaskEvent): void => {
    lastSeqRef.current = Math.max(lastSeqRef.current, ev.seq);
    // 收到新事件 → 连接健康，退避复位
    reconnectDelayRef.current = RECONNECT_BASE_MS;
    setEventsByRun((prev) => {
      const cur = prev[ev.run_id] ?? [];
      return { ...prev, [ev.run_id]: [...cur, ev] };
    });
    if (ev.event_type === 'run_start') setActiveRunId(ev.run_id);
    onShellEventRef.current?.(ev);
  }, []);

  const closeStream = useCallback((): void => {
    closingRef.current = true;
    if (reconnectTimerRef.current !== null) {
      window.clearTimeout(reconnectTimerRef.current);
      reconnectTimerRef.current = null;
    }
    streamRef.current?.close();
    streamRef.current = null;
  }, []);

  const startStream = useCallback(
    (taskId: string, afterSeq: number): void => {
      setError(''); // 重连/新连接时清掉旧流错误，避免健康后残留误导性提示
      closeStream();
      closingRef.current = false;
      taskIdRef.current = taskId;
      lastSeqRef.current = Math.max(lastSeqRef.current, afterSeq);

      const handle = streamTaskEvents(
        taskId,
        afterSeq,
        handleEvent,
        () => {
          // 已被新流替换（startStream 换任务/刷新）→ 忽略旧流关闭事件
          if (streamRef.current !== handle) return;
          streamRef.current = null;
          // 非主动关闭 → 自动重连（seq 锚点续拉，无重复）；失败按 2s→4s→…→30s 退避
          if (closingRef.current || reconnectTimerRef.current !== null) return;
          const delay = reconnectDelayRef.current;
          reconnectDelayRef.current = Math.min(delay * 2, RECONNECT_MAX_MS);
          reconnectTimerRef.current = window.setTimeout(() => {
            reconnectTimerRef.current = null;
            if (taskIdRef.current) startStream(taskIdRef.current, lastSeqRef.current);
          }, delay);
        },
      );
      streamRef.current = handle;
      handle.ready.catch((err: unknown) => {
        setError(err instanceof Error ? err.message : '任务事件流连接失败');
      });
    },
    [closeStream, handleEvent],
  );

  // 挂载时恢复上次任务（刷新持久）
  useEffect(() => {
    const tid = localStorage.getItem(ACTIVE_TASK_KEY);
    if (!tid) return;
    let cancelled = false;
    taskApi
      .getTask(tid)
      .then((detail) => {
        if (cancelled) return;
        setActiveTask(detail.task);
        const map: Record<string, TaskEvent[]> = {};
        let max = 0;
        for (const ev of detail.events) {
          (map[ev.run_id] ??= []).push(ev);
          if (ev.seq > max) max = ev.seq;
        }
        setEventsByRun(map);
        if (detail.runs.length > 0) {
          setActiveRunId(detail.runs[detail.runs.length - 1].id);
        }
        lastSeqRef.current = max;
        startStream(tid, max);
      })
      .catch(() => {
        if (cancelled) return;
        localStorage.removeItem(ACTIVE_TASK_KEY);
        setError('无法恢复上次任务，已清除记录');
      });
    return () => {
      cancelled = true;
    };
  }, [startStream]);

  // 卸载时断开
  useEffect(
    () => () => {
      closingRef.current = true;
      if (reconnectTimerRef.current !== null) window.clearTimeout(reconnectTimerRef.current);
      streamRef.current?.close();
    },
    [],
  );

  // 2026-08-10 修复：会话切换时清空不属于新会话的活跃任务显示。
  // 背景：localStorage 恢复的旧任务（conv=null）经 taskRuns 的放宽逻辑
  // （conv===null → 任意会话都显示）会「穿越」到新会话——用户实测：
  // 新建会话发「帮我做一个番茄钟」，旧任务的完成卡先冒出来，等新任务就绪才消失。
  // 规则：currentHistoryUid 发生「真切换」（前后都非空且不同，排除刷新/首挂载
  // null→uid 过渡）时，活跃任务锁定会话 ≠ 新会话 → 清空 activeTask/事件流
  // （localStorage 记录保留，切回原会话仍可恢复）。刷新场景 uid 不变不触发。
  const prevHistoryUidRef = useRef<string | null | undefined>(undefined);
  useEffect(() => {
    const next = options.currentHistoryUid ?? null;
    const prev = prevHistoryUidRef.current;
    prevHistoryUidRef.current = next;
    if (prev === undefined) return; // 首挂载 null→uid 过渡
    if (!prev || !next || prev === next) return; // 刷新/空值/未变 → 不触发
    if (conversationUidRef.current !== next) {
      setActiveTask(null);
      setActiveRunId(null);
      setEventsByRun({});
    }
  }, [options.currentHistoryUid]);

  // 任一 run 处于运行中（含 run_start 且未到 run_end/run_error）。
  // useMemo 出布尔值：UI 直接读；send 经 ref 读当前值，避免把 isRunning 塞进 deps
  // 导致每来一条 SSE 事件都重建 send 引用（进而传导 WindowModeView/ChatPanel 重渲染）。
  const isRunning = useMemo((): boolean => {
    for (const evs of Object.values(eventsByRun)) {
      for (let i = evs.length - 1; i >= 0; i--) {
        const t = evs[i].event_type;
        if (t === 'run_end' || t === 'run_error') break;
        if (t === 'run_start') return true;
      }
    }
    return false;
  }, [eventsByRun]);
  const isRunningRef = useRef(isRunning);
  isRunningRef.current = isRunning;

  const send = useCallback(
    async (text: string): Promise<void> => {
      setError('');
      if (!activeTask) {
        // v5.1：Codex 式「输入即执行」——首次输入自动创建任务，
        // 标题/目标由输入自动生成，不再弹「新建任务」窗口打断。
        await createAndRun({ title: autoTaskTitle(text), goal: text }, text);
        return;
      }
      // 2026-08-09 防串场：activeTask 是 localStorage 恢复的**旧任务**时，
      // 若其所属会话与当前会话不匹配（含 conv=null 的 P2 前旧任务）→
      // 视为新指令，创建**新任务**而非续跑旧任务——否则旧任务 checkpoint
      // 累积的历史（10+ 个 run）会让新 run 上下文爆炸（token_budget HARD STOP
      // 195%），且进度卡因会话不匹配永不显示（用户反馈"任务进度又不见了"）。
      const conv = conversationUidRef.current;
      const cur = convUidRef.current;
      if (conv !== cur) {
        await createAndRun({ title: autoTaskTitle(text), goal: text }, text);
        return;
      }
      if (isRunningRef.current) {
        setError('任务执行中，请先等待完成或中断');
        return;
      }
      // 2026-08-10 修复：任务已完成（非运行中），输入是明确的新任务句式
      // （再做一个 X / 帮我做一个 X / 重新做一个 X …）→ 创建**新任务**，
      // 不续跑旧任务。否则「再做一个计数器」复用「番茄钟」，标题/上下文全错。
      if (NEW_TASK_REQUEST_RE.test(text.trim())) {
        await createAndRun({ title: autoTaskTitle(text), goal: text }, text);
        return;
      }
      try {
        const { run } = await taskApi.startRun(activeTask.id, text);
        setActiveRunId(run.id);
        // 已连接的流会实时收到新 run 事件；无流时补连（如刷新后）
        if (!streamRef.current) startStream(activeTask.id, lastSeqRef.current);
      } catch (err) {
        setError(err instanceof Error ? err.message : '启动任务执行失败');
      }
    },
    [activeTask, startStream],
  );

  const createAndRun = useCallback(
    async (fields: CreateTaskFields, initialText?: string): Promise<void> => {
      setError('');
      // 2026-08-10 修复：乐观清空旧任务显示——创建新任务前立即移除旧 activeTask/
      // 事件流。否则 `await createTask` 网络往返期间旧任务仍是 activeTask，
      // 其卡片会继续渲染（新会话发任务时「旧卡片先出现、新任务就绪后才消失」）。
      setActiveTask(null);
      setActiveRunId(null);
      setEventsByRun({});
      // 初始指令：自动创建路径由调用方显式传入（= 用户首条输入）；
      // 手动「新建任务」弹窗路径回退到 pendingText（可能为空 → agent 等指令）。
      const text = initialText ?? pendingText;
      try {
        // v5：任务直接在当前会话的工作目录下创建执行；会话无目录时留空 → 后端默认根自动创建。
        // 标题留空时自动生成（输入前 30 字）。
        const title = fields.title.trim() || autoTaskTitle(fields.goal || text);
        // P2 上下文桥：任务创建时绑定发起会话 uid（简报回注聊天历史用）。
        const { task } = await taskApi.createTask(
          title,
          fields.goal,
          workspaceRef.current ?? undefined,
          convUidRef.current ?? undefined,
        );
        setActiveTask(task);
        localStorage.setItem(ACTIVE_TASK_KEY, task.id);
        // 任务锁定发起会话：摘要卡只在该会话显示（方案 C 的 message.task_id 溯源，前端先行版）
        const conv = convUidRef.current;
        if (conv) {
          setConversationUid(conv);
          try {
            localStorage.setItem(TASK_CONV_KEY, conv);
          } catch {
            /* localStorage 不可用时静默 */
          }
        }
        const { run } = await taskApi.startRun(task.id, text);
        setActiveRunId(run.id);
        // 切换到新任务：清空旧 run 事件，避免摘要卡用新任务标题渲染旧 run
        setEventsByRun({});
        setCreateOpen(false);
        setPendingText('');
        startStream(task.id, 0);
      } catch (err) {
        setError(err instanceof Error ? err.message : '创建任务失败');
      }
    },
    [pendingText, startStream],
  );

  /** 显式「新建任务」：弹出创建弹窗（activeTask 已存在时也能新建）。 */
  const openCreate = useCallback((): void => {
    setPendingText('');
    setCreateOpen(true);
  }, []);

  const interrupt = useCallback(async (): Promise<void> => {
    if (!activeTask) return;
    setError('');
    try {
      await taskApi.interruptRun(activeTask.id);
    } catch (err) {
      setError(err instanceof Error ? err.message : '中断任务失败');
    }
  }, [activeTask]);

  const closeCreate = useCallback((): void => {
    setCreateOpen(false);
    setPendingText('');
  }, []);

  return {
    activeTask,
    activeRunId,
    conversationUid,
    eventsByRun,
    isRunning,
    error,
    createOpen,
    pendingText,
    lastIntent,
    send,
    resolveSend,
    openCreate,
    createAndRun,
    interrupt,
    closeCreate,
  };
}

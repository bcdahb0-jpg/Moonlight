/**
 * WindowModeView — 窗口模式布局（前端重设计 · 方案 C 修正版）：
 * 标题栏（操作入口 + 窗口控制） + 会话侧边栏（可折叠，带状态 chip） +
 * 聊天面板 + 右栏（角色立绘常驻，RightPanel）。
 *
 * 任务显示模型（2026-08-09 精简）：
 * - 消息流内 TaskStreamPanel 执行记录卡：运行中实时展开事件流，完成后
 *   折叠为摘要头，点击头部展开/折叠——详情就地查看，不进入右栏；
 * - 右栏只保留角色舞台（Live2D），任务详情抽屉已移除。
 */
import { useEffect, useMemo, useRef, useState, type ReactElement } from 'react';
import type { Live2DAdapter } from '@/live2d/Live2DAdapter';
import { ChatPanel } from '@/chat/ChatPanel';
import { ConversationSidebar, WorkspacePicker } from '@/chat/ConversationSidebar';
import { TitleBar } from '@/components/TitleBar';
import { RightPanel } from '@/components/RightPanel';
import { FeatureDock } from '@/components/FeatureDock';
import type { WSClient } from '@/api/wsClient';
import type { AudioPlayer } from '@/api/audioPlayer';
import type { AffectionSummary } from '@/types/ws';
import type { ConnStatus, Emotion, ChatMessage, HistoryEntry } from '@/state/types';
import type { ErrorCode, ScreenStatusMessage } from '@/types/ws';
import type { TaskEvent } from '@/task/types';
import { useTaskMode } from '@/task/useTaskMode';
import { TaskStreamPanel } from '@/task/TaskStreamPanel';
import { TaskCreateModal } from '@/task/TaskCreateModal';

/**
 * 2026-08-10：按内容为 run 重建锚点——历史会话加载后消息 id 全部重新生成，
 * 运行期记录的 anchorId 全部失效（任务卡沉底堆末尾）。这里用 run_start 携带的
 * 触发指令（payload.message）在消息流里匹配对应的用户消息（任务对话已回注
 * chat_history，content 与 message 一致），让任务卡重新内联到正确位置。
 * 匹配不到返回 null（沉底兜底）。
 */
function findRunAnchor(events: TaskEvent[], messages: ChatMessage[]): string | null {
  const start = events.find((e) => e.event_type === 'run_start');
  const goal = String(
    (start?.payload as { message?: unknown } | undefined)?.message ?? '',
  )
    .trim();
  if (!goal) return null;
  for (let i = messages.length - 1; i >= 0; i--) {
    const m = messages[i];
    if (m.role !== 'user') continue;
    const t = (m.text ?? '').trim();
    if (!t) continue;
    // 严格相等优先；兼容文本略有差异（前缀互相包含）
    if (t === goal || t.startsWith(goal) || goal.startsWith(t)) return m.id;
  }
  return null;
}

export interface WindowModeViewProps {
  modelUrl: string;
  emotionMap?: Record<string, number>;
  tapMotions?: Record<string, Record<string, number>>;
  adapterRef: React.MutableRefObject<Live2DAdapter | null>;
  messages: ChatMessage[];
  isThinking: boolean;
  subtitle: string;
  /** 聊天 agent 工具执行状态文案（tool_call_status；空 = 无）。 */
  toolStatus?: string | null;
  connected: boolean;
  historyList: HistoryEntry[];
  currentHistoryUid: string | null;
  screenStatus?: ScreenStatusMessage | null;
  lastError: string | null;
  errorCode: ErrorCode | null;
  affection: AffectionSummary | null;
  emotion: Emotion;
  /** 情绪强度 0..1（诊断显示，透传给 RightPanel）。 */
  emotionIntensity?: number | null;
  /** 情绪来源 'rule' | 'llm'（诊断显示，透传给 RightPanel）。 */
  emotionSource?: string | null;
  /** 引擎类型（诊断，透传给 RightPanel 徽标）。 */
  engineType?: 'soullink' | 'legacy' | null;
  /** 引擎选择结果回调（透传给 RightPanel）。 */
  onEngineChange?: (engine: 'soullink' | 'legacy') => void;
  connStatus: ConnStatus;
  onDismissError: () => void;
  onOpenSettingsSection?: (section: string) => void;
  onToggleMode: () => void;
  /** 录音结束回调（默认只发 audio-end；App 层会额外回显「正在识别…」占位）。 */
  onMicAudioEnd?: () => void;
  /** 文字输入：本地立即回显（0ms，不阻塞于意图路由/WS/LLM 分类）。发送由
   *  本组件在后台 resolveSend 完成后按 chat/task 分流。
   *  UX 修复（2026-08-10）：返回回显消息 id（任务分支用作执行卡锚点）。 */
  onEchoText?: (text: string) => string | void;
  /** 文字发送回调（聊天链路，App 层只发 WS，回显已由 onEchoText 先行）；缺省时直接发 WS。 */
  onSendText?: (text: string) => void;
  /** 打断 AI 回复（App 层负责本地复位转圈 + 发 WS 中断信号）。 */
  onInterrupt?: () => void;
  /** 任务模式：用户消息本地回显（App 层 dispatch ADD_MESSAGE，不发 WS）。 */
  onTaskUserEcho?: (text: string) => void;
  /** 任务模式：每条任务事件外发（App 层据此生成角色外壳汇报气泡）。 */
  onTaskShellEvent?: (ev: TaskEvent) => void;
  ws: () => WSClient | null;
  audioPlayer: () => AudioPlayer | null;
  onError: (error: Error) => void;
}

export function WindowModeView({
  modelUrl,
  emotionMap,
  tapMotions,
  adapterRef,
  messages,
  isThinking,
  subtitle,
  toolStatus = null,
  connected,
  historyList,
  currentHistoryUid,
  screenStatus = null,
  lastError,
  errorCode,
  affection,
  emotion,
  emotionIntensity,
  emotionSource,
  engineType,
  connStatus,
  onEngineChange,
  onDismissError,
  onOpenSettingsSection,
  onToggleMode,
  onMicAudioEnd,
  onEchoText,
  onSendText,
  onInterrupt,
  // onTaskUserEcho 已废弃（v6 回显由 onEchoText 先行，任务分支不再重复 echo）：
  // 接口字段保留兼容，App 仍传 handleTaskUserEcho，此处不再解构使用。
  onTaskShellEvent,
  ws,
  audioPlayer,
  onError,
}: WindowModeViewProps): ReactElement {
  // v5：当前会话的工作目录（任务模式直接在此目录执行，不再让用户选择）。
  const currentWorkspace = useMemo(() => {
    const current = historyList.find(
      (h) => String(h.uid ?? h.history_uid ?? '') === currentHistoryUid,
    );
    const ws = String(current?.workspace ?? '').trim();
    return ws || null;
  }, [historyList, currentHistoryUid]);
  const currentSessionTitle = useMemo(() => {
    const current = historyList.find(
      (h) => String(h.uid ?? h.history_uid ?? '') === currentHistoryUid,
    );
    return String(current?.title ?? current?.name ?? '').trim() || null;
  }, [historyList, currentHistoryUid]);

  // 任务模式控制器：模式/活动任务/事件流/新建弹窗。shell 汇报经 onTaskShellEvent 外发。
  // currentHistoryUid 用于任务创建时锁定发起会话（摘要卡只在该会话显示）。
  const task = useTaskMode({
    onShellEvent: onTaskShellEvent,
    currentHistoryUid,
    currentWorkspace,
  });
  // v5：无激活会话时发送 → 先引导「选择工作目录新建会话」。
  const [sessionPickerOpen, setSessionPickerOpen] = useState(false);
  // 2026-08-10 任务卡锚点模型（v5，per-run 独立锚点）：
  // 之前的 v2/v3/v4 用**单个全局** taskAnchorId 控制所有 run 卡片的插入位置——
  // 新 run 触发重锚时会把历史 run 的卡片一起拖到新请求附近（用户实测：
  // 第一个任务的卡片跑到第二个任务卡片旁边，两张卡堆在一起）。
  // 修复：每个 run 独立维护自己的锚点（runId → 消息 id），新 run 只锚定自己，
  // 不再移动历史 run 的卡片。锚点语义：
  //  - string：执行卡内联到该消息之后（初始 = 触发它的用户消息，随后迁移到
  //    AI 确认消息之后，避免「任务已完成、AI 才刚答应」的时序倒挂）；
  //  - null：无锚点 → 渲染在消息流末尾兜底（Modal 新建任务 / 锚点消息已消失）。
  const [runAnchors, setRunAnchors] = useState<Record<string, string | null>>({});
  // 「下个 run」的初始锚点候选：onSend 判定 task 时记录回显消息 id，
  // 新 run 首次出现时消费（分配给该 run）。Modal 新建任务显式置 null（沉底）；
  // undefined = 无候选（chat 链路 delegate 任务 / 刷新恢复）→ 锚定最后用户消息。
  const pendingAnchorRef = useRef<string | null | undefined>(undefined);

  // effect A：新 run 首次出现 → 分配初始锚点（pending echoId ?? 内容匹配 ?? 最后用户消息 ?? 沉底）。
  // 依赖 messages：本地回显的用户消息异步到达，run 出现时回显必已在流中。
  // 2026-08-10：历史/刷新恢复的 run（getTask 重建 eventsByRun）优先按 run_start 指令
  // 内容匹配用户消息——否则全部沉底堆末尾（用户反馈"任务卡片合并到一起"）。
  useEffect(() => {
    if (!task.activeTask) return;
    const runs = Object.keys(task.eventsByRun);
    if (runs.length === 0) return;
    const lastUser = [...messages].reverse().find((m) => m.role === 'user');
    const pending = pendingAnchorRef.current;
    setRunAnchors((prev) => {
      const next = { ...prev };
      let changed = false;
      for (const runId of runs) {
        if (runId in next) continue; // 已有记录（含 null 沉底），不覆盖
        if (pending !== undefined) {
          next[runId] = pending;
        } else {
          next[runId] =
            findRunAnchor(task.eventsByRun[runId] ?? [], messages) ??
            lastUser?.id ??
            null;
        }
        changed = true;
      }
      if (changed) pendingAnchorRef.current = undefined; // 已消费
      return changed ? next : prev;
    });
  }, [task.activeTask, task.eventsByRun, messages]);

  // 任务卡锚点一旦绑定到发起任务的用户消息就保持不变。
  // 后续普通对话不能触发锚点迁移，否则任务卡会被挪到新的 AI 回复下面。
  const recentWorkspaces = useMemo(
    () =>
      Array.from(
        new Set(
          historyList
            .map((h) => String(h.workspace ?? '').trim())
            .filter(Boolean),
        ),
      ).slice(0, 8),
    [historyList],
  );

  // 每个 run 一张执行过程卡（v5.3：Codex 式就地展示执行过程）——
  // 运行中实时展开事件流，完成后折叠为摘要；点击头部展开/折叠（详情就地查看，
  // 2026-08-09：不再进右栏任务抽屉）。
  // 任务绑定发起会话（方案 C + P2 conversation_uid）：过程卡只在发起会话显示。
  // conv=null 的旧任务（P2 之前创建，无会话归属）：**当前激活任务时仍显示**
  // ——否则 localStorage 恢复旧任务后「续跑旧任务」场景进度完全不可见
  // （2026-08-09 用户反馈"任务进度又不见了"：旧任务 conv=null 卡片永不渲染）。
  // 仅当任务被激活（activeTask）且本会话是发起者（或旧任务无归属）时显示，
  // 不会出现"每个会话都出现同一批任务卡"的错乱。
  // v5（2026-08-10）：每个 run 携带自己的 anchorId，MessageList 按 run 分散内联到
  // 各自锚点消息之后——历史 run 的卡片不会跟随新 run 移动（修复"两张卡堆一起"）。
  const taskRuns = useMemo<
    Array<{ runId: string; anchorId: string | null; card: ReactElement }>
  >(() => {
    const active = task.activeTask;
    if (!active) return [];
    const conv = task.conversationUid;
    // 放宽：conv=null 的旧任务（激活态）也显示；有归属的仍严格绑定发起会话。
    const shouldShow = conv === null || conv === currentHistoryUid;
    if (!shouldShow) return [];
    return Object.entries(task.eventsByRun).map(([runId, events]) => ({
      runId,
      anchorId: runAnchors[runId] ?? null,
      card: (
        <TaskStreamPanel
          task={active}
          runId={runId}
          events={events}
          onInterrupt={() => void task.interrupt()}
        />
      ),
    }));
  }, [
    task.activeTask,
    task.eventsByRun,
    task.conversationUid,
    currentHistoryUid,
    runAnchors,
  ]);

  return (
    <div className="window-layout">
      <TitleBar
        onToggleMode={onToggleMode}
        onOpenSettings={() => onOpenSettingsSection?.('general')}
      />
      <div className="window-body">
        <ConversationSidebar
          historyList={historyList}
          currentHistoryUid={currentHistoryUid}
          onFetchHistory={() => ws()?.sendFetchHistoryList()}
          onLoadHistory={(uid) => ws()?.sendFetchAndSetHistory(uid)}
          onCreateHistory={(workspace) => ws()?.sendCreateNewHistory(workspace)}
          onDeleteHistory={(uid) => ws()?.sendDeleteHistory(uid)}
          onRename={(uid, title) => ws()?.sendSetHistoryTitle(uid, title)}
          onMoveHistory={(uid, workspace) => ws()?.sendSetHistoryWorkspace(uid, workspace)}
          onClearAllHistories={() => ws()?.sendClearAllHistories()}
          dock={<FeatureDock onOpenSection={onOpenSettingsSection} />}
        />
        <ChatPanel
          messages={messages}
          isThinking={isThinking}
          subtitle={subtitle}
          toolStatus={toolStatus}
          connected={connected}
          sessionTitle={currentSessionTitle}
          workspace={currentWorkspace}
          screenStatus={screenStatus}
          lastError={lastError}
          errorCode={errorCode}
          onDismissError={onDismissError}
          onOpenSettingsSection={onOpenSettingsSection}
          // Phase 1（pet-ptt-workflow）：无会话 **或** 有会话但未绑定工作目录 → 引导
          // 选择/迁移目录（旧无目录会话不静默伪造，继续输入前显式绑定）。
          showWorkspaceGuide={!currentHistoryUid || !currentWorkspace}
          onPickWorkspace={() => setSessionPickerOpen(true)}
          onSend={(text) => {
            // 2026-08-09 统一输入：不分模式。意图路由 resolveSend 自动分流——
            // 闲聊 → 聊天链路（人设语音）；任务指令 → 任务内核（skill/agents 全能力）。
            // v6 优化：先 0ms 本地回显（onEchoText），再后台分类路由。意图分类是
            // HTTP/LLM 调用（规则未命中时可能数秒），绝不能让用户消息上屏等它。
            const trimmed = text.trim();
            if (!trimmed) return;
            if (!currentHistoryUid) {
              setSessionPickerOpen(true);
              return;
            }
            const echoId = onEchoText?.(trimmed) ?? '';
            void task.resolveSend(trimmed).then((kind) => {
              if (kind === 'task') {
                // 任务指令：交给任务内核（事件流卡实时反馈执行过程；用户消息已回显）。
                // v5：记录 pending 锚点（回显消息 id），新 run 首次出现时消费——
                // 执行卡内联到这条用户消息下方（随后由 effect B 迁移到 AI 确认之后）。
                pendingAnchorRef.current = echoId || null;
                void task.send(trimmed);
                return;
              }
              // 闲聊：走聊天链路（回显已做，这里只发 WS；App 层 handleTextSend 只发送）。
              if (onSendText) {
                onSendText(trimmed);
              } else {
                ws()?.sendTextInput(trimmed);
              }
            });
          }}
          onAudioChunk={(chunk) => ws()?.sendMicAudioChunk(chunk)}
          onAudioEnd={() => {
            if (onMicAudioEnd) {
              onMicAudioEnd();
            } else {
              ws()?.sendMicAudioEnd();
            }
          }}
          // 气泡「再次播放」（2026-08-10）：用消息里保存的音频快照重新入队播放。
          onReplayAudio={(message) => {
            const data = message.audioData;
            if (!data || !message.text) return;
            audioPlayer()?.enqueue({
              id: `replay-${message.id}-${Date.now()}`,
              base64: data.base64,
              volumes: data.volumes,
              visemes: data.visemes ?? null,
              emotionMeta: null,
              sliceLengthMs: data.sliceLengthMs,
              expression: data.expression ?? null,
              displayText: message.text,
            });
          }}
          onInterrupt={() => {
            audioPlayer()?.stop();
            if (onInterrupt) {
              onInterrupt();
            } else {
              ws()?.sendInterrupt();
            }
          }}
          taskMode={task}
          taskRuns={taskRuns}
          onOpenSection={onOpenSettingsSection}
        />
        <RightPanel
          modelUrl={modelUrl}
          emotionMap={emotionMap}
          tapMotions={tapMotions}
          adapterRef={adapterRef}
          affection={affection}
          emotion={emotion}
          emotionIntensity={emotionIntensity}
          emotionSource={emotionSource}
          engineType={engineType}
          connStatus={connStatus}
          onError={onError}
          onEngineChange={onEngineChange}
        />
      </div>

      {/* 任务模式：首发送弹新建任务 Modal（默认标题 = 输入内容） */}
      <TaskCreateModal
        open={task.createOpen}
        defaultTitle={task.pendingText}
        defaultGoal=""
        onClose={task.closeCreate}
        onConfirm={(fields) => {
          // v5：Modal 新建任务没有触发它的用户消息 → 显式 pending=null（沉底），
          // 执行卡渲染在消息流末尾兜底，而不是锚定到之前某条旧消息。
          pendingAnchorRef.current = null;
          void task.createAndRun(fields);
        }}
      />

      {/* v5：无会话发送引导 → 选择工作目录新建会话；
          Phase 1：有会话但无工作目录（旧会话）→ 迁移目录 */}
      {sessionPickerOpen && (
        <WorkspacePicker
          title={
            currentHistoryUid && !currentWorkspace
              ? '迁移会话 · 选择工作目录'
              : '新建会话 · 选择工作目录'
          }
          recent={recentWorkspaces}
          onConfirm={(workspace) => {
            if (currentHistoryUid && !currentWorkspace) {
              ws()?.sendSetHistoryWorkspace(currentHistoryUid, workspace);
            } else {
              ws()?.sendCreateNewHistory(workspace);
            }
            setSessionPickerOpen(false);
          }}
          onClose={() => setSessionPickerOpen(false)}
        />
      )}
    </div>
  );
}

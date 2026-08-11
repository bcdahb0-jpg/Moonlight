/**
 * TaskStreamPanel — 消息流内的任务执行过程卡（Codex 式实时展示）。
 *
 * 运行中：就地展开完整事件流（agent 输出行 + 工具调用列表，SSE 增量更新，
 * 新事件自动滚到底部）——像 Codex 主界面一样看着智能体一步步执行。
 * 完成后：折叠为摘要头（状态 + 耗时 + 工具数），点击可展开回看全过程。
 * 详情就地查看（2026-08-09：不再跳右栏任务详情抽屉）。
 *
 * 事件聚合逻辑与旧右栏 TaskDetailPanel 共享 useTaskEventStream，两处展示一致。
 */
import { useEffect, useRef, useState, type ReactElement } from 'react';
import { taskApi } from '@/api/tasks';
import type { Task, TaskEvent } from './types';
import { useTaskEventStream, displayArtifactPath } from './useTaskEventStream';
import { Markdown } from '@/components/Markdown';
import { displayTaskTitle } from './taskDisplay';

export interface TaskStreamPanelProps {
  task: Task;
  runId: string;
  /** 该 run 的完整事件（已按 seq 升序，随 SSE 增量更新）。 */
  events: TaskEvent[];
  /** 中断当前 run（运行中可用）。 */
  onInterrupt?: () => void;
}

export function TaskStreamPanel({
  task,
  runId,
  events,
  onInterrupt,
}: TaskStreamPanelProps): ReactElement {
  const { state, statusText, outputLines, toolRows, toolCount, runEndMeta, duration, artifacts, runMessage, runNumber, failedCount } =
    useTaskEventStream(task, runId, events);

  // UX 修复（2026-08-10）：默认折叠为摘要头，运行中也不自动展开——
  // 聊天场景不主动暴露 agent 输出与工具命令（ls . 等），只看头部状态
  // （状态点 + 状态文字 + 耗时 + 工具数）；想看过程点击头部展开。
  const [open, setOpen] = useState(false);
  /** 打开目录/定位文件的状态：'' 空闲 | 'opening' 中 | 错误文案 */
  const [openState, setOpenState] = useState('');

  // 运行中：新事件自动滚动到底（Codex 式跟随执行，仅展开时）。
  const bodyRef = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    const el = bodyRef.current;
    if (el && open && state === 'running') {
      el.scrollTop = el.scrollHeight;
    }
  }, [events.length, open, state]);

  const dotClass =
    state === 'running' ? 'tsc-dot run' : state === 'error' ? 'tsc-dot err' : 'tsc-dot ok';
  // 2026-08-10：失败弱提示——失败的 tool_result 数，外层不再被「completed」掩盖。
  const hasMeta = Boolean(duration || toolCount > 0 || failedCount > 0);
  const meta = (
    <>
      {duration ? `${duration} · ` : ''}
      {toolCount > 0 ? `${toolCount} 次工具调用` : ''}
      {failedCount > 0 && <span className="tsc-meta-fail"> · {failedCount} 次失败重试</span>}
    </>
  );
  // 2026-08-10：续跑摘要——仅在第 2+ 次执行且 run_start 携带本次指令时显示，
  // 解决「任务卡标题永远是首次需求，看不出这次在改什么」的困惑。
  const isContinuation = runNumber > 1;
  const runNote = isContinuation && runMessage ? runMessage : '';

  /** 打开工作目录；path 非空则定位该文件。失败展示内联错误。 */
  const handleOpen = async (path?: string): Promise<void> => {
    if (openState === 'opening') return;
    setOpenState('opening');
    try {
      // 2026-08-10 修复：产物可能是相对路径（LLM 以 cwd=workspace 写盘，如
      // "pomodoro.html"）——点击时基于 workspace 补全为绝对路径再传给后端，
      // 避免后端按自身 cwd 解析导致「不在任务工作目录内」。绝对路径原样传。
      let target = path;
      if (target && !/^[A-Za-z]:[\\/]/.test(target) && !/^[\\/]/.test(target)) {
        const ws = task.workspace.replace(/[\\/]+$/, '');
        target = `${ws}/${target.replace(/^[\\/]+/, '')}`;
      }
      await taskApi.openTaskDir(task.id, target);
      setOpenState('');
    } catch (err) {
      setOpenState(err instanceof Error ? err.message : '打开失败');
    }
  };

  return (
    <div className={`tsc ${state === 'running' ? 'running' : ''}`}>
      {/* 摘要头（点击展开/折叠） */}
      <div className="tsc-head" onClick={() => setOpen((v) => !v)}>
        {/* P3 角色在场：任务卡头部带角色头像（与聊天气泡同一视觉语言） */}
        <span className="tsc-avatar" aria-hidden="true">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round">
            <path d="M20.5 14.5A8.5 8.5 0 1 1 9.5 3.5a7 7 0 0 0 11 11z" />
          </svg>
        </span>
        <span className={dotClass} />
        <span className="tsc-title">
          任务 · {displayTaskTitle(task)}
          {runNumber > 0 && <span className="tsc-run-badge">第 {runNumber} 次</span>}
        </span>
        <span className="tsc-status">{statusText}</span>
        {hasMeta && <span className="tsc-meta">{meta}</span>}
        <span className="tsc-head-actions" onClick={(e) => e.stopPropagation()}>
          {state === 'running' && onInterrupt ? (
            <button className="tsc-int" title="中断执行" onClick={() => onInterrupt()}>
              <svg width="9" height="9" viewBox="0 0 10 10" aria-hidden="true">
                <rect x="2" y="2" width="6" height="6" rx="1.2" fill="currentColor" />
              </svg>
              中断
            </button>
          ) : null}
          <span className={`tsc-chev ${open ? 'open' : ''}`}>▸</span>
        </span>
      </div>

      {/* 2026-08-10：续跑时的「本次执行」指令摘要（第 2+ 次 run 才显示） */}
      {runNote && (
        <div className="tsc-run-note" onClick={() => setOpen((v) => !v)}>
          <span className="tsc-run-note-label">本次执行</span>
          <span className="tsc-run-note-text" title={runNote}>
            {runNote}
          </span>
        </div>
      )}

      {/* 过程流 */}
      {open && (
        <div className="tsc-body" ref={bodyRef}>
          {events.length === 0 ? (
            <div className="tsc-empty">等待智能体开始执行…</div>
          ) : (
            <>
              {outputLines.length > 0 && (
                // 2026-08-10：内核输出折叠——agent 的技术独白（BoxGeometry、
                // 0.48 坐标等）默认收起，只留一行摘要；运行中自动展开跟随进度。
                <details className="tsc-output" open={state === 'running'}>
                  <summary className="tsc-output-summary">
                    AI 过程输出（{outputLines.length} 条）{state === 'running' ? '· 更新中' : ''}
                  </summary>
                  {outputLines.map((line, i) => (
                    <div key={i} className="tsc-output-line">
                      {/* M1：agent 输出为 Markdown，收敛渲染 */}
                      <Markdown content={line} />
                    </div>
                  ))}
                </details>
              )}
              {toolRows.length > 0 && <div className="tsc-tools">{toolRows}</div>}
              {/* 2026-08-10：生成文件列表，点击可在资源管理器中定位 */}
              {state !== 'running' && artifacts.length > 0 && (
                <div className="tsc-artifacts">
                  <div className="tsc-artifacts-title">生成文件</div>
                  <ul className="tsc-artifacts-list">
                    {artifacts.map((p, i) => (
                      <li key={i}>
                        <button
                          type="button"
                          className="artifact-link"
                          title={p}
                          onClick={() => void handleOpen(p)}
                        >
                          {displayArtifactPath(task.workspace, p)}
                        </button>
                      </li>
                    ))}
                  </ul>
                </div>
              )}
              {state !== 'running' && (
                <div className="tsc-summary">
                  <span className={`tsc-s-chip ${state === 'error' ? 'err' : ''}`}>
                    {state === 'error' ? `⚠ ${runEndMeta || '异常终止'}` : `✓ ${runEndMeta || '完成'}`}
                  </span>
                  {toolCount > 0 && <span className="tsc-s-chip">⚙ {toolCount} 次工具调用</span>}
                  {state !== 'error' && (
                    <button
                      type="button"
                      className="s-btn"
                      disabled={openState === 'opening'}
                      onClick={() => void handleOpen()}
                    >
                      {openState === 'opening' ? '打开中…' : '📂 打开工作目录'}
                    </button>
                  )}
                  {openState && <span className="tsc-s-chip err">{openState}</span>}
                </div>
              )}
            </>
          )}
        </div>
      )}
    </div>
  );
}

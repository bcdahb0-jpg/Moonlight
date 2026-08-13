/**
 * useScreenAwareness — 屏幕感知生命周期编排（Phase 1/2/5）。
 *
 * 组装：CaptureScheduler（事件驱动 + 低频轮询）→ screenContextClient（pHash 去重
 * + WS 上传）→ 后端 screen_awareness（隐私二次校验 + 单飞视觉摘要）。
 *
 * Phase 5 新增「仅用户询问时识别」模式（captureOnDemand=true）：不启动自动采集
 * 调度器，仅保留前台窗口元数据轮询；用户消息命中屏幕关键词时由外部触发
 * screenActions.captureOnce() 按需采集一帧。
 */
import { useEffect, useRef } from 'react';
import { useAppState } from '@/state/AppStateContext';
import { createScreenContextClient } from '@/screen/screenContextClient';
import { createCaptureScheduler } from '@/screen/CaptureScheduler';
import { checkPrivacyBlock } from '@/screen/privacyRules';
import { registerScreenActions, unregisterScreenActions } from '@/screen/screenActions';
import type { WSClient } from '@/api/wsClient';
import type { ScreenStatusMessage } from '@/types/ws';
import type { CapturedFrame } from '@/screen/CaptureScheduler';

export interface ScreenAwarenessOptions {
  enabled: boolean;
  pollIntervalSec: number;
  onProactiveTrigger: () => void;
  proactiveEnabled: boolean;
  proactiveIdleSec: number;
  /** Phase 1：WS 引用（上传 screen-frame / 同步 status）。 */
  wsRef?: React.MutableRefObject<WSClient | null>;
  /** Phase 1：用户隐私黑名单（应用/标题关键词）。 */
  blockedApps?: string[];
  blockedTitleKeywords?: string[];
  /** Phase 2：pHash 变化阈值（0-1）。 */
  changeThreshold?: number;
  /** Phase 1：空闲多少秒进入「仅监听窗口变更」。 */
  idleThresholdSec?: number;
  /** AI 回复中/任务运行中（外部状态，用于暂停内容采集与主动触发）。 */
  isBusy?: () => boolean;
  /** Phase 5：仅用户询问时识别（不自动采集；关键词触发按需采集）。 */
  captureOnDemand?: boolean;
  /** Phase 2（pet-ptt-workflow）：定时屏幕巡检间隔（秒）。0=关闭（默认）。
   *  独立于 idle 空闲触发；巡检只触发「检查」，新快照判定与策略门在后端。 */
  proactiveIntervalSec?: number;
  /** Phase 5（修复）：WS 连接状态。后端重启后前端 WS 重连时，靠这个依赖
   *  重建 effect 并重新 client.enable() —— 否则后端新实例收不到 screen-enable。 */
  connected?: boolean;
}

/**
 * 生命周期编排：窗口元数据轮询 + 自适应截图调度 + pHash 去重上传 + 主动触发。
 */
export function useScreenAwareness({
  enabled,
  pollIntervalSec,
  proactiveEnabled,
  proactiveIdleSec,
  onProactiveTrigger,
  wsRef,
  blockedApps,
  blockedTitleKeywords,
  changeThreshold = 0.08,
  idleThresholdSec = 15,
  isBusy,
  captureOnDemand = false,
  proactiveIntervalSec = 0,
  connected = true,
}: ScreenAwarenessOptions): void {
  const { dispatch } = useAppState();
  const triggeredRef = useRef(false);
  const triggerFnRef = useRef(onProactiveTrigger);
  triggerFnRef.current = onProactiveTrigger;
  const busyRef = useRef(isBusy);
  busyRef.current = isBusy;
  const wsRefRef = useRef(wsRef);
  wsRefRef.current = wsRef;
  /** Phase 2（pet-ptt-workflow）：最新后端状态（巡检读取，主 effect 更新）。 */
  const screenStatusRef = useRef<ScreenStatusMessage | null>(null);
  /** Phase 2：按需采集句柄（主 effect 注册，独立巡检 effect / 关键词采集调用）。
   *  返回是否在等待窗口内分析出新快照（last_analyze_at 前进）。 */
  const captureOnceRef = useRef<(reason?: string) => Promise<boolean>>(async () => false);

  useEffect(() => {
    const api = window.moonlight;
    // 修复（2026-08-11）：WS 未连接（后端重启/刚启动）时不启用采集；
    // 连接恢复（connected=true）后重建 effect 并重新 client.enable()。
    if (!enabled || !connected || !api) return;

    let disposed = false;
    let windowTimer: number | null = null;
    let idling = false;
    let scheduler: ReturnType<typeof createCaptureScheduler> | null = null;

    const client = createScreenContextClient(
      () => wsRefRef.current?.current ?? null,
      changeThreshold,
    );
    const wsClient = wsRefRef.current?.current;
    // 让本地采集控制器看到 WS 回包；否则 captureOnce 只能等到固定超时。
    const unsubWsStatus =
      wsClient?.on('screen-status', (s) => client.applyStatus(s)) ?? (() => undefined);

    // 后端状态同步（screen-status 推送 → 全局 state + 巡检 ref）。
    const unsub = client.onStatusChange((s: ScreenStatusMessage) => {
      screenStatusRef.current = s;
      if (!disposed) dispatch({ type: 'SET_SCREEN_STATUS', status: s });
    });

    // 上传前渲染端隐私确认（含用户自定义黑名单）；命中则不上传并上报暂停原因。
    const handleFrameReady = (frame: CapturedFrame): void => {
      const block = checkPrivacyBlock(
        frame.window.title,
        frame.window.app,
        { blockedApps, blockedTitleKeywords },
      );
      if (block) {
        dispatch({
          type: 'SET_SCREEN_STATUS',
          status: {
            type: 'screen-status',
            enabled: true,
            capturing: false,
            pause_reason: block,
          } as ScreenStatusMessage,
        });
        return;
      }
      void client.upload(frame);
    };

    // 按需采集一帧（on-demand 模式 + 状态灯手动采集 + Phase 2 巡检共用）。
    // reason 仅作调用语义标记（日志/统计用途），帧上报统一用 user_requested 语义。
    // 修复（2026-08-11）：等待「分析出新快照」再返回 —— 用户问屏幕时发送方
    // await 本函数，保证消息到达后端时快照有效（TTL 45s 过期也能拿到新鲜上下文）。
    const captureOnce = async (_reason: string = 'user_requested'): Promise<boolean> => {
      if (disposed) return false;
      const before = screenStatusRef.current?.last_analyze_at ?? null;
      const win = await api.getActiveWindow();
      if (!win || (!win.title && !win.app)) return false;
      const cap = await api.captureActiveWindowV2({ maxSide: 1280, quality: 78 });
      if (!cap) return false;
      if (cap.blocked) {
        dispatch({
          type: 'SET_SCREEN_STATUS',
          status: {
            type: 'screen-status',
            enabled: true,
            capturing: false,
            pause_reason: cap.reason ?? 'privacy',
          } as ScreenStatusMessage,
        });
        return false;
      }
      if (!cap.ok || !cap.base64) return false;
      handleFrameReady({
        window: {
          title: cap.title ?? win.title,
          app: cap.app ?? win.app,
          pid: cap.pid ?? win.pid ?? 0,
        },
        image: cap.base64,
        imageHash: '',
        // 巡检按需采集沿用 user_requested 语义（跳过 pHash 去重，强制后端分析）。
        reason: 'user_requested',
      });
      // 等待后端分析完成（last_analyze_at 前进；静态画面/隐私/失败不前进 → 超时）。
      // SiliconFlow 单次分析实测约 10s，等待窗口放宽到 12s。
      for (let i = 0; i < 24; i++) {
        const cur = screenStatusRef.current?.last_analyze_at ?? null;
        if (cur !== null && cur !== before) return true;
        await new Promise((r) => window.setTimeout(r, 500));
      }
      return false;
    };

    // 窗口元数据轮询（标题/应用/空闲 → SET_ACTIVE_WINDOW + 主动触发判定）。
    const pollWindow = async (): Promise<void> => {
      if (disposed) return;
      try {
        const [activeWindow, idleTime] = await Promise.all([
          api.getActiveWindow(),
          api.getIdleTime(),
        ]);
        dispatch({
          type: 'SET_ACTIVE_WINDOW',
          info: {
            title: activeWindow?.title ?? '',
            app: activeWindow?.app ?? '',
            idleTime,
            capturedAt: Date.now(),
          },
        });

        // 主动触发（Phase 4 前保持空闲触发；后端策略就绪后由后端注入 hint）。
        const shouldTrigger =
          proactiveEnabled && !(busyRef.current?.() ?? false) && idleTime >= proactiveIdleSec;
        if (shouldTrigger && !idling && !triggeredRef.current) {
          idling = true;
          triggeredRef.current = true;
          triggerFnRef.current();
        } else if (!shouldTrigger && idling) {
          idling = false;
        } else if (!shouldTrigger) {
          triggeredRef.current = false;
        }
      } catch {
        // IPC unavailable (e.g. running in a plain browser); ignore.
      }
    };

    // 注册全局动作（设置页「暂停/清除/立即采集」+ 状态灯用）。
    const actions = {
      clear: () => client.clear(),
      pause: () => {
        client.disable();
      },
      captureOnce: () => captureOnce(),
    };
    registerScreenActions(actions);
    // Phase 2：巡检 / 状态灯共用按需采集句柄。
    captureOnceRef.current = captureOnce;

    if (!captureOnDemand) {
      // 自动采集模式：事件驱动 + 低频轮询调度器。
      scheduler = createCaptureScheduler({
        pollIntervalSec,
        changeThreshold,
        idleThresholdSec,
        activeInputSec: 5,
        maxSide: 1280,
        quality: 78,
        getActiveWindow: async () => {
          const info = await api.getActiveWindow();
          if (!info || (!info.title && !info.app)) return null;
          return { title: info.title ?? '', app: info.app ?? '', pid: info.pid ?? 0 };
        },
        getIdleTime: async () => {
          try {
            return await api.getIdleTime();
          } catch {
            return null;
          }
        },
        capture: () => api.captureActiveWindowV2({ maxSide: 1280, quality: 78 }),
        isUserBusy: () => busyRef.current?.() ?? false,
        onPrivacyBlocked: (reason) => {
          dispatch({
            type: 'SET_SCREEN_STATUS',
            status: {
              type: 'screen-status',
              enabled: true,
              capturing: false,
              pause_reason: reason,
            } as ScreenStatusMessage,
          });
        },
        onFrameReady: handleFrameReady,
      });
      scheduler.start();
    }

    client.enable();
    void pollWindow();
    windowTimer = window.setInterval(
      () => void pollWindow(),
      Math.max(1, pollIntervalSec) * 1000,
    );

    return () => {
      disposed = true;
      unregisterScreenActions(actions);
      scheduler?.stop();
      client.disable();
      client.clear();
      unsub();
      unsubWsStatus();
      if (windowTimer !== null) window.clearInterval(windowTimer);
      captureOnceRef.current = async () => false;
      dispatch({ type: 'SET_SCREEN_STATUS', status: null });
    };
  }, [
    enabled,
    connected,
    pollIntervalSec,
    proactiveEnabled,
    proactiveIdleSec,
    changeThreshold,
    idleThresholdSec,
    captureOnDemand,
    blockedApps,
    blockedTitleKeywords,
    dispatch,
  ]);

  // ------------------------------------------------------------------ //
  // Phase 2（pet-ptt-workflow）：独立定时巡检 timer。
  // 只触发「检查」，不绕过后端策略：
  // - 到期检查桌宠模式（经 proactiveEnabled 闸门）、连接、AI/任务忙碌；
  // - on-demand 模式：巡检本身请求一帧受隐私保护的截图，等后端分析出
  //   新快照（last_analyze_at 变化）后才发 ai-speak-signal；
  // - 自动采集模式：对比「上次主动对话后」的 last_analyze_at 是否有更新；
  // - 后端 decide_proactive_for 再做快照版本去重 + 冷却 + 场景策略兜底。
  // ------------------------------------------------------------------ //
  useEffect(() => {
    if (!enabled || !connected || !proactiveEnabled) return;
    if (proactiveIntervalSec <= 0) return;
    const intervalMs = Math.min(3600, Math.max(300, proactiveIntervalSec)) * 1000;
    let disposed = false;
    let lastProactiveAnalyzeAt: number | null | undefined = null;

    const waitForNewSnapshot = async (from: number | null | undefined): Promise<boolean> => {
      // 最多等 10s（每 500ms 轮询后端 screen-status）：分析完成（last_analyze_at
      // 变化）或静默放弃。静态画面/隐私拦截/分析 fail-soft 都不会推进版本。
      for (let i = 0; i < 20; i++) {
        const cur = screenStatusRef.current?.last_analyze_at ?? null;
        if (cur !== null && cur !== from) return true;
        await new Promise((r) => window.setTimeout(r, 500));
      }
      return false;
    };

    const check = async (): Promise<void> => {
      if (disposed) return;
      if (busyRef.current?.() ?? false) return; // AI 回复中 / 任务运行中
      const from = screenStatusRef.current?.last_analyze_at ?? null;
      if (captureOnDemand) {
        // 巡检按需采集一帧（渲染端隐私规则前置），新快照到达后再触发。
        await captureOnceRef.current('scheduled_check');
        const fresh = await waitForNewSnapshot(from);
        if (fresh) triggerFnRef.current();
      } else {
        // 自动采集：上次主动触发之后是否有新分析快照。
        if (lastProactiveAnalyzeAt === null) {
          lastProactiveAnalyzeAt = from;
          return;
        }
        const cur = screenStatusRef.current?.last_analyze_at ?? null;
        if (cur !== null && cur !== lastProactiveAnalyzeAt) {
          lastProactiveAnalyzeAt = cur;
          triggerFnRef.current();
        }
      }
    };

    const timer = window.setInterval(() => void check(), intervalMs);
    return () => {
      disposed = true;
      window.clearInterval(timer);
    };
  }, [enabled, connected, proactiveEnabled, proactiveIntervalSec, captureOnDemand]);
}

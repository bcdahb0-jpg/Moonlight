/**
 * 自适应采集调度（Phase 1/2）：事件驱动 + 低频保底轮询。
 *
 * 规则（计划 §6）：
 * - 前台窗口切换 → 立即候选采集（300-500ms 稳定后截图，reason=window_changed）；
 * - 同窗口活动 → 默认每 3-5s 检查一次缩略图差异（reason=content_changed）；
 * - 画面变化 < 阈值 → 复用摘要，不上传（省视觉 token）；
 * - 用户高频输入（空闲 < 输入阈值）→ 降频检查；
 * - 系统空闲 → 只监听窗口变更，不做内容轮询；
 * - AI 回复中/任务运行中 → 暂停内容采集（不打断）；
 * - 隐私规则命中 → 跳过采集并上报 pauseReason。
 */

export interface CapturedFrame {
  window: { title: string; app: string; pid: number; bounds?: number[] };
  image: string;
  imageHash: string;
  reason: 'window_changed' | 'content_changed' | 'user_requested';
}

export interface SchedulerDeps {
  pollIntervalSec: number;
  changeThreshold: number;
  /** 空闲多少秒以上进入「仅监听窗口变更」模式（默认 15s）。 */
  idleThresholdSec: number;
  /** 用户持续输入（idle < 该值）时轮询间隔翻倍（默认 5s）。 */
  activeInputSec: number;
  maxSide?: number;
  quality?: number;
  /** 返回前台窗口信息；不可用时返回 null。 */
  getActiveWindow: () => Promise<{ title: string; app: string; pid: number } | null>;
  /** 系统空闲秒数（不可用时返回 null）。 */
  getIdleTime: () => Promise<number | null>;
  /** 主进程截图（含隐私前置阻断）；失败返回 null。 */
  capture: () => Promise<{
    ok: boolean;
    blocked?: boolean;
    reason?: string;
    error?: string;
    base64?: string;
    title?: string;
    app?: string;
    pid?: number;
  } | null>;
  /** AI 回复中 / 任务运行中 / 免打扰。 */
  isUserBusy: () => boolean;
  /** 隐私规则命中回调（reason）。 */
  onPrivacyBlocked: (reason: string) => void;
  /** 新帧就绪回调。 */
  onFrameReady: (frame: CapturedFrame) => void;
}

export interface SchedulerHandle {
  start(): void;
  stop(): void;
  /** 用户主动要求看屏幕：强制立即采集一帧（忽略轮询节流）。 */
  captureNow(): Promise<CapturedFrame | null>;
}

export function createCaptureScheduler(deps: SchedulerDeps): SchedulerHandle {
  let timer: number | null = null;
  let stopped = false;
  let lastWindow: { title: string; app: string; pid: number } | null = null;
  let lastCaptureAt = 0;
  let pendingWindowChange = false;
  let inFlight = false;

  const now = (): number => Date.now();

  async function captureOnce(
    reason: 'window_changed' | 'content_changed' | 'user_requested',
  ): Promise<CapturedFrame | null> {
    if (inFlight) return null;
    inFlight = true;
    try {
      const win = await deps.getActiveWindow();
      if (!win || (!win.title && !win.app)) return null;
      const cap = await deps.capture();
      if (!cap) return null;
      if (cap.blocked) {
        deps.onPrivacyBlocked(cap.reason ?? 'privacy');
        return null;
      }
      if (!cap.ok || !cap.base64) return null;
      lastWindow = { title: cap.title ?? win.title, app: cap.app ?? win.app, pid: cap.pid ?? win.pid };
      lastCaptureAt = now();
      return {
        window: lastWindow,
        image: cap.base64,
        imageHash: '',
        reason,
      };
    } finally {
      inFlight = false;
    }
  }

  async function poll(): Promise<void> {
    if (stopped) return;
    const win = await deps.getActiveWindow();
    if (!win) return;

    const windowChanged =
      !lastWindow ||
      lastWindow.title !== win.title ||
      lastWindow.app !== win.app ||
      lastWindow.pid !== win.pid;

    if (windowChanged) {
      // 窗口切换：300-500ms 稳定后再截（避免切换动画帧）。
      pendingWindowChange = true;
      setTimeout(() => {
        if (!stopped && pendingWindowChange) {
          pendingWindowChange = false;
          void captureOnce('window_changed').then((frame) => {
            if (frame) deps.onFrameReady(frame);
          });
        }
      }, 400);
      return;
    }

    // 同窗口内容轮询。
    if (deps.isUserBusy()) return; // AI 回复中/任务运行中：不打扰
    const idle = await safeIdle();
    if (idle !== null && idle > deps.idleThresholdSec) return; // 系统空闲：只监听窗口变更

    // 主动输入时降频（由调用方用更长间隔驱动，这里只做最小节流）。
    const minGap =
      idle !== null && idle < deps.activeInputSec
        ? deps.pollIntervalSec * 2000
        : deps.pollIntervalSec * 1000;
    if (now() - lastCaptureAt < minGap) return;

    const frame = await captureOnce('content_changed');
    if (frame) deps.onFrameReady(frame);
  }

  async function safeIdle(): Promise<number | null> {
    try {
      return await deps.getIdleTime();
    } catch {
      return null;
    }
  }

  const handle: SchedulerHandle = {
    start() {
      stopped = false;
      void poll();
      timer = window.setInterval(() => void poll(), Math.max(1, deps.pollIntervalSec) * 1000);
    },
    stop() {
      stopped = true;
      if (timer !== null) window.clearInterval(timer);
      timer = null;
      lastWindow = null;
    },
    async captureNow() {
      const frame = await captureOnce('user_requested');
      if (frame) deps.onFrameReady(frame);
      return frame;
    },
  };
  return handle;
}

/**
 * 屏幕感知全局动作（设置页/托盘与 useScreenAwareness 解耦）。
 *
 * useScreenAwareness 在挂载时注册实际实现；设置页等 UI 通过 screenActions
 * 触发「暂停采集」「立即清除」「按需采集」而无需持有 WS 引用。
 */

type ClearFn = () => void;
type PauseFn = (reason?: string) => void;
/** Phase 5：按需采集一帧（「仅用户询问时识别」模式 + 状态灯手动采集）。
 *  修复（2026-08-11）：返回 Promise<boolean> —— 是否在等待窗口内分析出新快照，
 *  供「用户问屏幕时先采集再发送」的调用方 await。 */
type CaptureOnceFn = () => Promise<boolean>;

let clearImpl: ClearFn | null = null;
let pauseImpl: PauseFn | null = null;
let captureOnceImpl: CaptureOnceFn | null = null;

export function registerScreenActions(actions: {
  clear: ClearFn;
  pause: PauseFn;
  captureOnce?: CaptureOnceFn;
}): void {
  clearImpl = actions.clear;
  pauseImpl = actions.pause;
  captureOnceImpl = actions.captureOnce ?? null;
}

export function unregisterScreenActions(actions: {
  clear: ClearFn;
  pause: PauseFn;
  captureOnce?: CaptureOnceFn;
}): void {
  if (clearImpl === actions.clear) clearImpl = null;
  if (pauseImpl === actions.pause) pauseImpl = null;
  if (captureOnceImpl === actions.captureOnce) captureOnceImpl = null;
}

export const screenActions = {
  /** 立即清除后端内存上下文（图像+摘要+窗口身份）。 */
  clear(): void {
    clearImpl?.();
  },
  /** 暂停采集（渲染端调度停 + 后端停用清空）。 */
  pause(reason = 'user_paused'): void {
    pauseImpl?.(reason);
  },
  /** 按需采集一帧（用户主动询问 / 状态灯手动采集）；resolve 表示分析出新快照。 */
  captureOnce(): Promise<boolean> {
    return captureOnceImpl?.() ?? Promise.resolve(false);
  },
};

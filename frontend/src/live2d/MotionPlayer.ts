/**
 * MotionPlayer（P1 口型与连续动作）
 *
 * 消费 `/api/expression/motion-plan` 的逐秒参数帧，在 TTS 播放期间按
 * secondIndex 逐帧平滑插值写入 Live2D 参数（参考 SoulLink controller.js 的
 * easeInOutCubic 思路：rAF 逐帧从当前值插值到目标值）。
 *
 * 播放驱动：
 * - `play(plan, adapter)`：开始播放（同一时间只有一个会话，重复调用会先停旧的）
 * - `stop()`：清空参数帧并复位（interrupt / 播放结束调用）
 * - 每帧 duration 固定 1s（与后端帧协议一致）；easing 由 ExpressionConfig 控制
 */
import type { ExpressionFrame } from '@/api/rest';
import type { Live2DAdapter } from './Live2DAdapter';

/** easeInOutCubic（与 SoulLink EASING_FUNCTIONS 一致）。 */
export function easeInOutCubic(t: number): number {
  return t < 0.5 ? 4 * t * t * t : (t - 1) * (2 * t - 2) * (2 * t - 2) + 1;
}

const FRAME_DURATION_MS = 1000;

export class MotionPlayer {
  private adapter: Live2DAdapter | null = null;
  private frameId = 0;
  private startAt = 0;
  private frames: ExpressionFrame[] = [];
  private currentIndex = -1;
  private fromParams: Record<string, number> = {};
  private toParams: Record<string, number> = {};
  private easing = true;
  private running = false;

  get isPlaying(): boolean {
    return this.running;
  }

  /** 播放参数帧序列；easing=false 时直接跳帧（关平滑）。 */
  play(plan: { frames: ExpressionFrame[] }, adapter: Live2DAdapter | null, easing = true): void {
    this.stop();
    if (!adapter?.applyExternalParams || plan.frames.length === 0) return;
    this.adapter = adapter;
    this.frames = [...plan.frames].sort((a, b) => a.secondIndex - b.secondIndex);
    this.easing = easing;
    this.currentIndex = -1;
    this.running = true;
    this.startAt = performance.now();
    this.frameId = requestAnimationFrame(this.frame.bind(this));
  }

  stop(): void {
    if (this.frameId) cancelAnimationFrame(this.frameId);
    this.frameId = 0;
    this.running = false;
    // 复位外部参数（引擎/模型回到自身控制）
    this.adapter?.applyExternalParams?.({});
    this.adapter = null;
    this.frames = [];
  }

  private frame(now: number): void {
    if (!this.running) return;
    const elapsed = now - this.startAt;
    const secondIndex = Math.floor(elapsed / FRAME_DURATION_MS);

    if (secondIndex !== this.currentIndex) {
      this.currentIndex = secondIndex;
      const frame = this.frames.find((f) => f.secondIndex === secondIndex);
      if (frame) {
        this.fromParams = this.toParams;
        this.toParams = frame.parameters;
      } else {
        // 该秒无帧：保持上一帧（target 不变），避免抖动
        this.fromParams = this.toParams;
      }
    }

    const targetIndex = this.frames.find((f) => f.secondIndex === secondIndex);
    if (targetIndex) {
      // 帧内平滑插值（当前帧的进度）
      const progress = Math.min(1, (elapsed - secondIndex * FRAME_DURATION_MS) / FRAME_DURATION_MS);
      const eased = this.easing ? easeInOutCubic(progress) : progress;
      const merged: Record<string, number> = {};
      const ids = new Set([...Object.keys(this.fromParams), ...Object.keys(this.toParams)]);
      for (const id of ids) {
        const from = this.fromParams[id] ?? 0;
        const to = this.toParams[id] ?? 0;
        merged[id] = from + (to - from) * eased;
      }
      this.adapter?.applyExternalParams?.(merged);
    }

    if (elapsed < this.frames.length * FRAME_DURATION_MS + 200) {
      this.frameId = requestAnimationFrame(this.frame.bind(this));
    } else {
      // 帧已播完：保持最后一帧参数（音频可能还在播），由调用方在
      // onItemEnd/onStop 时调 stop() 复位。
      this.applyLastFrame();
    }
  }

  /** 帧播完后保持最后一帧参数（避免表情突然跳回）。 */
  private applyLastFrame(): void {
    const last = this.frames[this.frames.length - 1];
    if (last) this.adapter?.applyExternalParams?.(last.parameters);
  }
}

export default MotionPlayer;

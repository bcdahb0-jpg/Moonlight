import type { Live2DAdapter, VisemeVector } from './Live2DAdapter';

/**
 * LipSyncDriver — 口型驱动层（三层架构 Level 1）。
 *
 * 职责：
 * 1. 平滑：rAF 60fps 连续一阶滤波（attack/release：开口快、闭口慢），
 *    消除 AudioPlayer 20ms 回调带来的跳变 → 嘴巴不再抽搐。
 * 2. 说话状态机：滞回阈值（进 Speaking 0.05 / 回 Idle 0.02 + 200ms 防抖），
 *    避免音量抖动导致动作反复切换。
 * 3. 动作联动：Speaking 时播放模型 Talk 动作组，Idle 时恢复 Idle 动作。
 *
 * 平滑放驱动层而不是 adapter 内部的原因：`Live2DModelAdapter` 与
 * `SoullinkAdapter` 共享同一套平滑逻辑，接口零破坏，Soullink 自动受益
 * （其 setLipSync 内部已有 voiceActive 状态机，收到平滑值后自然过渡）。
 *
 * 算法参考：reference/daidai-live2d-pet/src/renderer/app.js:1123-1138
 *   target = clamp((rms - 0.018) * 9.5) ** 0.72   // 弱音增益、强音压缩
 *   smoothing = target > last ? 0.65 : 0.32       // 开口快、闭口慢
 */
export interface LipSyncOptions {
  /** 说话动作组名（Cubism 惯例 Talk；模型缺失时 playMotion 内部静默降级）。 */
  talkMotionGroup?: string;
  /** 进入 Speaking 的映射后音量阈值。 */
  speakThreshold?: number;
  /** 回到 Idle 的映射后音量阈值（滞回，应小于 speakThreshold）。 */
  releaseThreshold?: number;
  /** 低于 releaseThreshold 持续多久才回 Idle（防抖）。 */
  idleHoldMs?: number;
  /** RMS 非线性映射系数：target = clamp((v - mapMin) * mapScale) ** mapPower。 */
  mapMin?: number;
  mapScale?: number;
  mapPower?: number;
  /** 一阶滤波系数：开口（attack，更大 = 更快张开）。 */
  attack?: number;
  /** 一阶滤波系数：闭口（release，更小 = 更慢闭合，更自然）。 */
  release?: number;
}

const DEFAULT_OPTIONS: Required<LipSyncOptions> = {
  talkMotionGroup: 'Talk',
  speakThreshold: 0.05,
  releaseThreshold: 0.02,
  // 900ms：句间短停顿（呼吸/换气 <0.9s）不打断动作状态，避免每句急抬头急恢复；
  // 只有真正说完（>0.9s 无声）才回静止待机。
  idleHoldMs: 900,
  mapMin: 0.018,
  mapScale: 9.5,
  mapPower: 0.72,
  attack: 0.65,
  release: 0.32,
};

export class LipSyncDriver {
  private readonly adapter: () => Live2DAdapter | null;
  private readonly opts: Required<LipSyncOptions>;

  /** AudioPlayer 传来的原始音量（0..1，20ms 粒度）。 */
  private targetVoice = 0;
  /** 当前 slice 的音素级口型向量（Phase 2，可选）。 */
  private currentViseme: VisemeVector | null = null;
  /** rAF 每帧平滑逼近 target 的映射后嘴部值。 */
  private smoothVoice = 0;

  private speaking = false;
  private idleTimer: number | null = null;
  private rafId: number | null = null;
  private destroyed = false;

  // Phase B：说话随机动作调度（idle 随机已移除——模型 Idle 组为静止动作）
  /** 进入说话时间（performance.now，ms）。 */
  private speakStartedAt = 0;
  /** 说话中下一次随机换动作的时间。 */
  private speakMotionNextAt = 0;

  constructor(adapter: () => Live2DAdapter | null, options?: LipSyncOptions) {
    this.adapter = adapter;
    this.opts = { ...DEFAULT_OPTIONS, ...options };
  }

  // ------------------------------------------------------------------ //
  // 外部接口（AudioPlayer 回调接线）
  // ------------------------------------------------------------------ //

  /** 由 AudioPlayer.onVolume 驱动（0..1，20ms 粒度；可附带音素级口型）。 */
  onVolume(value: number, viseme?: VisemeVector | null): void {
    this.targetVoice = value;
    this.currentViseme = viseme ?? null;
    this.ensureLoop();
  }

  /** 由 AudioPlayer.onItemStart 驱动（开始播一条音频）。 */
  onItemStart(): void {
    this.ensureLoop();
  }

  /**
   * 由 AudioPlayer.onItemEnd 驱动。
   * immediate=true（打断 stop() 场景）：立即复位 Idle；
   * false：等 idleHoldMs 防抖后回 Idle（target 此时已被置 0，平滑自然衰减）。
   */
  onItemEnd(immediate = false): void {
    if (immediate) {
      this.resetToIdle();
      return;
    }
    this.ensureLoop();
    if (this.idleTimer === null) {
      this.idleTimer = window.setTimeout(() => this.resetToIdle(), this.opts.idleHoldMs);
    }
  }

  /** 组件卸载时释放定时器与动画帧。 */
  destroy(): void {
    this.destroyed = true;
    if (this.rafId !== null) {
      cancelAnimationFrame(this.rafId);
      this.rafId = null;
    }
    if (this.idleTimer !== null) {
      window.clearTimeout(this.idleTimer);
      this.idleTimer = null;
    }
    this.adapter()?.setLipSync(0);
  }

  // ------------------------------------------------------------------ //
  // rAF 平滑循环
  // ------------------------------------------------------------------ //

  private ensureLoop(): void {
    if (this.rafId !== null || this.destroyed) return;
    this.rafId = requestAnimationFrame(this.frame.bind(this));
  }

  private stopLoop(): void {
    if (this.rafId !== null) {
      cancelAnimationFrame(this.rafId);
      this.rafId = null;
    }
  }

  private readonly frame = (): void => {
    if (this.destroyed) return;

    const { speakThreshold, releaseThreshold, attack, release, mapMin, mapScale, mapPower } =
      this.opts;

    // 1) RMS → 非线性映射（daidai app.js:1132-1133）
    const mapped = Math.pow(
      Math.min(1, Math.max(0, (this.targetVoice - mapMin) * mapScale)),
      mapPower,
    );

    // 2) 一阶低通：开口快 / 闭口慢（daidai app.js:1134-1135）
    const k = mapped > this.smoothVoice ? attack : release;
    this.smoothVoice += (mapped - this.smoothVoice) * k;

    // 3) 输出到渲染层（音素级口型随帧透传；Soullink 实现自动忽略）
    this.adapter()?.setLipSync(this.smoothVoice, this.currentViseme);

    // 4) 说话状态机（滞回 + 防抖）
    if (!this.speaking && this.smoothVoice >= speakThreshold) {
      this.enterSpeaking();
    } else if (this.speaking && this.smoothVoice < releaseThreshold) {
      if (this.idleTimer === null) {
        this.idleTimer = window.setTimeout(
          () => this.resetToIdle(),
          this.opts.idleHoldMs,
        );
      }
    } else if (this.speaking && this.smoothVoice >= releaseThreshold) {
      this.clearIdleTimer();
      // 说话中每 3.2-5.8s 随机换一个说话动作（daidai scheduleSpeakingBodyMotion）
      const now = performance.now();
      if (now >= this.speakMotionNextAt) {
        this.adapter()?.playRandomMotion('speaking');
        this.speakMotionNextAt = now + 3200 + Math.random() * 2600;
      }
    }

    // 5) 循环管理：完全静音且不在说话态 → 停帧省电
    const quiet = this.smoothVoice < 0.005 && this.targetVoice < 0.005;
    if (quiet && !this.speaking && this.idleTimer === null) {
      this.stopLoop();
      return;
    }
    this.rafId = requestAnimationFrame(this.frame);
  };

  // ------------------------------------------------------------------ //
  // 状态切换
  // ------------------------------------------------------------------ //

  private enterSpeaking(): void {
    this.clearIdleTimer();
    this.speaking = true;
    this.speakStartedAt = performance.now();
    this.speakMotionNextAt = this.speakStartedAt + 3200 + Math.random() * 2600;
    // 优先随机说话动作（多组名兼容），模型没有候选组时回退固定 Talk
    if (!this.adapter()?.playRandomMotion('speaking')) {
      this.adapter()?.playMotion(this.opts.talkMotionGroup, 0);
    }
  }

  private resetToIdle(): void {
    this.clearIdleTimer();
    this.speaking = false;
    this.targetVoice = 0;
    this.currentViseme = null;
    this.smoothVoice = 0;
    // 回到模型 Idle 组（静止动作：calm_idle Loop 永不完成 → 不再随机换动作）
    this.adapter()?.stopMotion();
    this.adapter()?.setLipSync(0);
    this.stopLoop();
  }

  private clearIdleTimer(): void {
    if (this.idleTimer !== null) {
      window.clearTimeout(this.idleTimer);
      this.idleTimer = null;
    }
  }
}

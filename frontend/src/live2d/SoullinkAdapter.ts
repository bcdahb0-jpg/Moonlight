import * as PIXI from 'pixi.js';
import {
  loadModelProfile,
  motionStylePresets,
  SoullinkRuntime,
} from '@soullink-emotion/engine';
import type {
  AudioLevelAnalyzer,
  EmotionIntent,
  Live2DParamState,
  ModelProfile,
  NativeAnimationDirective,
  RuntimeSnapshot,
} from '@soullink-emotion/engine';
import { ensureCubismCore } from './cubismCore';
import { neutralIntent, toEmotionIntent } from './emotionBridge';
import { HeadMotion, isBodyParam, isHeadParam, pickRandomMotion } from './motionUtils';
import type { Live2DAdapter } from './Live2DAdapter';
import type { EmotionMap, TapMotions } from './Live2DModelAdapter';

type Live2DModelInstance = import('pixi-live2d-display/cubism4').Live2DModel;

interface SoullinkAdapterOptions {
  canvas: HTMLCanvasElement;
  modelUrl: string;
  profileUrl: string;
  /** 直接注入 profile 对象（默认 profile 生成器产物）；优先于 profileUrl 加载。 */
  profile?: ModelProfile;
  emotionMap?: EmotionMap;
  tapMotions?: TapMotions;
}

interface CubismCoreLike {
  getParameterIndex(id: string): number;
  setParameterValueById(id: string, value: number, weight?: number): void;
}

/**
 * Live2D 适配器：用 Soullink Emotion SDK 引擎驱动逐帧自然表情/动作/口型。
 *
 * 与 {@link Live2DModelAdapter} 保持完全相同的公共 API，内部差异：
 * - 加载模型的 `soullink.profile.json`，构造 `SoullinkRuntime`
 * - 每帧 `runtime.update(t, dt)` 得到 FACS 表情、分层动作与口型合成出的
 *   `live2dParams`（Record<CubismId, number>），在 Cubism `beforeModelUpdate`
 *   hook 中写入 coreModel
 * - `setExpression(emotion)` 被桥接为 `runtime.triggerIntent(EmotionIntent)`，
 *   由引擎做连续的 VAD 过渡，而不是生硬地切换预设 expression
 * - 引擎原生动画（expression/motion 指令）通过 `model.expression/motion` 应用
 */
export class SoullinkAdapter implements Live2DAdapter {
  private app: PIXI.Application | null = null;
  private model: Live2DModelInstance | null = null;
  private runtime: SoullinkRuntime | null = null;
  private readonly canvas: HTMLCanvasElement;
  private readonly modelUrl: string;
  private readonly profileUrl: string;
  private readonly injectedProfile?: ModelProfile;
  private readonly emotionMap: EmotionMap;
  private readonly tapMotions: TapMotions;

  // 帧循环状态
  private startedAt = 0;
  private previousTime = 0;
  private frameId = 0;
  private destroyed = false;

  // 口型状态
  private voiceLevel = 0;
  private voiceActive = false;

  // 说话头部微动（Phase B2，引擎 idle 头部输出为 0 的补丁）
  private readonly headMotion = new HeadMotion();

  // 引擎最新一帧参数（在 beforeModelUpdate hook 中写入）
  private latestParams: Live2DParamState = {};
  private lastNativeAnimToken = -1;
  private suppressedParamIds: ReadonlySet<string> = new Set();
  // P1 连续动作：外部参数帧（motion-plan），叠加在引擎输出之上
  private externalParams: Record<string, number> = {};
  // P1.5 外观 / 滤镜 / 遮罩状态
  private appearance: { scale: number; posX: number; posY: number; opacity: number } = {
    scale: 100,
    posX: 50,
    posY: 60,
    opacity: 100,
  };
  private visualFx: { enabled: boolean; brightness: number; colorTemp: number; saturation: number } = {
    enabled: false,
    brightness: 1,
    colorTemp: 6500,
    saturation: 1,
  };
  private colorFilter: PIXI.ColorMatrixFilter | null = null;
  private occlusion: { x: number; y: number }[] | null = null;
  private occlusionGraphics: PIXI.Graphics | null = null;

  // ---- 自研眨眼（引擎 0.1.0-beta.1 的 BlinkController 缺陷补丁）----
  // Node 探针实证：idle 的 eyeBlinkL/R FACS 恒 0，12s 无一次眨眼。
  // 此处自己调度眨眼（2.5-6s 随机间隔、150ms 正弦波形），在写参数时把
  // ParamEyeLOpen/ROpen 按 (1 - blinkValue) 压低，实现自然眨眼。
  private blinkNextAt = 2 + Math.random() * 3;
  private blinkUntil = -1;
  private blinkValue = 0;
  private readonly blinkCloseDuration = 0.15; // 一次眨眼的闭合-张开总时长(s)

  private readonly onBeforeModelUpdate = (): void => this.applyParametersNow();
  private resizeObserver: ResizeObserver | null = null;

  constructor(options: SoullinkAdapterOptions) {
    this.canvas = options.canvas;
    this.modelUrl = options.modelUrl;
    this.profileUrl = options.profileUrl;
    this.injectedProfile = options.profile;
    this.emotionMap = options.emotionMap ?? {};
    this.tapMotions = options.tapMotions ?? {};
  }

  async load(): Promise<void> {
    await ensureCubismCore();
    const { Live2DModel } = await import('pixi-live2d-display/cubism4');
    Live2DModel.registerTicker(PIXI.Ticker as unknown as typeof import('@pixi/ticker').Ticker);

    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    this.app = new PIXI.Application({
      view: this.canvas,
      backgroundAlpha: 0,
      antialias: true,
      autoDensity: true,
      resolution: dpr,
      powerPreference: 'high-performance',
      preserveDrawingBuffer: false,
    });

    this.model = await Live2DModel.from(this.modelUrl, {
      autoUpdate: true,
      autoHitTest: false,
      autoFocus: false,
    });
    this.model.anchor.set(0.5, 0.5);
    this.app.stage.addChild(this.model);

    // 关闭模型自带的内部眨眼，交由引擎的 BlinkController 统一驱动
    const internalModel = this.model.internalModel as unknown as {
      eyeBlink?: unknown;
      on?: (event: string, fn: () => void) => void;
      off?: (event: string, fn: () => void) => void;
    };
    internalModel.eyeBlink = undefined;

    // 加载模型 Profile 并构造引擎；blinkRate=2.2 → BlinkController 间隔
    // ~1.4-3.2s（引擎 rate 范围 0.25-2.5；scheduleNext: base=(3+rand*4)/rate，
    // rate 越大眨眼越频繁。0.35 是错误方向——会放大到 8.6-20s/次）
    const profile = this.injectedProfile ?? (await this.loadProfile());
    this.runtime = new SoullinkRuntime({
      profile,
      motionStyle: { ...motionStylePresets.lively, blinkRate: 2.2 },
    });
    // 用后端的实时音量驱动引擎口型
    this.runtime.setAudioLevelAnalyzer(new VolumeLevelAnalyzer(() => this.voiceLevel));

    // Cubism 在每帧更新模型参数前应用引擎输出
    internalModel.on?.('beforeModelUpdate', this.onBeforeModelUpdate);

    this.fitModel();
    this.startFrameLoop();

    this.resizeObserver = new ResizeObserver(() => this.fitModel());
    if (this.canvas.parentElement) {
      this.resizeObserver.observe(this.canvas.parentElement);
    }
  }

  // ------------------------------------------------------------------ //
  // Public control API（与 Live2DModelAdapter 保持一致）
  // ------------------------------------------------------------------ //

  setExpression(input: string | number | undefined | null, confidence?: number | null): void {
    if (!this.runtime) return;
    const intent = toEmotionIntent(input, this.emotionMap, confidence ?? undefined);
    if (!intent) {
      this.revertExpression();
      return;
    }
    this.triggerIntent(intent);
  }

  revertExpression(): void {
    if (!this.runtime) return;
    this.triggerIntent(neutralIntent());
  }

  playMotion(group: string, index?: number): void {
    if (!this.model) return;
    try {
      void this.model.motion(group, index ?? 0, 2).catch(() => undefined);
    } catch {
      // motion group may not exist; ignore
    }
  }

  playTapMotion(hitArea: string): void {
    const mapping = this.tapMotions[hitArea];
    if (!mapping) return;
    const firstEntry = Object.entries(mapping)[0];
    if (firstEntry) this.playMotion(firstEntry[0], firstEntry[1]);
  }

  stopMotion(): void {
    if (!this.model) return;
    void this.model.motion('Idle', 0, 3).catch(() => undefined);
  }

  playRandomMotion(mode: 'idle' | 'speaking'): boolean {
    if (!this.model) return false;
    const picked = pickRandomMotion(this.model, mode);
    if (!picked) return false;
    try {
      // priority 2 (NORMAL)：不打断 FORCE 级关键动作
      void this.model.motion(picked.group, picked.index, 2).catch(() => undefined);
      return true;
    } catch {
      return false;
    }
  }

  setLipSync(value: number, _viseme?: import('./Live2DAdapter').VisemeVector | null): void {
    this.voiceLevel = value;
    const active = value > 0.005;
    if (active !== this.voiceActive) {
      this.voiceActive = active;
      this.runtime?.setVoicePlaybackActive(active);
    }
  }

  /** P1 连续动作：外部参数帧叠加（motion-plan），在 beforeModelUpdate 时写入。 */
  applyExternalParams(params: Record<string, number>): void {
    this.externalParams = { ...params };
  }

  // ------------------------------------------------------------------ //
  // P1.5 外观 / 视觉滤镜 / 遮罩
  // ------------------------------------------------------------------ //

  /** P1.5 外观：缩放/位置/不透明度应用到模型（渲染层真实生效）。 */
  applyAppearance(appearance: {
    scale?: number; // 40-200 %
    posX?: number; // 0-100 %
    posY?: number; // 0-100 %
    opacity?: number; // 20-100 %
  }): void {
    this.appearance = { ...this.appearance, ...appearance };
    this.fitModel();
    if (this.model) {
      this.model.alpha = (this.appearance.opacity ?? 100) / 100;
    }
  }

  /** P1.5 视觉滤镜：亮度/色温/饱和度（ColorMatrixFilter，参考 SoulLink ambient-lighting）。 */
  applyVisualFx(fx: {
    enabled?: boolean;
    brightness?: number; // 0.2-2.0（1 = 不变）
    colorTemp?: number; // 2000-10000 K（6500 = 不变）
    saturation?: number; // 0-2（1 = 不变）
  }): void {
    this.visualFx = { ...this.visualFx, ...fx };
    if (!this.model) return;
    const { enabled, brightness, colorTemp, saturation } = this.visualFx;
    if (!enabled) {
      if (this.colorFilter) {
        this.model.filters = [];
        this.colorFilter = null;
      }
      return;
    }
    if (!this.colorFilter) {
      this.colorFilter = new PIXI.ColorMatrixFilter();
      this.model.filters = [this.colorFilter];
    }
    const cf = this.colorFilter;
    cf.reset();
    // 亮度
    const b = Math.max(0.2, Math.min(2.0, brightness ?? 1));
    cf.brightness(b, false);
    // 色温：6500K 不变；偏暖（<6500）→ 提高 R 降低 B；偏冷（>6500）反向
    const temp = Math.max(2000, Math.min(10000, colorTemp ?? 6500));
    const diff = (temp - 6500) / 6500; // -1..+0.54
    const rBoost = Math.max(0.85, Math.min(1.25, 1 + diff * 0.35));
    const bBoost = Math.max(0.8, Math.min(1.3, 1 - diff * 0.3));
    cf.matrix[0] *= rBoost;
    cf.matrix[12] *= bBoost;
    // 饱和度（PIXI ColorMatrixFilter.saturate）
    const sat = Math.max(0, Math.min(2, saturation ?? 1));
    if (Math.abs(sat - 1) > 0.01) cf.saturate(sat, false);
  }

  /** P1.5 遮罩：多边形裁剪（画布坐标归一化 0..1，多边形内可见）。 */
  setOcclusion(points: { x: number; y: number }[] | null): void {
    this.occlusion = points;
    if (!this.model || !this.app) return;
    if (!points || points.length < 3) {
      this.model.mask = null;
      if (this.occlusionGraphics) {
        this.occlusionGraphics.destroy();
        this.occlusionGraphics = null;
      }
      return;
    }
    if (!this.occlusionGraphics) {
      this.occlusionGraphics = new PIXI.Graphics();
      this.model.mask = this.occlusionGraphics;
    }
    const w = this.app.renderer.width;
    const h = this.app.renderer.height;
    const g = this.occlusionGraphics;
    g.clear();
    g.beginFill(0xffffff);
    g.moveTo(points[0].x * w, points[0].y * h);
    for (let i = 1; i < points.length; i += 1) {
      g.lineTo(points[i].x * w, points[i].y * h);
    }
    g.closePath();
    g.endFill();
  }

  /** 捕获当前渲染帧为 PNG data URL（用于生成模型缩略图）。 */
  capturePng(): string {
    if (!this.app) return this.canvas.toDataURL('image/png');
    const view = this.app.view as HTMLCanvasElement | undefined;
    return (view ?? this.canvas).toDataURL('image/png');
  }

  setFocus(x: number, y: number): void {
    if (!this.model) return;
    try {
      this.model.focus(x, y);
    } catch {
      // focus can throw before the model is fully initialized
    }
  }

  hitTest(x: number, y: number): string[] {
    if (!this.model) return [];
    try {
      return this.model.hitTest(x, y);
    } catch {
      return [];
    }
  }

  resize(width: number, height: number): void {
    if (this.app) this.app.renderer.resize(width, height);
    this.fitModel();
  }

  destroy(): void {
    this.destroyed = true;
    if (this.frameId) cancelAnimationFrame(this.frameId);
    if (this.resizeObserver) {
      this.resizeObserver.disconnect();
      this.resizeObserver = null;
    }
    if (this.model) {
      const internalModel = this.model.internalModel as unknown as {
        off?: (event: string, fn: () => void) => void;
      };
      internalModel.off?.('beforeModelUpdate', this.onBeforeModelUpdate);
      if (this.occlusionGraphics) {
        this.occlusionGraphics.destroy();
        this.occlusionGraphics = null;
        this.model.mask = null;
      }
      this.model.filters = [];
      this.colorFilter = null;
      this.model.destroy();
      this.model = null;
    }
    if (this.app) {
      this.app.destroy(true, { children: true });
      this.app = null;
    }
    this.runtime = null;
  }

  // ------------------------------------------------------------------ //
  // Internals
  // ------------------------------------------------------------------ //

  private async loadProfile(): Promise<ModelProfile> {
    const { profile } = await loadModelProfile(this.profileUrl);
    return profile;
  }

  private triggerIntent(intent: EmotionIntent): void {
    const t = this.currentSeconds();
    this.runtime?.triggerIntent(intent, t);
  }

  private currentSeconds(): number {
    return this.startedAt > 0 ? performance.now() / 1000 - this.startedAt : 0;
  }

  private startFrameLoop(): void {
    this.startedAt = performance.now() / 1000;
    this.previousTime = this.startedAt;
    this.frameId = requestAnimationFrame(this.frame.bind(this));
  }

  private frame(timestampMs: number): void {
    if (this.destroyed) return;

    const absoluteTime = timestampMs / 1000;
    const timeSeconds = absoluteTime - this.startedAt;
    const deltaSeconds = Math.min(0.1, absoluteTime - this.previousTime);
    this.previousTime = absoluteTime;

    // 自研眨眼调度（引擎 BlinkController 缺陷补丁）
    this.updateBlink(timeSeconds);

    // 说话头部微动（Phase B2）：随音量摆动/点头；idle 交给模型动作
    this.headMotion.update(timeSeconds, this.voiceLevel);

    const snapshot = this.runtime?.update(timeSeconds, deltaSeconds);
    if (snapshot) this.applySnapshot(snapshot);

    this.frameId = requestAnimationFrame(this.frame.bind(this));
  }

  /**
   * 眨眼调度：2.5-6s 随机间隔触发一次，150ms 正弦波形（0→1→0）。
   * 值在 applyParametersNow 里作用于 ParamEyeLOpen/ROpen。
   */
  private updateBlink(timeSeconds: number): void {
    if (timeSeconds >= this.blinkNextAt) {
      this.blinkUntil = timeSeconds + this.blinkCloseDuration;
      this.blinkNextAt = timeSeconds + 2.5 + Math.random() * 3.5;
    }
    if (this.blinkUntil > timeSeconds) {
      const phase = (this.blinkUntil - timeSeconds) / this.blinkCloseDuration; // 1 → 0
      this.blinkValue = Math.sin(phase * Math.PI); // 0 → 1 → 0
    } else {
      this.blinkValue = 0;
    }
  }

  private applySnapshot(snapshot: RuntimeSnapshot): void {
    this.latestParams = snapshot.live2dParams;
    this.applyNativeAnimation(snapshot.nativeAnimation);
  }

  /**
   * 应用引擎解析出的原生动画指令（模型的 expression/motion），并记录需要
   * 抑制（交给动画本身控制）的参数 ID。逻辑与 SDK Live2DRenderer 一致。
   */
  private applyNativeAnimation(directive: NativeAnimationDirective | null): void {
    if (!this.model) return;

    if (directive === null) {
      this.suppressedParamIds = new Set();
      if (this.lastNativeAnimToken !== 0) {
        this.applyExpression();
        this.lastNativeAnimToken = 0;
      }
      return;
    }

    this.suppressedParamIds = new Set(directive.suppressParamIds);
    if (directive.token === this.lastNativeAnimToken) return;

    if (directive.expression !== null) this.applyExpression(directive.expression);
    if (directive.motion !== null) {
      this.applyMotion(
        directive.motion.group,
        directive.motion.index ?? 0,
        priorityFor(directive.motion.priority ?? 'normal'),
      );
    }
    this.lastNativeAnimToken = directive.token;
  }

  private applyExpression(name?: string): void {
    if (!this.model) return;
    try {
      void Promise.resolve(
        name === undefined ? this.model.expression() : this.model.expression(name),
      ).catch(() => undefined);
    } catch {
      // expression may not exist; ignore
    }
  }

  private applyMotion(group: string, index: number, priority: number): void {
    if (!this.model) return;
    try {
      void Promise.resolve(this.model.motion(group, index, priority)).catch(() => undefined);
    } catch {
      // motion group may not exist; ignore
    }
  }

  /** 在 Cubism beforeModelUpdate 时写入引擎本帧输出的参数。 */
  private applyParametersNow(): void {
    const coreModel = this.model?.internalModel?.coreModel as CubismCoreLike | undefined;
    if (!coreModel?.setParameterValueById) return;

    for (const [id, value] of Object.entries(this.latestParams)) {
      if (this.suppressedParamIds.has(id)) continue;
      if (coreModel.getParameterIndex && coreModel.getParameterIndex(id) < 0) continue;
      // 引擎的头部/身体姿态参数（headX/Y/Z → ParamAngle*、bodyX/Y/Z → ParamBodyAngle*）
      // 与模型动作（Idle/Talk 动画）同写一套参数，每帧覆盖会把动作动画压成
      // 引擎的静态微动（Node 探针实证引擎 idle 也输出 head/body 非 0）。
      // 姿态完全交给动作层 + 说话微动（HeadMotion），引擎只负责表情 FACS。
      if (isHeadParam(id) || isBodyParam(id)) continue;
      // 自研眨眼补丁：眨眼期间压低眼睛张开度（引擎 BlinkController 缺陷兜底）
      let final = value;
      if (this.blinkValue > 0 && (id === 'ParamEyeLOpen' || id === 'ParamEyeROpen')) {
        final = value * (1 - this.blinkValue);
      }
      coreModel.setParameterValueById(id, final, 1);
    }

    // P1 连续动作：外部参数帧覆盖（motion-plan 产物；跳过口型开合——交给音量驱动）
    for (const [id, value] of Object.entries(this.externalParams)) {
      if (this.suppressedParamIds.has(id)) continue;
      if (isHeadParam(id) || isBodyParam(id)) continue;
      const lower = id.toLowerCase();
      if (lower.includes('mouthopen') || lower === 'parammouthopeny') continue;
      if (coreModel.getParameterIndex && coreModel.getParameterIndex(id) < 0) continue;
      coreModel.setParameterValueById(id, value, 1);
    }

    // 说话头部微动叠加（Phase B2）：仅说话时生效，幅度小不冲突动作。
    // 引擎头部参数已在上方无条件跳过，此处直接叠加（无需再检查引擎值）。
    if (this.headMotion.isActive()) {
      for (const [id, angle] of [
        ['ParamAngleX', this.headMotion.angleX],
        ['ParamAngleY', this.headMotion.angleY],
        ['ParamAngleZ', this.headMotion.angleZ],
      ] as const) {
        if (coreModel.getParameterIndex && coreModel.getParameterIndex(id) < 0) continue;
        coreModel.setParameterValueById(id, angle, 1);
      }
    }
  }

  private fitModel(): void {
    if (!this.model || !this.app) return;
    const parent = this.canvas.parentElement;
    if (!parent) return;
    const w = parent.clientWidth;
    const h = parent.clientHeight;
    if (w <= 0 || h <= 0) return;

    this.app.renderer.resize(w, h);

    const origW = this.model.internalModel.originalWidth || this.model.width;
    const origH = this.model.internalModel.originalHeight || this.model.height;
    if (origW <= 0 || origH <= 0) return;

    // P1.5 外观：缩放百分比 + 位置百分比叠加
    const scalePct = (this.appearance.scale ?? 100) / 100;
    const scale = Math.min(w / origW, h / origH) * 0.95 * scalePct;
    this.model.scale.set(scale);
    const dx = (((this.appearance.posX ?? 50) - 50) / 100) * w * 0.5;
    const dy = (((this.appearance.posY ?? 60) - 50) / 100) * h * 0.5;
    this.model.x = w / 2 + dx;
    this.model.y = h / 2 + h * 0.04 + dy;
    this.model.alpha = (this.appearance.opacity ?? 100) / 100;

    // 遮罩随画布尺寸重绘（点坐标为归一化 0..1）
    if (this.occlusion) this.setOcclusion(this.occlusion);
  }
}

/** 供引擎读取当前实时音量的极简 AudioLevelAnalyzer。 */
class VolumeLevelAnalyzer implements AudioLevelAnalyzer {
  private readonly read: () => number;

  constructor(read: () => number) {
    this.read = read;
  }

  getLevel(): number {
    return this.read();
  }
}

function priorityFor(priority: 'idle' | 'normal' | 'force'): number {
  // pixi-live2d-display 的 MotionPriority: NONE=0, IDLE=1, NORMAL=2, FORCE=3
  if (priority === 'idle') return 1;
  if (priority === 'force') return 3;
  return 2;
}

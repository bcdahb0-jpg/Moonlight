import * as PIXI from 'pixi.js';
import { ensureCubismCore } from './cubismCore';
import type { Live2DAdapter, VisemeVector } from './Live2DAdapter';
import { HeadMotion, pickRandomMotion } from './motionUtils';

/**
 * Map of backend emotion names (e.g. "joy") to expression indices used by the
 * Live2D model. Provided by the backend's `model_dict.json` via the
 * `set-model-and-conf` WS message (`model_info.emotionMap`).
 */
export type EmotionMap = Record<string, number>;

/**
 * Tap-motion mapping from `model_info.tapMotions`:
 *   hitArea -> { motionGroup: motionIndex }
 */
export type TapMotions = Record<string, Record<string, number>>;

export interface Live2DAdapterOptions {
  canvas: HTMLCanvasElement;
  modelUrl: string;
  emotionMap?: EmotionMap;
  tapMotions?: TapMotions;
  /** 仅生成缩略图时开启；桌宠常态不需要保留 GPU 帧缓冲。 */
  preserveDrawingBuffer?: boolean;
}

interface CubismExpressionDefinition {
  Name: string;
  File: string;
}

type Live2DModelInstance = import('pixi-live2d-display/cubism4').Live2DModel;

const LIP_PARAM_CANDIDATES = ['ParamMouthOpenY', 'ParamA', 'ParamI', 'ParamU', 'ParamE', 'ParamO', 'ParamMouthOpen'];

/**
 * 副口型参数按音量比例的固定权重（仅对模型实际存在的参数生效）。
 * Cubism 标准元音口型参数：/a/ 开口最大、/i/ 微笑、/u/ 嘟嘴、/e/ 咧嘴、/o/ 圆口。
 * 单参数音量只能表达「嘴张多大」；叠加这些权重后能表达基础嘴型倾向，
 * 口型观感显著提升且不依赖后端数据。
 */
const LIP_AUX_WEIGHTS: ReadonlyArray<readonly [string, number]> = [
  ['ParamA', 0.35],
  ['ParamI', 0.25],
  ['ParamU', 0.15],
  ['ParamE', 0.2],
  ['ParamO', 0.3],
];

/**
 * viseme 向量索引 → 副口型参数映射（与 LIP_AUX_WEIGHTS 顺序一致）：
 * [a, i, u, e, o] → [ParamA, ParamI, ParamU, ParamE, ParamO]
 */
const VISEME_AUX_PARAMS: ReadonlyArray<string> = [
  'ParamA',
  'ParamI',
  'ParamU',
  'ParamE',
  'ParamO',
];

/** viseme 驱动副口型参数的缩放系数（概率 0..1 → 参数 0..0.9）。 */
const VISEME_PARAM_SCALE = 0.9;

/**
 * Wraps a pixi-live2d-display model behind a small imperative API used by the
 * React layer: load / setExpression / revertExpression / playMotion / stopMotion
 * / setLipSync / resize / destroy. The Cubism runtime is ensured before the
 * pixi-live2d-display module is dynamically imported.
 */
export class Live2DModelAdapter implements Live2DAdapter {
  private app: PIXI.Application | null = null;
  private model: Live2DModelInstance | null = null;
  private readonly canvas: HTMLCanvasElement;
  private readonly modelUrl: string;
  private readonly emotionMap: EmotionMap;
  private readonly tapMotions: TapMotions;
  private readonly preserveDrawingBuffer: boolean;

  private expressionNames: string[] = [];
  /** 主口型参数（ParamMouthOpenY 或其别名）。 */
  private lipParamId: string | null = null;
  /** 模型实际存在的副口型参数（ParamA/I/U/E/O 子集）。 */
  private lipAuxParams: string[] = [];
  private lipSyncValue = 0;
  /** 音素级口型向量（Phase 2，缺省 null = 回退固定比例口型）。 */
  private lipViseme: VisemeVector | null = null;
  /** 说话头部微动（Phase B2）。 */
  private readonly headMotion = new HeadMotion();
  private resizeObserver: ResizeObserver | null = null;
  private readonly frameListener: () => void;

  constructor(options: Live2DAdapterOptions) {
    this.canvas = options.canvas;
    this.modelUrl = options.modelUrl;
    this.emotionMap = options.emotionMap ?? {};
    this.tapMotions = options.tapMotions ?? {};
    this.preserveDrawingBuffer = options.preserveDrawingBuffer ?? false;
    this.frameListener = () => this.applyFrameParams();
  }

  async load(): Promise<void> {
    await ensureCubismCore();
    const { Live2DModel } = await import('pixi-live2d-display/cubism4');
    // pixi-live2d-display types reference `@pixi/ticker` directly; the pixi.js
    // bundle re-exports the same class, so a cast is safe across minor versions.
    Live2DModel.registerTicker(PIXI.Ticker as unknown as typeof import('@pixi/ticker').Ticker);

    const dpr = Math.min(Math.max(1, window.devicePixelRatio || 1), 2);
    this.app = new PIXI.Application({
      view: this.canvas,
      backgroundAlpha: 0,
      antialias: true,
      autoDensity: true,
      resolution: dpr,
      powerPreference: 'high-performance',
      autoStart: true,
      preserveDrawingBuffer: this.preserveDrawingBuffer,
    });

    this.model = await Live2DModel.from(this.modelUrl, {
      autoUpdate: true,
      autoHitTest: false,
      autoFocus: false,
    });
    this.model.anchor.set(0.5, 0.5);
    this.app.stage.addChild(this.model);

    this.expressionNames = this.readExpressionNames(this.model);
    this.resolveLipParams(this.model);
    this.fitModel();

    this.app.ticker.add(this.frameListener);

    this.resizeObserver = new ResizeObserver(() => this.fitModel());
    if (this.canvas.parentElement) {
      this.resizeObserver.observe(this.canvas.parentElement);
    }
  }

  // ------------------------------------------------------------------ //
  // Public control API
  // ------------------------------------------------------------------ //

  setExpression(input: string | number | undefined | null, _confidence?: number | null): void {
    if (!this.model) return;
    const name = this.resolveExpressionName(input);
    if (!name) {
      this.revertExpression();
      return;
    }
    void this.model.expression(name).catch(() => undefined);
  }

  revertExpression(): void {
    if (!this.model) return;
    void this.model.expression().catch(() => undefined);
  }

  playMotion(group: string, index?: number): void {
    if (!this.model) return;
    try {
      void this.model.motion(group, index ?? 0, 3).catch(() => undefined);
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
      // priority 2 (NORMAL)：不打断 FORCE 级的关键动作
      void this.model.motion(picked.group, picked.index, 2).catch(() => undefined);
      return true;
    } catch {
      return false;
    }
  }

  setLipSync(value: number, viseme?: VisemeVector | null): void {
    this.lipSyncValue = value;
    this.lipViseme = viseme ?? null;
  }

  /** 捕获当前渲染帧为 PNG data URL（用于生成模型缩略图）。 */
  capturePng(): string {
    return this.canvas.toDataURL('image/png');
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
    if (this.resizeObserver) {
      this.resizeObserver.disconnect();
      this.resizeObserver = null;
    }
    if (this.app && this.frameListener) {
      this.app.ticker.remove(this.frameListener);
    }
    if (this.model) {
      this.model.destroy();
      this.model = null;
    }
    if (this.app) {
      this.app.destroy(true, { children: true });
      this.app = null;
    }
  }

  // ------------------------------------------------------------------ //
  // Internals
  // ------------------------------------------------------------------ //

  private readExpressionNames(model: Live2DModelInstance): string[] {
    const settings = model.internalModel.settings as unknown as {
      expressions?: CubismExpressionDefinition[];
    };
    if (!settings || !Array.isArray(settings.expressions)) return [];
    return settings.expressions.map((e) => e.Name).filter(Boolean);
  }

  private resolveExpressionName(input: string | number | undefined | null): string | null {
    if (input === undefined || input === null || input === '') return null;

    if (typeof input === 'number') {
      return this.expressionNames[Math.floor(input)] ?? null;
    }

    const lower = input.toLowerCase();
    const direct = this.expressionNames.find((n) => n.toLowerCase() === lower);
    if (direct) return direct;

    // Backend emotion string -> emotionMap -> expression index.
    if (this.emotionMap[input] !== undefined) {
      const idx = this.emotionMap[input];
      const name = this.expressionNames[idx];
      if (name) return name;
    }

    const fuzzy = this.expressionNames.find((n) => n.toLowerCase().includes(lower));
    return fuzzy ?? null;
  }

  private resolveLipParams(model: Live2DModelInstance): void {
    try {
      const core = model.internalModel.coreModel as {
        getParameterIndex(id: string): number;
      };
      this.lipParamId = null;
      for (const id of LIP_PARAM_CANDIDATES) {
        if (core.getParameterIndex(id) >= 0) {
          this.lipParamId = id;
          break;
        }
      }
      // 收集模型实际存在的副口型参数（多参数口型，Phase 1）
      this.lipAuxParams = [];
      for (const [id] of LIP_AUX_WEIGHTS) {
        if (core.getParameterIndex(id) >= 0) this.lipAuxParams.push(id);
      }
    } catch {
      this.lipParamId = null;
      this.lipAuxParams = [];
    }
  }

  private applyFrameParams(): void {
    if (!this.model || !this.app) return;

    const core = this.model.internalModel.coreModel as {
      getParameterIndex(id: string): number;
      setParameterValueById(id: string, value: number, weight?: number): void;
    };

    if (this.lipSyncValue > 0.005 && this.lipParamId) {
      try {
        const open = this.lipSyncValue * 0.9;
        core.setParameterValueById(this.lipParamId, open);

        // 有 viseme（Phase 2）：按元音概率驱动对应口型参数（嘴形由音素决定）
        if (this.lipViseme && this.lipViseme.length >= 5) {
          for (let v = 0; v < VISEME_AUX_PARAMS.length; v++) {
            const id = VISEME_AUX_PARAMS[v];
            if (this.lipAuxParams.indexOf(id) >= 0) {
              core.setParameterValueById(id, open * this.lipViseme[v] * VISEME_PARAM_SCALE);
            }
          }
        } else {
          // 回退（Phase 1）：副口型参数按固定比例推导
          for (const [id, weight] of LIP_AUX_WEIGHTS) {
            if (this.lipAuxParams.indexOf(id) >= 0) {
              core.setParameterValueById(id, open * weight);
            }
          }
        }
      } catch {
        // ignore per-frame param errors
      }
    } else {
      // 静音：主参数归零；副参数留给模型的 idle 动画自行控制（不强行覆盖）
      try {
        if (this.lipParamId) core.setParameterValueById(this.lipParamId, 0);
      } catch {
        // ignore per-frame param errors
      }
    }

    // 说话头部微动（Phase B2）：幅度小、三轴正弦错相；idle 时归零交给动作
    this.headMotion.update(performance.now() / 1000, this.lipSyncValue);
    if (this.headMotion.isActive()) {
      try {
        if (core.getParameterIndex('ParamAngleX') >= 0) {
          core.setParameterValueById('ParamAngleX', this.headMotion.angleX);
        }
        if (core.getParameterIndex('ParamAngleY') >= 0) {
          core.setParameterValueById('ParamAngleY', this.headMotion.angleY);
        }
        if (core.getParameterIndex('ParamAngleZ') >= 0) {
          core.setParameterValueById('ParamAngleZ', this.headMotion.angleZ);
        }
      } catch {
        // ignore per-frame param errors
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

    // Use the model's intrinsic canvas size as the scaling baseline.
    // `model.width`/`model.height` already include the current `model.scale`
    // (PIXI's DisplayObject getters multiply by scale), so using them here
    // would compound the factor on every resize and the model never tracks
    // the window. `internalModel.width/height` is fixed at layout setup and
    // is independent of the outer scale, so it stays stable across resizes.
    const origW = this.model.internalModel.width;
    const origH = this.model.internalModel.height;
    if (origW <= 0 || origH <= 0) return;

    const scale = Math.min(w / origW, h / origH) * 0.95;
    this.model.scale.set(scale);
    this.model.x = w / 2;
    this.model.y = h / 2 + h * 0.04;
  }
}

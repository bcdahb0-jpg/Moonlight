import * as PIXI from 'pixi.js';
import { ensureCubismCore } from './cubismCore';
import type { Live2DAdapter } from './Live2DAdapter';

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

interface Live2DAdapterOptions {
  canvas: HTMLCanvasElement;
  modelUrl: string;
  emotionMap?: EmotionMap;
  tapMotions?: TapMotions;
}

interface CubismExpressionDefinition {
  Name: string;
  File: string;
}

type Live2DModelInstance = import('pixi-live2d-display/cubism4').Live2DModel;

const LIP_PARAM_CANDIDATES = ['ParamMouthOpenY', 'ParamA', 'ParamMouthOpen'];

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

  private expressionNames: string[] = [];
  private lipParamId: string | null = null;
  private lipSyncValue = 0;
  private resizeObserver: ResizeObserver | null = null;
  private readonly frameListener: () => void;

  constructor(options: Live2DAdapterOptions) {
    this.canvas = options.canvas;
    this.modelUrl = options.modelUrl;
    this.emotionMap = options.emotionMap ?? {};
    this.tapMotions = options.tapMotions ?? {};
    this.frameListener = () => this.applyFrameParams();
  }

  async load(): Promise<void> {
    await ensureCubismCore();
    const { Live2DModel } = await import('pixi-live2d-display/cubism4');
    // pixi-live2d-display types reference `@pixi/ticker` directly; the pixi.js
    // bundle re-exports the same class, so a cast is safe across minor versions.
    Live2DModel.registerTicker(PIXI.Ticker as unknown as typeof import('@pixi/ticker').Ticker);

    const dpr = Math.max(1, window.devicePixelRatio || 1);
    this.app = new PIXI.Application({
      view: this.canvas,
      backgroundAlpha: 0,
      antialias: true,
      autoDensity: true,
      resolution: dpr,
      powerPreference: 'high-performance',
      autoStart: true,
      // Needed so canvas.toDataURL() captures the rendered model (thumbnail gen).
      preserveDrawingBuffer: true,
    });

    this.model = await Live2DModel.from(this.modelUrl, {
      autoUpdate: true,
      autoHitTest: false,
      autoFocus: false,
    });
    this.model.anchor.set(0.5, 0.5);
    this.app.stage.addChild(this.model);

    this.expressionNames = this.readExpressionNames(this.model);
    this.resolveLipParam(this.model);
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

  setExpression(input: string | number | undefined | null): void {
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

  setLipSync(value: number): void {
    this.lipSyncValue = value;
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

  private resolveLipParam(model: Live2DModelInstance): void {
    try {
      const core = model.internalModel.coreModel as {
        getParameterIndex(id: string): number;
      };
      for (const id of LIP_PARAM_CANDIDATES) {
        if (core.getParameterIndex(id) >= 0) {
          this.lipParamId = id;
          break;
        }
      }
    } catch {
      this.lipParamId = null;
    }
  }

  private applyFrameParams(): void {
    if (!this.model || !this.app) return;
    if (this.lipSyncValue > 0.005 && this.lipParamId) {
      try {
        const core = this.model.internalModel.coreModel as {
          setParameterValueById(id: string, value: number, weight?: number): void;
        };
        core.setParameterValueById(this.lipParamId, this.lipSyncValue * 0.9);
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

import { useEffect, useRef, type ReactElement } from 'react';
import { Live2DModelAdapter, type EmotionMap, type TapMotions } from './Live2DModelAdapter';
import { SoullinkAdapter } from './SoullinkAdapter';
import type { Live2DAdapter } from './Live2DAdapter';
import { generateDefaultProfile } from './defaultProfile';
import type { ModelProfile } from '@soullink-emotion/engine';

/**
 * 是否禁用 Soullink Emotion SDK 引擎（Phase 0 起默认为启用）。
 * 通过 Vite 环境变量 `VITE_USE_SOULLINK=false` 可显式关闭；
 * 未配置时走运行时探测：模型 profile 存在 → 直接使用；
 * profile 缺失 → 用标准 Cubism 参数启发式生成默认 profile（defaultProfile.ts）；
 * 生成也失败 → 回退到「切换式 expression」适配器。
 */
const DISABLE_SOULLINK = import.meta.env.VITE_USE_SOULLINK === 'false';

/** 从模型 URL 推导同目录下的 SDK profile URL（.model3.json → soullink.profile.json）。 */
function deriveProfileUrl(modelUrl: string): string {
  return modelUrl.replace(/\/[^/]+\.model3\.json$/, '/soullink.profile.json');
}

/** 探测 profile URL 是否可用（HTTP 200 且能解析为 JSON 对象）。 */
async function probeProfile(url: string): Promise<ModelProfile | null> {
  try {
    const res = await fetch(url, { cache: 'no-cache' });
    if (!res.ok) return null;
    const json = (await res.json()) as ModelProfile;
    if (!json || typeof json !== 'object' || !json.parameterMap) return null;
    return json;
  } catch {
    return null;
  }
}

export interface Live2DCanvasProps {
  modelUrl: string;
  emotionMap?: EmotionMap;
  tapMotions?: TapMotions;
  /** 显式指定 profile URL；缺省时从 modelUrl 推导。 */
  profileUrl?: string;
  /** 显式注入 profile 对象（优先于 profileUrl 探测）。 */
  profile?: ModelProfile;
  onAdapterReady?: (adapter: Live2DAdapter) => void;
  /** 引擎选择结果回调（诊断用：确认实际运行的是哪个引擎）。 */
  onEngineChange?: (engine: 'soullink' | 'legacy') => void;
  onError?: (error: Error) => void;
  className?: string;
}

/**
 * Renders a Live2D model into a transparent canvas. The component owns the
 * adapter lifecycle (create on mount / model change, destroy on unmount) and
 * wires pointer interaction: drag-to-move, click-to-play-tap-motion, and
 * mouse-look tracking via `model.focus`.
 *
 * Soullink 选择策略（Phase 0 运行时探测，默认启用）：
 *   1. 显式 `profile` prop → 直接使用
 *   2. `profileUrl`（或推导 URL）可拉取 → 使用
 *   3. 生成默认 profile（标准 Cubism 参数启发式）→ 使用
 *   4. 全部失败 → 回退 Live2DModelAdapter（切换式 expression）
 */
export function Live2DCanvas({
  modelUrl,
  emotionMap,
  tapMotions,
  profileUrl,
  profile: injectedProfile,
  onAdapterReady,
  onEngineChange,
  onError,
  className,
}: Live2DCanvasProps): ReactElement {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const adapterRef = useRef<Live2DAdapter | null>(null);
  const onReadyRef = useRef(onAdapterReady);
  const onErrorRef = useRef(onError);
  const onEngineChangeRef = useRef(onEngineChange);
  onReadyRef.current = onAdapterReady;
  onErrorRef.current = onError;
  onEngineChangeRef.current = onEngineChange;

  useEffect(() => {
    onReadyRef.current = onAdapterReady;
  }, [onAdapterReady]);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || !modelUrl) return;

    const resolvedProfileUrl = profileUrl || deriveProfileUrl(modelUrl);
    let cancelled = false;

    const runAdapter = async (): Promise<void> => {
      if (cancelled) return;

      // ---- Soullink 优先：显式注入 → URL 探测 → 启发式生成 ----
      if (!DISABLE_SOULLINK) {
        let profile: ModelProfile | null | undefined = injectedProfile;
        let profileSource: 'injected' | 'url' | 'generated' | null = null;
        if (!profile) {
          profile = await probeProfile(resolvedProfileUrl);
          if (profile) profileSource = 'url';
        } else {
          profileSource = 'injected';
        }
        if (!profile) {
          const generated = await generateDefaultProfile(modelUrl);
          if (generated) {
            profile = generated.profile;
            profileSource = 'generated';
            // 非标模型连眨眼参数都没有：引擎价值有限，直接回退切换式
            if (!generated.canBlink) profile = null;
          }
        }

        if (profile) {
          const soullink = new SoullinkAdapter({
            canvas,
            modelUrl,
            profileUrl: resolvedProfileUrl,
            profile,
            emotionMap,
            tapMotions,
          });
          adapterRef.current = soullink;
          try {
            await soullink.load();
            console.info(
              `[live2d] engine=Soullink source=${profileSource ?? 'unknown'} model=${modelUrl}`,
            );
            if (!cancelled) {
              onEngineChangeRef.current?.('soullink');
              onReadyRef.current?.(soullink);
            }
            return;
          } catch (err) {
            // SDK 初始化失败（profile 结构异常 / 引擎异常）：销毁并降级
            console.warn('[live2d] Soullink 初始化失败，回退切换式:', err);
            soullink.destroy();
            adapterRef.current = null;
            if (cancelled) return;
            void profileSource; // 保留来源信息便于排查（日志在 catch 外层）
          }
        }
      }

      // ---- 回退：切换式 expression 适配器 ----
      if (cancelled) return;
      console.info('[live2d] engine=Legacy（切换式 expression）');
      onEngineChangeRef.current?.('legacy');
      const legacy = new Live2DModelAdapter({ canvas, modelUrl, emotionMap, tapMotions });
      adapterRef.current = legacy;
      await legacy.load();
      if (!cancelled) onReadyRef.current?.(legacy);
    };

    void runAdapter().catch((err: unknown) => {
      if (!cancelled) onErrorRef.current?.(err instanceof Error ? err : new Error(String(err)));
    });

    return () => {
      cancelled = true;
      adapterRef.current?.destroy();
      adapterRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [modelUrl, profileUrl]);

  // 眼神跟随：鼠标在画布区域移动时驱动 model.focus（0..1 视口坐标）
  const handlePointerMove = (e: React.PointerEvent<HTMLDivElement>): void => {
    const adapter = adapterRef.current;
    if (!adapter) return;
    const rect = e.currentTarget.getBoundingClientRect();
    if (rect.width <= 0 || rect.height <= 0) return;
    const nx = (e.clientX - rect.left) / rect.width;
    const ny = (e.clientY - rect.top) / rect.height;
    adapter.setFocus(nx, ny);
  };

  return (
    <div
      className={`live2d-root ${className ?? ''}`}
      onPointerMove={handlePointerMove}
    >
      <canvas ref={canvasRef} className="live2d-canvas" />
    </div>
  );
}

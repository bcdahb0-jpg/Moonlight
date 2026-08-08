import { useEffect, useRef, type ReactElement } from 'react';
import { Live2DModelAdapter, type EmotionMap, type TapMotions } from './Live2DModelAdapter';
import { SoullinkAdapter } from './SoullinkAdapter';
import type { Live2DAdapter } from './Live2DAdapter';

/**
 * 是否启用 Soullink Emotion SDK 引擎。通过 Vite 环境变量控制，
 * 关闭时回退到旧的「切换式 expression」适配器。
 */
const USE_SOULLINK = import.meta.env.VITE_USE_SOULLINK === 'true';

/** 从模型 URL 推导同目录下的 SDK profile URL（.model3.json → soullink.profile.json）。 */
function deriveProfileUrl(modelUrl: string): string {
  return modelUrl.replace(/\/[^/]+\.model3\.json$/, '/soullink.profile.json');
}

export interface Live2DCanvasProps {
  modelUrl: string;
  emotionMap?: EmotionMap;
  tapMotions?: TapMotions;
  /** 显式指定 profile URL；缺省时从 modelUrl 推导。 */
  profileUrl?: string;
  onAdapterReady?: (adapter: Live2DAdapter) => void;
  onError?: (error: Error) => void;
  className?: string;
}

/**
 * Renders a Live2D model into a transparent canvas. The component owns the
 * adapter lifecycle (create on mount / model change, destroy on unmount) and
 * wires pointer interaction: drag-to-move, click-to-play-tap-motion, and
 * mouse-look tracking via `model.focus`.
 *
 * 当 `VITE_USE_SOULLINK=true` 且模型存在 profile 时，使用 SoullinkAdapter
 * （SDK 逐帧引擎）；否则使用 Live2DModelAdapter（切换式 expression）。
 * Soullink 初始化失败（例如 profile 缺失）时自动降级到旧适配器。
 */
export function Live2DCanvas({
  modelUrl,
  emotionMap,
  tapMotions,
  profileUrl,
  onAdapterReady,
  onError,
  className,
}: Live2DCanvasProps): ReactElement {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const adapterRef = useRef<Live2DAdapter | null>(null);
  const onReadyRef = useRef(onAdapterReady);
  const onErrorRef = useRef(onError);
  onReadyRef.current = onAdapterReady;
  onErrorRef.current = onError;

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

      const useSoullink = USE_SOULLINK && Boolean(resolvedProfileUrl);
      if (!useSoullink) {
        const adapter = new Live2DModelAdapter({ canvas, modelUrl, emotionMap, tapMotions });
        adapterRef.current = adapter;
        await adapter.load();
        if (!cancelled) onReadyRef.current?.(adapter);
        return;
      }

      const soullink = new SoullinkAdapter({
        canvas,
        modelUrl,
        profileUrl: resolvedProfileUrl,
        emotionMap,
        tapMotions,
      });
      adapterRef.current = soullink;
      try {
        await soullink.load();
        if (!cancelled) onReadyRef.current?.(soullink);
      } catch (err) {
        // SDK 初始化失败（profile 缺失 / 引擎异常）：销毁并降级到旧适配器
        soullink.destroy();
        adapterRef.current = null;
        if (cancelled) return;
        const legacy = new Live2DModelAdapter({ canvas, modelUrl, emotionMap, tapMotions });
        adapterRef.current = legacy;
        await legacy.load();
        if (!cancelled) onReadyRef.current?.(legacy);
      }
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

  return (
    <div className={`live2d-root ${className ?? ''}`}>
      <canvas ref={canvasRef} className="live2d-canvas" />
    </div>
  );
}

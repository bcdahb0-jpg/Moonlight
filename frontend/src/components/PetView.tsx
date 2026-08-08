/**
 * PetView — 桌宠模式画布（Phase 2 拆分自 App.tsx）。
 * 保留「点击 vs 拖拽」判定：按下到抬起移动 < 6px 视为点击互动（sendInteract）。
 */
import { useRef, type ReactElement } from 'react';
import { Live2DCanvas } from '@/live2d/Live2DCanvas';
import type { Live2DAdapter } from '@/live2d/Live2DAdapter';

export interface PetViewProps {
  modelUrl: string;
  emotionMap?: Record<string, number>;
  tapMotions?: Record<string, Record<string, number>>;
  adapterRef: React.MutableRefObject<Live2DAdapter | null>;
  onError: (error: Error) => void;
  onInteract?: (zone: string) => void;
}

export function PetView({
  modelUrl,
  emotionMap,
  tapMotions,
  adapterRef,
  onError,
  onInteract,
}: PetViewProps): ReactElement {
  const downPos = useRef<{ x: number; y: number } | null>(null);
  return (
    <div
      className="pet-layout"
      onPointerDown={(e) => {
        downPos.current = { x: e.clientX, y: e.clientY };
      }}
      onClick={(e) => {
        const d = downPos.current;
        // 只有「按下到抬起移动 < 6px」才视为点击（区分拖拽）
        if (d && Math.hypot(e.clientX - d.x, e.clientY - d.y) < 6) {
          onInteract?.('click');
        }
        downPos.current = null;
      }}
    >
      <Live2DCanvas
        modelUrl={modelUrl}
        emotionMap={emotionMap}
        tapMotions={tapMotions}
        onAdapterReady={(a) => {
          adapterRef.current = a;
        }}
        onError={onError}
      />
    </div>
  );
}

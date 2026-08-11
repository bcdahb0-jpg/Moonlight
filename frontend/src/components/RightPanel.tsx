/**
 * RightPanel — 右栏容器（UX 改造 M1 · 三区模型，精简版）。
 *
 * 角色舞台常驻（Live2D 立绘 + 好感/情绪/连接条）。
 * 任务详情不再进入右栏（任务执行记录卡在聊天区就地展开即可，
 * TaskStreamPanel 点击头部展开/折叠），右栏只服务角色。
 */
import { type ReactElement } from 'react';
import { Live2DCanvas } from '@/live2d/Live2DCanvas';
import type { Live2DAdapter } from '@/live2d/Live2DAdapter';
import { AffectionBadge } from '@/emotion/AffectionBadge';
import { EmotionBadge } from '@/emotion/EmotionBadge';
import type { AffectionSummary } from '@/types/ws';
import type { ConnStatus, Emotion } from '@/state/types';

export interface RightPanelProps {
  /* —— 角色舞台（常驻）props —— */
  modelUrl: string;
  emotionMap?: Record<string, number>;
  tapMotions?: Record<string, Record<string, number>>;
  adapterRef: React.MutableRefObject<Live2DAdapter | null>;
  affection: AffectionSummary | null;
  emotion: Emotion;
  /** 情绪强度 0..1（诊断显示）。 */
  emotionIntensity?: number | null;
  /** 情绪来源 'rule' | 'llm'（诊断显示）。 */
  emotionSource?: string | null;
  /** 引擎类型（诊断：soullink / legacy 徽标）。 */
  engineType?: 'soullink' | 'legacy' | null;
  /** 引擎选择结果回调。 */
  onEngineChange?: (engine: 'soullink' | 'legacy') => void;
  connStatus: ConnStatus;
  onError: (error: Error) => void;
}

export function RightPanel({
  modelUrl,
  emotionMap,
  tapMotions,
  adapterRef,
  affection,
  emotion,
  emotionIntensity,
  emotionSource,
  engineType,
  connStatus,
  onError,
  onEngineChange,
}: RightPanelProps): ReactElement {
  return (
    <div className="right-panel">
      {/* 角色舞台：常驻 */}
      <div className="character-stage">
        <Live2DCanvas
          modelUrl={modelUrl}
          emotionMap={emotionMap}
          tapMotions={tapMotions}
          onAdapterReady={(a) => {
            adapterRef.current = a;
          }}
          onEngineChange={onEngineChange}
          onError={onError}
        />
        <div className="char-status-bar">
          <AffectionBadge affection={affection} />
          <EmotionBadge
            emotion={emotion}
            intensity={emotionIntensity}
            source={emotionSource}
          />
          {engineType && (
            <span className={`engine-badge ${engineType}`}>
              {engineType === 'soullink' ? 'SDK引擎' : '切换式'}
            </span>
          )}
          <span className="conn-dot" data-status={connStatus} />
        </div>
      </div>
    </div>
  );
}

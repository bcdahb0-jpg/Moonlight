/**
 * PetOverlay — 桌宠悬浮控制条。
 * 信息区（好感 / 心情 / 连接状态）常驻显示；操作按钮（切换模式、设置、
 * 鼠标穿透、打断）以图标形式存在，鼠标移入悬浮条时才展开显示，不再收纳
 * 进「更多」下拉菜单。
 */
import type { ReactElement } from 'react';
import { AffectionBadge } from '@/emotion/AffectionBadge';
import { EmotionBadge } from '@/emotion/EmotionBadge';
import type { AffectionSummary, ErrorCode } from '@/types/ws';
import type { ConnStatus, Emotion } from '@/state/types';
import { Icon } from '@/ui/icons';

export interface PetOverlayProps {
  affection: AffectionSummary | null;
  emotion: Emotion;
  connStatus: ConnStatus;
  errorCode: ErrorCode | null;
  onToggleMode: () => void;
  onOpenSettings: () => void;
  onInterrupt: () => void;
}

export function PetOverlay({
  affection,
  emotion,
  connStatus,
  onToggleMode,
  onOpenSettings,
  onInterrupt,
}: PetOverlayProps): ReactElement {
  return (
    <div className="pet-overlay">
      {/* 信息区：常驻显示 */}
      <AffectionBadge affection={affection} />
      <EmotionBadge emotion={emotion} />
      <div className="conn-dot" data-status={connStatus} />

      {/* 操作区：hover 显示图标，默认隐藏 */}
      <button className="overlay-btn" onClick={onToggleMode} title="切换模式">
        <Icon name="switch" size={15} />
      </button>
      <button className="overlay-btn" onClick={onOpenSettings} title="设置">
        <Icon name="settings" size={15} />
      </button>
      <button className="overlay-btn" onClick={onInterrupt} title="打断 AI 说话">
        <Icon name="stop" size={15} />
      </button>
    </div>
  );
}

import type { ReactElement } from 'react';
import type { AffectionSummary } from '@/types/ws';

export interface AffectionBadgeProps {
  affection: AffectionSummary | null;
}

/**
 * 好感度徽章：显示当前关系层级与好感度数值（桌宠模式小圆标）。
 * 未收到后端数据时为 null，不渲染。
 */
export function AffectionBadge({ affection }: AffectionBadgeProps): ReactElement | null {
  if (!affection) return null;
  return (
    <span className="affection-badge" title={`${affection.description}${affection.next ? ` 下一级：${affection.next.name}(${affection.next.threshold})` : '（已达最高关系）'}`}>
      ♥ {affection.tier} · {affection.value}
    </span>
  );
}

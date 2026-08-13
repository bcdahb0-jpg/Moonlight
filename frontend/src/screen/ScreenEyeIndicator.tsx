/**
 * ScreenEyeIndicator — 屏幕感知「眼睛」状态灯（Phase 5）。
 * 低干扰：桌宠悬浮条常驻小圆点；active=识别中（绿点呼吸）、paused=暂停。
 * 点击 = 快捷暂停 / 恢复。
 */
import type { ReactElement } from 'react';
import { Icon } from '@/ui/icons';

export interface ScreenEyeIndicatorProps {
  /** 屏幕感知是否启用（settings.screenAwareEnabled）。 */
  enabled: boolean;
  /** 后端是否正在采集/识别。 */
  capturing?: boolean;
  onToggle: () => void;
}

export function ScreenEyeIndicator({
  enabled,
  capturing = false,
  onToggle,
}: ScreenEyeIndicatorProps): ReactElement {
  const cls = ['screen-eye-indicator'];
  if (enabled && capturing) cls.push('active');
  else if (!enabled) cls.push('paused');
  return (
    <button
      className={cls.join(' ')}
      onClick={onToggle}
      aria-label={enabled ? '暂停屏幕感知' : '启用屏幕感知'}
      title={enabled ? '屏幕感知已开启（点击暂停）' : '屏幕感知已暂停（点击开启）'}
    >
      <Icon name="eye" size={14} />
    </button>
  );
}

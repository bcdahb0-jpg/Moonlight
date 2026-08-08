import type { ReactElement } from 'react';

export interface SettingStatusProps {
  /** null = 不显示 */
  message: string | null;
  /** 明确指定；缺省时按文案自动判断（含「生效/重启」视为重启后生效；含错误关键词视为失败） */
  kind?: 'applied' | 'restart' | 'error';
}

/** 错误文案关键词：命中即以红色错误样式展示（不再误显示「✅ 已应用」前缀）。 */
const ERROR_HINTS = /失败|错误|Missing|Invalid|无法|请填写|请选择|不存在|未配置|404|409|500|Exception/i;

/**
 * 统一保存反馈。区分三种状态：
 * - ✅ 已应用：改动立即生效
 * - ⚠️ 重启/重选角色后生效
 * - 🔴 错误：红色样式，文案原样展示
 * 消除各设置页文案不一致造成的「到底生效没有」困惑。
 */
export function SettingStatus({ message, kind }: SettingStatusProps): ReactElement | null {
  if (!message) return null;
  const isError = kind === 'error' || (kind === undefined && ERROR_HINTS.test(message));
  if (isError) {
    return (
      <div className="setting-status error" data-kind="error">
        {message}
      </div>
    );
  }
  const isRestart =
    kind === 'restart' || (kind === undefined && /生效|重启|重新选择/.test(message));
  const label = isRestart ? '⚠️ 重启/重选角色后生效' : '✅ 已应用';
  return (
    <div className="setting-status" data-kind={isRestart ? 'restart' : 'applied'}>
      {label} · {message}
    </div>
  );
}

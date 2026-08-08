/**
 * ErrorBanner — 结构化错误修复卡（Phase 3）。
 * 替代「把错误塞进聊天流」：显示在聊天面板顶部，带 code 对应文案 +
 * 一键修复动作（跳转设置分区）。
 */
import type { ReactElement } from 'react';
import { getErrorFix } from '@/ws/errorFixes';
import type { ErrorCode } from '@/types/ws';

export interface ErrorBannerProps {
  message: string | null;
  code: ErrorCode | null;
  onDismiss: () => void;
  onOpenSection?: (section: string) => void;
}

export function ErrorBanner({ message, code, onDismiss, onOpenSection }: ErrorBannerProps): ReactElement | null {
  if (!message) return null;
  const fix = getErrorFix(code);

  return (
    <div className="error-banner" role="alert">
      <div className="error-banner-body">
        <div className="error-banner-title">
          {code ? <span className="error-banner-code">{code}</span> : null}
          <span>{message}</span>
        </div>
        <div className="error-banner-fix">{fix.action}</div>
      </div>
      <div className="error-banner-actions">
        {fix.section && onOpenSection ? (
          <button className="btn btn-sm" onClick={() => onOpenSection(fix.section!)}>
            去修复
          </button>
        ) : null}
        <button className="btn btn-sm btn-ghost" onClick={onDismiss} title="关闭">
          关闭
        </button>
      </div>
    </div>
  );
}

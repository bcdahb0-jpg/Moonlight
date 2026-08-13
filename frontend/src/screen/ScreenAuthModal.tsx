/**
 * ScreenAuthModal — 首次开启屏幕感知的授权说明（Phase 5）。
 * 明确告知：读取的是前台窗口标题与画面摘要；原始截图仅存内存 ≤30s、
 * 不落盘、不写日志、不进长期记忆；可随时暂停/清除。
 * 确认后写 localStorage 标记，不再重复打扰。
 */
import type { ReactElement } from 'react';

export const SCREEN_AUTH_KEY = 'moonlight.screenAuthSeen';

export interface ScreenAuthModalProps {
  open: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}

export function ScreenAuthModal({ open, onConfirm, onCancel }: ScreenAuthModalProps): ReactElement {
  if (!open) return <div />;
  return (
    <div className="mask open" onClick={(e) => e.target === e.currentTarget && onCancel()}>
      <div className="modal" role="dialog" aria-label="屏幕感知授权说明">
        <div className="m-title">开启屏幕感知</div>
        <div className="m-sub">小月将能够「看见」你的屏幕，用于理解你在做什么</div>

        <div className="m-field">
          <ul className="screen-auth-list">
            <li>识别前台窗口的画面内容（截图摘要），用于在你询问时提供上下文</li>
            <li>原始截图<b>仅存内存 ≤30 秒</b>，分析完立即释放</li>
            <li>默认<b>不落盘、不写日志、不进长期记忆</b></li>
            <li>密码/登录/支付页面与 Moonlight 自身窗口自动排除，不会截图</li>
            <li>可随时暂停或清除（设置页 / 桌宠眼睛图标）</li>
            <li>不会上传历史截图，仅在开启后识别新画面</li>
          </ul>
        </div>

        <div className="m-actions">
          <button className="btn" onClick={onCancel}>
            先不了
          </button>
          <button className="btn btn-primary" onClick={onConfirm}>
            我了解，开启
          </button>
        </div>
      </div>
    </div>
  );
}

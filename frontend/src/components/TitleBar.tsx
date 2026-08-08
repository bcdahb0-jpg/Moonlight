/**
 * TitleBar — 窗口模式（window-mode）自定义标题栏，参考 Codex 桌面端布局：
 * 左侧品牌区（logo + 名称）与操作入口（切换模式 / 设置 / 打断），
 * 右侧最小化 / 最大化还原 / 关闭三个窗口控制。
 * 标题栏本身是拖拽区（-webkit-app-region: drag），按钮显式 no-drag。
 */
import { useEffect, useState, type ReactElement } from 'react';
import { Icon } from '@/ui/icons';

export interface TitleBarProps {
  onToggleMode: () => void;
  onOpenSettings: () => void;
}

function MinIcon(): ReactElement {
  return (
    <svg width="12" height="12" viewBox="0 0 12 12" aria-hidden="true">
      <path d="M2 6.5h8" fill="none" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round" />
    </svg>
  );
}

function MaxIcon(): ReactElement {
  return (
    <svg width="12" height="12" viewBox="0 0 12 12" aria-hidden="true">
      <rect x="2.5" y="2.5" width="7" height="7" rx="1.2" fill="none" stroke="currentColor" strokeWidth="1.2" />
    </svg>
  );
}

function RestoreIcon(): ReactElement {
  return (
    <svg width="12" height="12" viewBox="0 0 12 12" aria-hidden="true">
      <path d="M4.5 3.5V2.5h5v5h-1" fill="none" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round" strokeLinejoin="round" />
      <rect x="2.5" y="4.5" width="5" height="5" rx="1.2" fill="none" stroke="currentColor" strokeWidth="1.2" />
    </svg>
  );
}

function CloseIcon(): ReactElement {
  return (
    <svg width="12" height="12" viewBox="0 0 12 12" aria-hidden="true">
      <path d="M2.5 2.5l7 7M9.5 2.5l-7 7" fill="none" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round" />
    </svg>
  );
}

export function TitleBar({
  onToggleMode,
  onOpenSettings,
}: TitleBarProps): ReactElement {
  const [maximized, setMaximized] = useState(false);

  useEffect(() => window.moonlight?.onMaximizeChanged(setMaximized), []);

  return (
    <header className="window-titlebar">
      <div className="window-titlebar-left">
        <div className="window-titlebar-brand">
          <span className="window-titlebar-logo" aria-hidden="true">
            <svg width="12" height="12" viewBox="0 0 12 12">
              <path
                d="M8.6 1.6a4.9 4.9 0 1 0 1.8 6.1A3.9 3.9 0 0 1 8.6 1.6Z"
                fill="none"
                stroke="currentColor"
                strokeWidth="1.1"
                strokeLinejoin="round"
              />
            </svg>
          </span>
          <span className="window-titlebar-name">Moonlight</span>
        </div>

        {/* 操作入口：切换模式 / 设置 */}
        <div className="window-titlebar-actions">
          <button
            type="button"
            className="titlebar-action"
            aria-label="切换模式"
            title="切换模式"
            onClick={onToggleMode}
          >
            <Icon name="switch" size={14} />
          </button>
          <button
            type="button"
            className="titlebar-action"
            aria-label="设置"
            title="设置"
            onClick={onOpenSettings}
          >
            <Icon name="settings" size={14} />
          </button>
        </div>
      </div>

      <div className="window-titlebar-controls">
        <button
          type="button"
          className="win-control"
          aria-label="最小化"
          title="最小化"
          onClick={() => void window.moonlight?.minimizeWindow()}
        >
          <MinIcon />
        </button>
        <button
          type="button"
          className="win-control"
          aria-label={maximized ? '还原' : '最大化'}
          title={maximized ? '还原' : '最大化'}
          onClick={() => void window.moonlight?.toggleMaximize()}
        >
          {maximized ? <RestoreIcon /> : <MaxIcon />}
        </button>
        <button
          type="button"
          className="win-control win-control-close"
          aria-label="关闭"
          title="关闭"
          onClick={() => void window.moonlight?.closeWindow()}
        >
          <CloseIcon />
        </button>
      </div>
    </header>
  );
}

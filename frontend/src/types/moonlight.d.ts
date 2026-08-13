/**
 * Types for the `window.moonlight` API exposed by electron/preload.ts through
 * contextBridge. This file is a global ambient declaration.
 */

export interface MoonlightCapturedWindow {
  base64: string;
  title: string;
  app: string;
}

/** screen_awareness Phase 1：主进程按前台 PID/标题匹配的截图结果。 */
export interface MoonlightCapturedWindowV2 {
  ok: boolean;
  /** blocked=true 表示隐私规则命中（0 张图像离开本机）。 */
  blocked?: boolean;
  reason?: string;
  error?: string;
  base64?: string;
  title?: string;
  app?: string;
  pid?: number;
  width?: number;
  height?: number;
  /** 截图源类型：window（前台窗口）| screen（全屏 fallback）。 */
  sourceType?: 'window';
  matchedBy?: string;
}

export interface MoonlightActiveWindow {
  title: string;
  app: string;
  pid?: number;
}

export interface MoonlightScreenAwareness {
  title: string;
  app: string;
  idleTime: number;
  capturedAt: number;
}

/** Window edge/corner a resize drag is attached to. */
export type ResizeDirection = 'n' | 's' | 'e' | 'w' | 'ne' | 'nw' | 'se' | 'sw';

export interface MoonlightAPI {
  captureActiveWindow(): Promise<MoonlightCapturedWindow>;
  /** Phase 1：按前台 PID/标题匹配截图 + 主进程缩放编码 + 隐私前置。 */
  captureActiveWindowV2(opts?: { maxSide?: number; quality?: number }): Promise<MoonlightCapturedWindowV2>;
  getActiveWindow(): Promise<MoonlightActiveWindow>;
  getIdleTime(): Promise<number>;
  toggleAlwaysOnTop(): Promise<boolean>;
  showWindow(): Promise<void>;
  hideWindow(): Promise<void>;
  setWindowSize(width: number, height: number): Promise<void>;
  setWindowMode(mode: 'pet' | 'window'): Promise<void>;
  moveBy(dx: number, dy: number): Promise<void>;
  setWindowPosition(x: number, y: number): Promise<void>;
  resizeWindow(direction: ResizeDirection, dx: number, dy: number): Promise<void>;
  minimizeWindow(): Promise<void>;
  toggleMaximize(): Promise<boolean>;
  closeWindow(): Promise<void>;
  onMaximizeChanged(callback: (isMaximized: boolean) => void): () => void;
  onToggleVisibility(callback: () => void): () => void;
  onOpenSettings(callback: () => void): () => void;
  onScreenAwareness(callback: (info: MoonlightScreenAwareness) => void): () => void;
  /** v5：原生目录选择对话框（取消返回 null）。 */
  selectDirectory(): Promise<string | null>;
  /** Phase 1（pet-ptt-workflow）：默认工作目录（用户主目录），桌宠自动建会话用。 */
  getDefaultWorkspace(): Promise<string>;
}

declare global {
  interface Window {
    moonlight: MoonlightAPI;
    /** Set by live2dcubismcore.min.js (see live2d/cubismCore.ts). */
    Live2DCubismCore?: unknown;
  }
}

export {};

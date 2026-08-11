/**
 * Types for the `window.moonlight` API exposed by electron/preload.ts through
 * contextBridge. This file is a global ambient declaration.
 */

export interface MoonlightCapturedWindow {
  base64: string;
  title: string;
  app: string;
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
}

declare global {
  interface Window {
    moonlight: MoonlightAPI;
    /** Set by live2dcubismcore.min.js (see live2d/cubismCore.ts). */
    Live2DCubismCore?: unknown;
  }
}

export {};

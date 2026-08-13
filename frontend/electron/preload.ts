import { contextBridge, ipcRenderer, IpcRendererEvent } from 'electron';

export interface ScreenAwarenessInfo {
  title: string;
  app: string;
  idleTime: number;
  capturedAt: number;
}

export interface ActiveWindowInfo {
  title: string;
  app: string;
  pid?: number;
}

export interface CapturedWindow {
  base64: string;
  title: string;
  app: string;
}

export interface CapturedWindowV2 {
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
  sourceType?: 'window' | 'screen';
  matchedBy?: string;
}

export interface MoonlightAPI {
  /** Capture the currently active window (fallback: full screen) as a data URL. */
  captureActiveWindow(): Promise<CapturedWindow>;
  /**
   * Capture the active window matched by foreground PID/title (Phase 1):
   * main-process resize + JPEG encode + privacy pre-check.
   */
  captureActiveWindowV2(opts?: { maxSide?: number; quality?: number }): Promise<CapturedWindowV2>;
  /** Get the title + app of the foreground window. */
  getActiveWindow(): Promise<ActiveWindowInfo>;
  /** Seconds since the user last pressed a key / moved the mouse. */
  getIdleTime(): Promise<number>;
  /** Toggle always-on-top; returns the new state. */
  toggleAlwaysOnTop(): Promise<boolean>;
  showWindow(): Promise<void>;
  hideWindow(): Promise<void>;
  /** Resize the BrowserWindow (used when switching pet/window mode). */
  setWindowSize(width: number, height: number): Promise<void>;
  /** Switch the window layout mode (pet vs window). */
  setWindowMode(mode: 'pet' | 'window'): Promise<void>;
  /** Move the window by an incremental delta (drag-to-move). */
  moveBy(dx: number, dy: number): Promise<void>;
  /** Set the window position to an absolute (x, y). */
  setWindowPosition(x: number, y: number): Promise<void>;
  /** Resize the window from an edge/corner by an incremental delta. */
  resizeWindow(direction: string, dx: number, dy: number): Promise<void>;
  /** Minimize the window (window-mode titlebar). */
  minimizeWindow(): Promise<void>;
  /** Toggle maximize/restore; resolves to the new maximized state. */
  toggleMaximize(): Promise<boolean>;
  /** Hide the window to tray (window-mode close button). */
  closeWindow(): Promise<void>;
  /** Notified when the window is maximized or restored. */
  onMaximizeChanged(callback: (isMaximized: boolean) => void): () => void;
  /** Notified when the global shortcut toggles visibility. */
  onToggleVisibility(callback: () => void): () => void;
  /** Notified when the tray "设置" menu item is clicked. */
  onOpenSettings(callback: () => void): () => void;
  /** Notified when screen-awareness poll data is pushed from main. */
  onScreenAwareness(callback: (info: ScreenAwarenessInfo) => void): () => void;
  /** v5：原生目录选择对话框（取消返回 null）。 */
  selectDirectory(): Promise<string | null>;
  /** Phase 1（pet-ptt-workflow）：默认工作目录（用户主目录），桌宠自动建会话用。 */
  getDefaultWorkspace(): Promise<string>;
}

const api: MoonlightAPI = {
  captureActiveWindow: () => ipcRenderer.invoke('screen:capture-active-window'),
  captureActiveWindowV2: (opts?: { maxSide?: number; quality?: number }) =>
    ipcRenderer.invoke('screen:capture-active-window-v2', opts),
  getActiveWindow: () => ipcRenderer.invoke('screen:get-active-window'),
  getIdleTime: () => ipcRenderer.invoke('screen:get-idle-time'),
  selectDirectory: () => ipcRenderer.invoke('dialog:select-directory'),
  getDefaultWorkspace: () => ipcRenderer.invoke('workspace:get-default'),
  toggleAlwaysOnTop: () => ipcRenderer.invoke('win:toggle-always-on-top'),
  showWindow: () => ipcRenderer.invoke('win:show'),
  hideWindow: () => ipcRenderer.invoke('win:hide'),
  setWindowSize: (width: number, height: number) =>
    ipcRenderer.invoke('win:set-size', width, height),
  setWindowMode: (mode: 'pet' | 'window') => ipcRenderer.invoke('win:set-mode', mode),
  moveBy: (dx: number, dy: number) => ipcRenderer.invoke('win:move-by', dx, dy),
  setWindowPosition: (x: number, y: number) =>
    ipcRenderer.invoke('win:set-position', x, y),
  resizeWindow: (direction: string, dx: number, dy: number) =>
    ipcRenderer.invoke('win:resize', direction, dx, dy),
  minimizeWindow: () => ipcRenderer.invoke('win:minimize'),
  toggleMaximize: () => ipcRenderer.invoke('win:toggle-maximize'),
  closeWindow: () => ipcRenderer.invoke('win:close'),
  onMaximizeChanged: (callback: (isMaximized: boolean) => void) => {
    const listener = (_event: IpcRendererEvent, isMaximized: boolean) =>
      callback(isMaximized);
    ipcRenderer.on('win:maximize-changed', listener);
    return () => ipcRenderer.removeListener('win:maximize-changed', listener);
  },
  onToggleVisibility: (callback: () => void) => {
    const listener = (_event: IpcRendererEvent) => callback();
    ipcRenderer.on('win:toggle-visibility', listener);
    return () => ipcRenderer.removeListener('win:toggle-visibility', listener);
  },
  onOpenSettings: (callback: () => void) => {
    const listener = (_event: IpcRendererEvent) => callback();
    ipcRenderer.on('win:open-settings', listener);
    return () => ipcRenderer.removeListener('win:open-settings', listener);
  },
  onScreenAwareness: (callback: (info: ScreenAwarenessInfo) => void) => {
    const listener = (_event: IpcRendererEvent, info: ScreenAwarenessInfo) => callback(info);
    ipcRenderer.on('screen:awareness-update', listener);
    return () => ipcRenderer.removeListener('screen:awareness-update', listener);
  },
};

contextBridge.exposeInMainWorld('moonlight', api);

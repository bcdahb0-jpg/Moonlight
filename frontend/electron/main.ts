import { app, BrowserWindow, Tray, Menu, globalShortcut, ipcMain, screen, desktopCapturer, nativeImage, powerMonitor } from 'electron';
import * as path from 'node:path';
import { getActiveWindow } from './active-window';

const DEV_SERVER_URL = process.env.VITE_DEV_SERVER_URL;

// --------------------------------------------------------------------------- //
// Window sizing presets (pet vs window mode)
// --------------------------------------------------------------------------- //
const PET_SIZE = { width: 400, height: 600 };
// 窗口模式默认大小：主屏工作区宽高的比例（“大窗口但不全屏”，像常规桌面软件）。
const WINDOW_SIZE_RATIO = { width: 0.72, height: 0.78 };
const MIN_WINDOW_SIZE = { width: 240, height: 360 };

let mainWindow: BrowserWindow | null = null;
let tray: Tray | null = null;
let alwaysOnTop = true;

const CSP =
  "default-src 'self'; script-src 'self' 'unsafe-inline' 'unsafe-eval'; style-src 'self' 'unsafe-inline'; " +
  "img-src * data: blob:; media-src * data: blob:; font-src 'self' data:; " +
  "connect-src * data: blob:; worker-src 'self' blob:;";

function applyCSP(win: BrowserWindow): void {
  win.webContents.session.webRequest.onHeadersReceived((details, callback) => {
    callback({
      responseHeaders: {
        ...details.responseHeaders,
        'Content-Security-Policy': [CSP],
      },
    });
  });
}

// --------------------------------------------------------------------------- //
// Tray icon (embedded 32x32 moon PNG, base64)
// --------------------------------------------------------------------------- //
const TRAY_ICON_DATA_URL =
  'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAACAAAAAgCAYAAABzenr0AAAAIElEQVR42u3OQQ0AIBDAsIP/6eEIbEAVu5mS6dvXkgALqNwMAtE9m/sAAAAASUVORK5CYII=';

function createTrayIcon(): Tray {
  const image = nativeImage.createFromDataURL(TRAY_ICON_DATA_URL);
  const trayIcon = new Tray(image.resize({ width: 16, height: 16 }));
  trayIcon.setToolTip('Moonlight');
  trayIcon.setContextMenu(buildTrayMenu());
  trayIcon.on('click', () => toggleVisibility());
  return trayIcon;
}

function buildTrayMenu(): Menu {
  return Menu.buildFromTemplate([
    { label: '显示 / 隐藏', click: () => toggleVisibility() },
    { type: 'separator' },
    {
      label: '窗口模式',
      type: 'radio',
      checked: false,
      click: () => {
        mainWindow?.webContents.send('win:set-mode', 'window');
      },
    },
    {
      label: '桌宠模式',
      type: 'radio',
      checked: true,
      click: () => {
        mainWindow?.webContents.send('win:set-mode', 'pet');
      },
    },
    { type: 'separator' },
    { label: '设置', click: () => openSettings() },
    { type: 'separator' },
    { label: '退出', click: () => quitApp() },
  ]);
}

// --------------------------------------------------------------------------- //
// Window lifecycle
// --------------------------------------------------------------------------- //
function createWindow(): void {
  const primary = screen.getPrimaryDisplay();
  const { x, y } = primary.workArea;

  mainWindow = new BrowserWindow({
    width: PET_SIZE.width,
    height: PET_SIZE.height,
    x: x + primary.workAreaSize.width - PET_SIZE.width - 24,
    y: y + primary.workAreaSize.height - PET_SIZE.height - 24,
    frame: false,
    transparent: true,
    alwaysOnTop: true,
    hasShadow: false,
    resizable: true,
    skipTaskbar: true,
    minimizable: true,
    maximizable: true,
    fullscreenable: false,
    backgroundColor: '#00000000',
    webPreferences: {
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: false,
      preload: path.join(__dirname, 'preload.js'),
    },
  });

  mainWindow.setAlwaysOnTop(true, 'screen-saver');

  // 自定义标题栏（窗口模式）需要的最小化/最大化/关闭能力。
  // 注意：Windows 上 transparent 窗口最大化时透明合成层会残留伪影，
  // 最大化瞬间把背景色切为不透明兜底，还原时恢复透明。
  mainWindow.on('maximize', () => {
    mainWindow?.setBackgroundColor('#0d0d0d');
    mainWindow?.webContents.send('win:maximize-changed', true);
  });
  mainWindow.on('unmaximize', () => {
    mainWindow?.setBackgroundColor('#00000000');
    mainWindow?.webContents.send('win:maximize-changed', false);
  });

  if (DEV_SERVER_URL) {
    void mainWindow.loadURL(DEV_SERVER_URL);
  } else {
    void mainWindow.loadFile(path.join(__dirname, '..', 'dist', 'index.html'));
  }

  applyCSP(mainWindow);

  mainWindow.on('closed', () => {
    mainWindow = null;
  });
}

function openSettings(): void {
  mainWindow?.webContents.send('win:open-settings');
}

function toggleVisibility(): void {
  if (!mainWindow) return;
  if (mainWindow.isVisible()) {
    mainWindow.hide();
  } else {
    mainWindow.show();
    mainWindow.focus();
  }
  mainWindow.webContents.send('win:toggle-visibility');
}

function quitApp(): void {
  app.quit();
}

// --------------------------------------------------------------------------- //
// IPC handlers
// --------------------------------------------------------------------------- //
function registerIpcHandlers(): void {
  ipcMain.handle('screen:capture-active-window', async () => {
    try {
      const sources = await desktopCapturer.getSources({
        types: ['window'],
        thumbnailSize: { width: 768, height: 768 },
      });
      // Prefer the visible, non-empty titled window; fall back to full screen.
      let source = sources.find((s) => s.thumbnail && !s.thumbnail.isEmpty() && s.name !== '');
      let fallback: Electron.DesktopCapturerSource | undefined;
      if (!source) {
        const screenSources = await desktopCapturer.getSources({
          types: ['screen'],
          thumbnailSize: { width: 768, height: 768 },
        });
        fallback = screenSources[0];
        source = fallback;
      }
      if (!source) return { base64: '', title: '', app: '' };
      return {
        base64: source.thumbnail.toDataURL(),
        title: source.name,
        app: '',
      };
    } catch {
      return { base64: '', title: '', app: '' };
    }
  });

  ipcMain.handle('screen:get-active-window', async () => {
    const info = await getActiveWindow();
    return info ?? { title: '', app: '', pid: 0 };
  });

  ipcMain.handle('screen:get-idle-time', () => powerMonitor.getSystemIdleTime());

  ipcMain.handle('win:toggle-always-on-top', () => {
    alwaysOnTop = !alwaysOnTop;
    mainWindow?.setAlwaysOnTop(alwaysOnTop, 'screen-saver');
    return alwaysOnTop;
  });

  ipcMain.handle('win:show', () => {
    mainWindow?.show();
    mainWindow?.focus();
  });

  ipcMain.handle('win:hide', () => mainWindow?.hide());

  // 窗口模式自定义标题栏：最小化 / 最大化还原 / 关闭。
  ipcMain.handle('win:minimize', () => {
    mainWindow?.minimize();
  });

  ipcMain.handle('win:toggle-maximize', () => {
    if (!mainWindow) return false;
    if (mainWindow.isMaximized()) {
      mainWindow.unmaximize();
      return false;
    }
    mainWindow.maximize();
    return true;
  });

  ipcMain.handle('win:close', () => {
    // 桌宠应用惯例：关闭按钮隐藏到托盘（托盘菜单可恢复 / 退出），
    // 而不是直接 app.quit() —— 用户仍保有桌宠模式的常驻入口。
    mainWindow?.hide();
  });

  ipcMain.handle('win:set-size', (_event, width: number, height: number) => {
    if (!mainWindow) return;
    const bounds = mainWindow.getBounds();
    mainWindow.setBounds({
      x: bounds.x,
      y: bounds.y,
      width: Math.max(200, Math.round(width)),
      height: Math.max(200, Math.round(height)),
    });
  });

  ipcMain.handle('win:set-mode', (_event, mode: 'pet' | 'window') => {
    if (!mainWindow) return;
    // ① 最大化状态下 setSize/setBounds 会被系统忽略（Windows 接管最大化窗口
    //    尺寸），必须先 unmaximize；unmaximize 事件同时会把背景色恢复为透明。
    const wasMaximized = mainWindow.isMaximized();
    if (wasMaximized) {
      mainWindow.unmaximize();
    }
    // ② 防御性显式恢复透明背景：maximize 兜底背景色(#0d0d0d)若残留，
    //    桌宠模式的透明内容会直接露在纯黑窗口底上（「黑屏」）。
    mainWindow.setBackgroundColor('#00000000');

    // ③ 置顶策略：桌宠模式必须置顶（悬浮于其他窗口之上）；窗口模式
    //    像常规桌面软件一样不置顶。
    alwaysOnTop = mode === 'pet';
    mainWindow.setAlwaysOnTop(alwaysOnTop, 'screen-saver');

    const workArea = screen.getPrimaryDisplay().workArea;
    const applyMode = (): void => {
      if (!mainWindow) return;
      if (mode === 'window') {
        // 窗口模式：大窗口但不全屏（工作区 72% × 78%），并居中显示。
        const width = Math.max(
          MIN_WINDOW_SIZE.width,
          Math.round(workArea.width * WINDOW_SIZE_RATIO.width),
        );
        const height = Math.max(
          MIN_WINDOW_SIZE.height,
          Math.round(workArea.height * WINDOW_SIZE_RATIO.height),
        );
        // setBounds 一步到位（尺寸+位置原子），避免 setSize+center 两步
        // 之间窗口闪现/错位。
        mainWindow.setBounds({
          x: workArea.x + Math.round((workArea.width - width) / 2),
          y: workArea.y + Math.round((workArea.height - height) / 2),
          width,
          height,
        });
      } else {
        // 桌宠模式：恢复小尺寸，并归位到工作区右下角（setBounds 原子应用，
        // 不带 animate——动画与置顶切换叠加时在 Windows 上容易中断/闪烁）。
        mainWindow.setBounds({
          x: workArea.x + workArea.width - PET_SIZE.width - 24,
          y: workArea.y + workArea.height - PET_SIZE.height - 24,
          width: PET_SIZE.width,
          height: PET_SIZE.height,
        });
      }
    };

    if (wasMaximized) {
      // ④ unmaximize 是异步动画，动画期间（约 200-300ms）Windows 仍接管
      //    窗口尺寸，立即 setBounds 会被忽略 → 桌宠模式保持最大化的大窗口。
      //    延迟到动画完成后应用，根治「桌宠模式突然变大」。
      setTimeout(applyMode, 250);
    } else {
      applyMode();
    }
  });

  // Drag-to-move: renderer sends incremental deltas computed from screen-space
  // pointer positions, so we never round-trip an async position read mid-drag.
  ipcMain.handle('win:move-by', (_event, dx: number, dy: number) => {
    if (!mainWindow) return;
    if (typeof dx !== 'number' || typeof dy !== 'number') return;
    const [x, y] = mainWindow.getPosition();
    mainWindow.setPosition(Math.round(x + dx), Math.round(y + dy));
  });

  // Absolute position set (used e.g. by a "复位" / reset helper or tests).
  ipcMain.handle('win:set-position', (_event, x: number, y: number) => {
    if (!mainWindow) return;
    const bounds = mainWindow.getBounds();
    mainWindow.setBounds({
      x: Math.round(x),
      y: Math.round(y),
      width: bounds.width,
      height: bounds.height,
    });
  });

  // Resize from an edge/corner by an incremental delta. Left/top edges also
  // shift the window origin so the opposite edge stays anchored while dragging.
  const RESIZE_DIRS = /^(n|s|e|w|ne|nw|se|sw)$/;
  ipcMain.handle(
    'win:resize',
    (_event, direction: string, dx: number, dy: number) => {
      if (!mainWindow || !RESIZE_DIRS.test(direction)) return;
      if (typeof dx !== 'number' || !Number.isFinite(dx)) dx = 0;
      if (typeof dy !== 'number' || !Number.isFinite(dy)) dy = 0;
      const bounds = mainWindow.getBounds();
      let { x, y, width, height } = bounds;

      if (direction.includes('e')) {
        width = Math.max(MIN_WINDOW_SIZE.width, width + dx);
      } else if (direction.includes('w')) {
        const next = Math.max(MIN_WINDOW_SIZE.width, width - dx);
        x += width - next;
        width = next;
      }
      if (direction.includes('s')) {
        height = Math.max(MIN_WINDOW_SIZE.height, height + dy);
      } else if (direction.includes('n')) {
        const next = Math.max(MIN_WINDOW_SIZE.height, height - dy);
        y += height - next;
        height = next;
      }

      mainWindow.setBounds({ x, y, width, height });
    },
  );

  // Notify the renderer when the user toggles via the global shortcut / tray.
  ipcMain.on('win:open-settings', () => {
    mainWindow?.webContents.send('win:open-settings');
  });
}

// --------------------------------------------------------------------------- //
// App lifecycle
// --------------------------------------------------------------------------- //
const gotLock = app.requestSingleInstanceLock();
if (!gotLock) {
  app.quit();
} else {
  app.on('second-instance', () => {
    if (mainWindow) {
      mainWindow.show();
      mainWindow.focus();
    }
  });

  app.whenReady().then(() => {
    registerIpcHandlers();

    // Phase 3：打包模式下托管后端子进程（dev 模式开发者自己起后端，跳过）。
    // 后端启动可能耗时（首次要下载 ASR 模型），不阻塞窗口创建。
    if (!DEV_SERVER_URL) {
      void import('./backendManager').then(async ({ ensureBackend }) => {
        try {
          await ensureBackend();
        } catch (err) {
          // eslint-disable-next-line no-console
          console.warn('[backend] ensureBackend failed:', err);
        }
      });
    }

    tray = createTrayIcon();

    globalShortcut.register('Ctrl+Shift+P', toggleVisibility);

    createWindow();

    app.on('activate', () => {
      if (BrowserWindow.getAllWindows().length === 0) createWindow();
    });
  });

  app.on('will-quit', () => {
    globalShortcut.unregisterAll();
    tray?.destroy();
    tray = null;
    if (!DEV_SERVER_URL) {
      void import('./backendManager').then(({ stopBackend }) => stopBackend());
    }
  });

  app.on('window-all-closed', () => {
    if (process.platform !== 'darwin') app.quit();
  });
}

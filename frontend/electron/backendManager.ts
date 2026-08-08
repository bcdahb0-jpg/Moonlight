/**
 * backendManager — Electron 主进程托管后端子进程（Phase 3）。
 *
 * 目标：打包分发后用户「双击 → 桌宠出现」，无需手动 `uv run run_server.py`。
 *
 * 行为：
 * 1. 先探测 12393 端口：已在监听（用户手动起的后端 / 开发模式）→ 不接管。
 * 2. 未监听 → spawn 后端子进程（优先 MOONLIGHT_BACKEND_PYTHON 环境变量，其次
 *    `uv run` / 系统 python），cwd 指向 backend 目录。
 * 3. 日志经管道转发到主进程 console；崩溃（非零退出）后最多重启 MAX_RESTARTS 次。
 * 4. 应用退出时 kill 子进程（Windows 用 taskkill /T 杀进程树）。
 *
 * ⚠️ 验证状态：本模块在真实 Electron 打包环境（electron-builder 产物）下尚未实测；
 * 打包时的 backend 目录位置由 electron-builder.yml 的 files 决定，运行时可经
 * MOONLIGHT_BACKEND_DIR 覆盖。开发模式（VITE_DEV_SERVER_URL）下不启用。
 */
import { spawn, ChildProcess } from 'node:child_process';
import * as net from 'node:net';
import * as path from 'node:path';
import * as fs from 'node:fs';

const BACKEND_PORT = 12393;
const PROBE_TIMEOUT_MS = 1200;
const RESTART_DELAY_MS = 3000;
const MAX_RESTARTS = 3;

let child: ChildProcess | null = null;
let restartCount = 0;
let stopping = false;

/** 解析 backend 目录：环境变量 > dev 仓库布局 > 打包布局。 */
function resolveBackendDir(): string | null {
  if (process.env.MOONLIGHT_BACKEND_DIR) {
    return process.env.MOONLIGHT_BACKEND_DIR;
  }
  const candidates = [
    // 打包后：可执行文件旁的 backend/（electron-builder extraResources）
    path.join(path.dirname(process.execPath), 'backend'),
    // dev：仓库根 backend（main.ts 在 frontend/electron/）
    path.resolve(__dirname, '../../backend'),
  ];
  for (const dir of candidates) {
    try {
      if (fs.existsSync(path.join(dir, 'run_server.py'))) return dir;
    } catch {
      // ignore
    }
  }
  return null;
}

function isPortOpen(port: number, timeoutMs = PROBE_TIMEOUT_MS): Promise<boolean> {
  return new Promise((resolve) => {
    const socket = net.connect({ port, host: '127.0.0.1' });
    const timer = setTimeout(() => {
      socket.destroy();
      resolve(false);
    }, timeoutMs);
    socket.once('connect', () => {
      clearTimeout(timer);
      socket.destroy();
      resolve(true);
    });
    socket.once('error', () => {
      clearTimeout(timer);
      socket.destroy();
      resolve(false);
    });
  });
}

function spawnBackend(backendDir: string): void {
  stopping = false;
  const python = process.env.MOONLIGHT_BACKEND_PYTHON;
  // 无显式解释器时优先 uv（项目用 uv.lock 管理依赖）。
  const command = python ?? 'uv';
  const args = python ? ['run_server.py'] : ['run', 'python', 'run_server.py'];

  // eslint-disable-next-line no-console
  console.log(`[backend] spawning ${command} ${args.join(' ')} (cwd=${backendDir})`);

  child = spawn(command, args, {
    cwd: backendDir,
    env: { ...process.env, PYTHONUNBUFFERED: '1' },
    stdio: ['ignore', 'pipe', 'pipe'],
    windowsHide: true,
  });

  child.stdout?.on('data', (d: Buffer) => process.stdout.write(`[backend] ${d}`));
  child.stderr?.on('data', (d: Buffer) => process.stderr.write(`[backend] ${d}`));

  child.on('error', (err) => {
    // eslint-disable-next-line no-console
    console.error(`[backend] spawn error: ${err.message}`);
    child = null;
  });

  child.on('exit', (code, signal) => {
    child = null;
    if (stopping) return;
    // eslint-disable-next-line no-console
    console.log(`[backend] exited code=${code} signal=${signal}`);
    if (code !== 0 && restartCount < MAX_RESTARTS) {
      restartCount += 1;
      // eslint-disable-next-line no-console
      console.log(`[backend] restarting (${restartCount}/${MAX_RESTARTS}) in ${RESTART_DELAY_MS}ms`);
      setTimeout(() => spawnBackend(backendDir), RESTART_DELAY_MS);
    }
  });
}

export async function ensureBackend(): Promise<void> {
  if (await isPortOpen(BACKEND_PORT)) {
    // eslint-disable-next-line no-console
    console.log('[backend] already listening on 12393, skipping spawn');
    return;
  }
  const backendDir = resolveBackendDir();
  if (!backendDir) {
    // eslint-disable-next-line no-console
    console.warn('[backend] backend dir not found; user must start backend manually');
    return;
  }
  spawnBackend(backendDir);
}

/** 应用退出时停止后端子进程（Windows 杀进程树）。 */
export function stopBackend(): void {
  stopping = true;
  if (!child) return;
  const pid = child.pid;
  child.kill();
  if (pid && process.platform === 'win32') {
    try {
      spawn('taskkill', ['/pid', String(pid), '/T', '/F']);
    } catch {
      // best-effort
    }
  }
  child = null;
}

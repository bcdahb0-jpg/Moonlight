import { spawn } from 'node:child_process';
import { createRequire } from 'node:module';
import { context } from 'esbuild';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const require = createRequire(import.meta.url);
// `require('electron')` from plain Node returns the path to the electron binary.
const electronBinary = require('electron');
const root = path.dirname(path.dirname(fileURLToPath(import.meta.url)));

const esbuildOptions = {
  bundle: true,
  platform: 'node',
  format: 'cjs',
  target: 'node20',
  external: ['electron', 'active-win'],
  sourcemap: true,
  logLevel: 'info',
  entryPoints: {
    main: path.join(root, 'electron', 'main.ts'),
    preload: path.join(root, 'electron', 'preload.ts'),
  },
  outdir: path.join(root, 'dist-electron'),
};

let electronProcess = null;

function startElectron() {
  if (electronProcess && !electronProcess.killed) electronProcess.kill();
  const env = { ...process.env, VITE_DEV_SERVER_URL: 'http://127.0.0.1:5173' };
  // Electron 拒绝 NODE_OPTIONS=--use-system-ca（WorkBuddy 沙箱注入），且 ELECTRON_RUN_AS_NODE
  // 会把 electron 当纯 node 跑（无窗口）。dev 启动时强制清除，避免继承异常环境。
  delete env.NODE_OPTIONS;
  delete env.ELECTRON_RUN_AS_NODE;
  const args = ['.'];
  // 默认 userData 在 %APPDATA%\moonlight-frontend；若该目录被沙箱写保护（lockfile
  // 创建失败），可设 MOONLIGHT_USER_DATA 指向项目内目录绕过。
  if (process.env.MOONLIGHT_USER_DATA) {
    args.push(`--user-data-dir=${path.join(root, process.env.MOONLIGHT_USER_DATA)}`);
  }
  electronProcess = spawn(electronBinary, args, {
    cwd: root,
    stdio: 'inherit',
    env,
  });
}

function shutdown() {
  if (electronProcess && !electronProcess.killed) electronProcess.kill();
  process.exit(0);
}

// 1. Start Vite dev server (renderer).
const vite = spawn('npx vite', { cwd: root, stdio: 'inherit', shell: true });

const VITE_URL = 'http://127.0.0.1:5173';

// Wait until Vite is actually serving before launching Electron. Otherwise the
// window races ahead and shows a blank page (ERR_CONNECTION_REFUSED) with no
// retry. Poll every 250ms up to 20s; on timeout just proceed (Vite may be on a
// different port if 5173 was taken).
async function waitForVite(timeoutMs = 20000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    try {
      const res = await fetch(VITE_URL, { signal: AbortSignal.timeout(1500) });
      if (res.ok) return true;
    } catch {
      /* not ready yet */
    }
    await new Promise((r) => setTimeout(r, 250));
  }
  return false;
}

// 2. Build electron main, launch Electron, and watch for rebuilds via a plugin.
let firstBuild = true;
const restartElectronPlugin = {
  name: 'restart-electron',
  setup(build) {
    build.onEnd((result) => {
      if (result.errors.length > 0) return;
      if (firstBuild) {
        firstBuild = false;
        return;
      }
      console.log('[electron] rebuilt, restarting...');
      startElectron();
    });
  },
};

try {
  const ctx = await context({ ...esbuildOptions, plugins: [restartElectronPlugin] });
  await ctx.watch();
} catch (err) {
  console.error('[electron] initial build failed:', err);
  vite.kill();
  process.exit(1);
}

const ready = await waitForVite();
if (ready) {
  console.log('[dev] Vite ready, launching Electron...');
} else {
  console.warn('[dev] Vite not reachable in time, launching Electron anyway...');
}
startElectron();

process.on('SIGINT', shutdown);
process.on('SIGTERM', shutdown);

// Keep alive; ensure vite dies on exit.
vite.on('exit', () => process.exit(0));

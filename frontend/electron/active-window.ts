import { execFile } from 'node:child_process';
import { promisify } from 'node:util';

const execFileAsync = promisify(execFile);

export interface ActiveWindowResult {
  title: string;
  app: string;
  pid: number;
}

/**
 * Best-effort foreground-window lookup on Windows.
 *
 * `active-win` (v8) is the preferred source but it depends on the `koffi` native
 * addon, which may not be rebuilt for the running Electron ABI. When that fails we
 * fall back to a pure Win32 PowerShell query (no native deps), so the screen-
 * awareness feature never silently breaks.
 */
export async function getActiveWindow(): Promise<ActiveWindowResult | null> {
  try {
    const viaActiveWin = await tryActiveWin();
    if (viaActiveWin) return viaActiveWin;
  } catch {
    // fall through to PowerShell
  }
  try {
    const viaPs = await tryPowerShell();
    if (viaPs) return viaPs;
  } catch {
    // both sources failed -> return null (renderer handles gracefully)
  }
  return null;
}

async function tryActiveWin(): Promise<ActiveWindowResult | null> {
  // Dynamic require: only load the native module when actually needed, and never
  // let its absence crash the main process.
  // eslint-disable-next-line @typescript-eslint/no-var-requires
  const activeWin = require('active-win') as unknown as (opts?: {
    screenRecordingPolicy?: 'loose';
  }) => Promise<{ title?: string; owner?: { name?: string; processId?: number } } | null>;

  const window = await activeWin({ screenRecordingPolicy: 'loose' });
  if (!window) return null;
  return {
    title: window.title ?? '',
    app: window.owner?.name ?? '',
    pid: window.owner?.processId ?? 0,
  };
}

const PS_SCRIPT = String.raw`
Add-Type @"
using System;
using System.Runtime.InteropServices;
using System.Text;
public class FgWin {
  [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
  [DllImport("user32.dll", CharSet=CharSet.Unicode)] public static extern int GetWindowText(IntPtr hWnd, StringBuilder text, int count);
  [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr hWnd, out uint processId);
  [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr hWnd);
}
"@
$h = [FgWin]::GetForegroundWindow()
if ($h -eq [IntPtr]::Zero) { Write-Output '{}'; exit }
if (-not [FgWin]::IsWindowVisible($h)) { Write-Output '{}'; exit }
$sb = New-Object System.Text.StringBuilder 512
[void][FgWin]::GetWindowText($h, $sb, 512)
$pid = 0
[void][FgWin]::GetWindowThreadProcessId($h, [ref]$pid)
$procName = ''
try { $procName = (Get-Process -Id $pid -ErrorAction Stop).ProcessName } catch {}
[PSCustomObject]@{ title = $sb.ToString(); app = $procName; pid = $pid } | ConvertTo-Json -Compress
`;

async function tryPowerShell(): Promise<ActiveWindowResult | null> {
  const { stdout } = await execFileAsync('powershell.exe', [
    '-NoProfile',
    '-NonInteractive',
    '-ExecutionPolicy',
    'Bypass',
    '-Command',
    PS_SCRIPT,
  ], { timeout: 5000, windowsHide: true });
  const parsed = JSON.parse(stdout || '{}') as Partial<ActiveWindowResult>;
  if (!parsed.title && !parsed.app) return null;
  return {
    title: parsed.title ?? '',
    app: parsed.app ?? '',
    pid: parsed.pid ?? 0,
  };
}

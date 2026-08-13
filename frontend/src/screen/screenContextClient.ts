/**
 * 屏幕上下文客户端（Phase 2/3）：上传帧、启用/停用、清除、状态同步。
 * 薄封装 WSClient 的 screen 方法 + 本地最近状态缓存。
 */

import type { ScreenStatusMessage } from '@/types/ws';
import type { WSClient } from '@/api/wsClient';
import { imageHash, hashDiff } from './frameDiff';

export interface UploadFrame {
  window: { title: string; app: string; pid: number; bounds?: number[] };
  image: string;
  reason: 'window_changed' | 'content_changed' | 'user_requested';
  /** 帧被丢弃（重复/无变化）时不回调。 */
  onDropped?: (reason: string) => void;
}

export interface ScreenContextClient {
  /** 上传一帧（内部做 pHash 去重；变化才真正上传）。返回是否真的上传。 */
  upload(frame: UploadFrame): Promise<boolean>;
  enable(): void;
  disable(): void;
  clear(): void;
  /** 最近一次后端推送的状态。 */
  status(): ScreenStatusMessage | null;
  onStatusChange(cb: (s: ScreenStatusMessage) => void): () => void;
  /** 后端 screen-status 消息入口（由 useScreenAwareness 注入）。 */
  applyStatus(s: ScreenStatusMessage): void;
}

export function createScreenContextClient(
  ws: () => WSClient | null,
  changeThreshold: number,
): ScreenContextClient {
  let lastHash = '';
  let lastWindowKey = '';
  let lastStatus: ScreenStatusMessage | null = null;
  const listeners = new Set<(s: ScreenStatusMessage) => void>();

  const windowKey = (w: UploadFrame['window']): string =>
    `${w.app}|${w.title}|${w.pid}`;

  async function upload(frame: UploadFrame): Promise<boolean> {
    const client = ws();
    if (!client) return false;

    // ① 窗口身份去重：窗口切换强制上传（旧摘要立即失效）。
    const key = windowKey(frame.window);
    if (key !== lastWindowKey) {
      lastWindowKey = key;
      lastHash = '';
    }

    const hash = await imageHash(frame.image);

    // ② pHash 去重（静态画面不重复上传，省视觉 token）。
    //    user_requested（用户主动看屏幕）跳过去重。
    if (frame.reason !== 'user_requested') {
      if (!hash) {
        // hash 计算失败：保守丢弃（不分析），避免盲传。
        frame.onDropped?.('hash_failed');
        return false;
      }
      if (lastHash && hashDiff(lastHash, hash) < changeThreshold) {
        frame.onDropped?.('no_change');
        return false;
      }
      lastHash = hash;
    } else {
      lastHash = hash || '';
    }

    client.sendScreenFrame({
      frame_id: cryptoRandomId(),
      captured_at: Date.now() / 1000,
      window: frame.window,
      image: frame.image,
      image_hash: hash,
      reason: frame.reason,
    });
    return true;
  }

  function enable(): void {
    ws()?.sendScreenEnable(true);
  }

  function disable(): void {
    lastHash = '';
    lastWindowKey = '';
    ws()?.sendScreenEnable(false, 'user_disabled');
  }

  function clear(): void {
    lastHash = '';
    lastWindowKey = '';
    ws()?.sendScreenClear();
  }

  function status(): ScreenStatusMessage | null {
    return lastStatus;
  }

  function onStatusChange(cb: (s: ScreenStatusMessage) => void): () => void {
    listeners.add(cb);
    return () => listeners.delete(cb);
  }

  function applyStatus(s: ScreenStatusMessage): void {
    lastStatus = s;
    listeners.forEach((cb) => {
      try {
        cb(s);
      } catch {
        // 监听器异常不中断。
      }
    });
  }

  return { upload, enable, disable, clear, status, onStatusChange, applyStatus };
}

function cryptoRandomId(): string {
  if (typeof crypto !== 'undefined' && 'randomUUID' in crypto) {
    return crypto.randomUUID();
  }
  return `sf_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 10)}`;
}

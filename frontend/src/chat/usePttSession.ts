/**
 * usePttSession — 桌宠对讲会话确保（Phase 1）。
 *
 * 计划 docs/pet-ptt-workflow-plan.md §3.2：
 * - workspace 优先级：当前 currentHistoryUid 的 metadata.workspace → 用户最近
 *   选择的 workspace → 默认 workspace（用户主目录，Electron IPC 提供）；
 * - 桌宠没有会话时，第一次 PTT 先创建会话；在收到 new-history-created 前，
 *   录音 PCM 暂存于内存队列，创建成功后再一次性发送（失败回滚丢弃，不丢录音）。
 */
import { useCallback, useEffect, useRef } from 'react';
import type { WSClient } from '@/api/wsClient';
import type { HistoryEntry } from '@/state/types';

export interface PttSessionOptions {
  ws: () => WSClient | null;
  getHistoryUid: () => string | null;
  getHistoryList: () => HistoryEntry[];
}

const CREATE_TIMEOUT_MS = 10_000;

interface PendingCreate {
  resolve: (uid: string) => void;
  reject: (error: Error) => void;
  timer: number;
}

/** 解析最终 workspace：显式传入 > 最近会话使用过的 > 默认主目录。 */
export async function resolveWorkspace(
  explicit: string | undefined,
  historyList: HistoryEntry[],
): Promise<string> {
  const trimmed = (explicit ?? '').trim();
  if (trimmed) return trimmed;
  const recent = historyList
    .map((h) => String(h.workspace ?? '').trim())
    .filter(Boolean);
  if (recent.length > 0) return recent[0];
  try {
    const home = await window.moonlight?.getDefaultWorkspace();
    if (home && home.trim()) return home.trim();
  } catch {
    /* IPC 不可用（纯浏览器调试） */
  }
  return '';
}

export function usePttSession({
  ws,
  getHistoryUid,
  getHistoryList,
}: PttSessionOptions): {
  /** 确保存在会话：有 UID 直接返回；否则创建并等待 new-history-created。 */
  ensureHistory: (workspace?: string) => Promise<string | null>;
} {
  const pendingRef = useRef<Map<string, PendingCreate>>(new Map());
  const pendingSeqRef = useRef(0);
  const wsRef = useRef(ws);
  wsRef.current = ws;
  const getHistoryUidRef = useRef(getHistoryUid);
  getHistoryUidRef.current = getHistoryUid;
  const getHistoryListRef = useRef(getHistoryList);
  getHistoryListRef.current = getHistoryList;

  const ensureHistory = useCallback(
    async (workspace?: string): Promise<string | null> => {
      const client = wsRef.current();
      if (!client) return null;
      const uid = getHistoryUidRef.current();
      if (uid) return uid;

      const resolved = await resolveWorkspace(workspace, getHistoryListRef.current());
      if (!resolved) return null; // 无可用 workspace：放弃自动创建（由 UI 引导选择）。

      // 每个等待独立注册（自增 id 防覆盖）；new-history-created 到达时统一 resolve。
      const key = `new-history:${pendingSeqRef.current++}`;
      return new Promise<string | null>((resolve, reject) => {
        const timer = window.setTimeout(() => {
          pendingRef.current.delete(key);
          reject(new Error('创建会话超时'));
        }, CREATE_TIMEOUT_MS);
        pendingRef.current.set(key, { resolve, reject, timer });
        client.sendCreateNewHistory(resolved);
      });
    },
    [],
  );

  // 监听 new-history-created：解析所有等待中的创建。
  useEffect(() => {
    const client = wsRef.current();
    if (!client) return;
    const unsub = client.once('new-history-created', (msg) => {
      const uid = msg.history_uid;
      if (!uid) return;
      for (const [, entry] of pendingRef.current) {
        window.clearTimeout(entry.timer);
        entry.resolve(uid);
      }
      pendingRef.current.clear();
    });
    // 会话删除/清空时清理等待（防悬挂 promise）。
    const unsubDeleted = client.once('history-deleted', () => {
      for (const [, entry] of pendingRef.current) {
        window.clearTimeout(entry.timer);
        entry.reject(new Error('会话被删除'));
      }
      pendingRef.current.clear();
    });
    return () => {
      unsub();
      unsubDeleted();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return { ensureHistory };
}

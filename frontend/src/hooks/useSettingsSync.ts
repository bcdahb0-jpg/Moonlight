/**
 * useSettingsSync — 前端设置与后端配置 API 双向同步（Phase 2）。
 *
 * 单一事实源 = 后端 conf.yaml（system_config.ui_prefs）。localStorage 仅保留 theme。
 *
 * 行为：
 * 1. 挂载时 GET /api/config 拉取 ui_prefs，覆盖本地 state（首次运行时即把老用户
 *    localStorage 里旧的默认值迁移到新默认/服务端值——服务端成为事实源）。
 * 2. 监听 state.settings（仅 5 个 ui_prefs 字段，不含 theme），600ms 防抖后
 *    PUT /api/config 以 JSON Merge Patch 落盘；失败静默（下次变更重试）。
 * 3. 后端热重载成功后广播 config-updated（前端当前 no-op），不会造成同步回环。
 */
import { useEffect, useRef } from 'react';
import { useAppState } from '@/state/AppStateContext';
import { configApi } from '@/api/rest';
import type { LocalSettings } from '@/state/types';/** LocalSettings → 后端 UiPrefs（config_manager/system.py）的字段映射。 */
const UI_PREFS_MAP: Array<[keyof LocalSettings, string]> = [
  ['screenAwareEnabled', 'screen_aware_enabled'],
  ['screenPollIntervalSec', 'screen_poll_interval_sec'],
  ['proactiveEnabled', 'proactive_enabled'],
  ['proactiveIdleSec', 'proactive_idle_sec'],
  ['autoSpeakOnIdle', 'auto_speak_on_idle'],
  // UX 修复（2026-08-10）：与后端 UiPrefs 新增字段保持同步
  ['proactivePetModeOnly', 'proactive_pet_mode_only'],
  ['subtitleEnabled', 'subtitle_enabled'],
];

const SYNC_DEBOUNCE_MS = 600;

function pickUiPrefs(settings: LocalSettings): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const [localKey, backendKey] of UI_PREFS_MAP) out[backendKey] = settings[localKey];
  return out;
}

export function useSettingsSync(): void {
  const { state, dispatch } = useAppState();
  const timerRef = useRef<number | null>(null);
  const stateRef = useRef(state);
  stateRef.current = state;

  // 1) 启动拉取：服务端 ui_prefs 覆盖本地（迁移旧 localStorage 值）。
  //    与服务端值一致时不 dispatch，避免启动即触发一次无意义的写盘。
  useEffect(() => {
    let cancelled = false;
    void configApi
      .get()
      .then((cfg) => {
        if (cancelled) return;
        const prefs = cfg?.system_config?.ui_prefs;
        if (!prefs) return;
        const cur = stateRef.current.settings;
        const next: Partial<LocalSettings> = {
          screenAwareEnabled: prefs.screen_aware_enabled ?? cur.screenAwareEnabled,
          screenPollIntervalSec: prefs.screen_poll_interval_sec ?? cur.screenPollIntervalSec,
          proactiveEnabled: prefs.proactive_enabled ?? cur.proactiveEnabled,
          proactiveIdleSec: prefs.proactive_idle_sec ?? cur.proactiveIdleSec,
          autoSpeakOnIdle: prefs.auto_speak_on_idle ?? cur.autoSpeakOnIdle,
          proactivePetModeOnly: prefs.proactive_pet_mode_only ?? cur.proactivePetModeOnly,
          subtitleEnabled: prefs.subtitle_enabled ?? cur.subtitleEnabled,
        };
        const changed = UI_PREFS_MAP.some(([localKey]) => next[localKey] !== cur[localKey]);
        if (changed) dispatch({ type: 'UPDATE_SETTINGS', settings: next });
      })
      .catch(() => {
        // 后端未启动/不可达：沿用本地默认，下次启动再同步。
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // 2) 变更防抖落盘。
  useEffect(() => {
    const patch = pickUiPrefs(state.settings);
    if (timerRef.current !== null) window.clearTimeout(timerRef.current);
    timerRef.current = window.setTimeout(() => {
      timerRef.current = null;
      void configApi.update({ system_config: { ui_prefs: patch } }).catch(() => {
        // 落盘失败静默：后端不可达或校验拒绝；保留本地态，下次变更重试。
      });
    }, SYNC_DEBOUNCE_MS);
    return () => {
      if (timerRef.current !== null) window.clearTimeout(timerRef.current);
    };
  }, [state.settings, dispatch]);
}

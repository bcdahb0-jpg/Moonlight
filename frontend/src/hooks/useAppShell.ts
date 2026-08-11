/**
 * useAppShell — 应用外壳组合式 Hook（Phase 2：App.tsx 拆分核心）。
 *
 * 收纳全部「壳层」逻辑：音频引擎、WS 连接与契约分发、设置同步（配置 API）、
 * 屏幕感知、托盘事件、窗口交互（拖拽/缩放/穿透）。App.tsx 只负责装配与渲染。
 */
import { useCallback, useEffect, useMemo, useRef, useState as useReactState } from 'react';
import { useAppState } from '@/state/AppStateContext';
import { useWindowDrag } from '@/hooks/useWindowDrag';
import { useWindowResize } from '@/hooks/useWindowResize';
import { useScreenAwareness } from '@/screen/useScreenAwareness';
import { useSettingsSync } from '@/hooks/useSettingsSync';
import { WSClient } from '@/api/wsClient';
import { AudioPlayer } from '@/api/audioPlayer';
import { LipSyncDriver } from '@/live2d/LipSyncDriver';
import { expressionToEmotion } from '@/emotion/expression';
import { dispatchServerMessage, type WsHandlerDeps } from '@/ws/messageHandlers';
import type { Live2DAdapter } from '@/live2d/Live2DAdapter';
import type { VisemeVector } from '@/live2d/Live2DAdapter';
import type { LocalSettings, ThemeMode } from '@/state/types';
import { DEFAULT_SETTINGS } from '@/state/types';

const SETTINGS_KEY = 'moonlight.settings';
const PLAYBACK_COMPLETE_DELAY_MS = 250;

/** 读取 localStorage 中的主题（Phase 2：设置只存后端，本地仅留主题）。 */
function loadLocalTheme(): ThemeMode {
  try {
    const raw = localStorage.getItem(SETTINGS_KEY);
    if (raw) {
      const parsed = JSON.parse(raw) as Partial<LocalSettings>;
      if (parsed.theme === 'light' || parsed.theme === 'dark') return parsed.theme;
    }
  } catch {
    // corrupted storage -> default
  }
  return DEFAULT_SETTINGS.theme;
}

export interface AppShell {
  state: ReturnType<typeof useAppState>['state'];
  adapterRef: React.MutableRefObject<Live2DAdapter | null>;
  audioPlayerRef: React.MutableRefObject<AudioPlayer | null>;
  wsRef: React.MutableRefObject<WSClient | null>;
  toggleMode: () => void;
  settingsOpen: boolean;
  setSettingsOpen: (open: boolean) => void;
  handlePointerDown: (e: React.PointerEvent) => void;
  handlePointerMove: (e: React.PointerEvent) => void;
  handlePointerUp: () => void;
  petVisible: boolean;
  maximized: boolean;
}

export function useAppShell(): AppShell {
  const { state, dispatch } = useAppState();

  const adapterRef = useRef<Live2DAdapter | null>(null);
  const audioPlayerRef = useRef<AudioPlayer | null>(null);
  const lipDriverRef = useRef<LipSyncDriver | null>(null);
  /** 情绪生命周期计时（Phase 2：duration_ms 到期 revert 表情）。 */
  const emotionTimerRef = useRef<number | null>(null);
  const wsRef = useRef<WSClient | null>(null);
  const stateRef = useRef(state);
  const synthCompleteRef = useRef(false);
  const pendingAudioRef = useRef(0);
  const completeTimerRef = useRef<number | null>(null);

  stateRef.current = state;

  // ------------------------------------------------------------------ //
  // 设置：主题持久化到 localStorage；其余 ui_prefs 与后端配置 API 同步
  // ------------------------------------------------------------------ //
  useSettingsSync();

  useEffect(() => {
    document.documentElement.dataset.theme = state.theme;
  }, [state.theme]);

  // Hydrate persisted theme once.
  useEffect(() => {
    dispatch({ type: 'SET_THEME', theme: loadLocalTheme() });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // ------------------------------------------------------------------ //
  // Audio player (created once)
  // ------------------------------------------------------------------ //
  const maybeCompletePlayback = useCallback((): void => {
    if (completeTimerRef.current !== null) return;
    if (!synthCompleteRef.current) return;
    if (pendingAudioRef.current > 0 || audioPlayerRef.current?.isPlaying) return;

    // Give the backend a beat to register its wait_for_response handler.
    completeTimerRef.current = window.setTimeout(() => {
      completeTimerRef.current = null;
      synthCompleteRef.current = false;
      wsRef.current?.sendFrontendPlaybackComplete();
    }, PLAYBACK_COMPLETE_DELAY_MS);
  }, []);

  useEffect(() => {
    // 口型驱动层：rAF 平滑 + 说话状态机 + 动作联动（Talk ↔ Idle）
    const lipDriver = new LipSyncDriver(() => adapterRef.current);
    lipDriverRef.current = lipDriver;

    const player = new AudioPlayer({
      onVolume: (value, viseme) => {
        // AudioPlayer 层是通用 number[]，此处校验为五元音向量再交给驱动层
        const v: VisemeVector | null =
          viseme && viseme.length >= 5 ? (viseme as VisemeVector) : null;
        lipDriverRef.current?.onVolume(value, v);
      },
      onItemStart: (item) => {
        if (item.expression && item.expression.length > 0) {
          // Phase 1：emotion_meta.intensity 透传为表情强度（Soullink 生效）
          adapterRef.current?.setExpression(
            item.expression[0],
            item.emotionMeta?.intensity ?? null,
          );
          dispatch({
            type: 'SET_EMOTION',
            emotion: expressionToEmotion(item.expression[0], stateRef.current.modelInfo),
            intensity: item.emotionMeta?.intensity ?? null,
            source: item.emotionMeta?.source ?? null,
          });
          // Phase 2 情绪生命周期：duration_ms 到期归零（段内中途复位表情）
          const dur = item.emotionMeta?.duration_ms;
          if (dur && dur > 0) {
            if (emotionTimerRef.current !== null) {
              window.clearTimeout(emotionTimerRef.current);
            }
            emotionTimerRef.current = window.setTimeout(() => {
              emotionTimerRef.current = null;
              adapterRef.current?.revertExpression();
            }, dur);
          }
        }
        if (item.displayText) {
          wsRef.current?.sendAudioPlayStart({ text: item.displayText });
        }
        lipDriverRef.current?.onItemStart();
      },
      onItemEnd: (item) => {
        adapterRef.current?.revertExpression();
        if (emotionTimerRef.current !== null) {
          window.clearTimeout(emotionTimerRef.current);
          emotionTimerRef.current = null;
        }
        pendingAudioRef.current = Math.max(0, pendingAudioRef.current - 1);
        void item;
        lipDriverRef.current?.onItemEnd(false);
        maybeCompletePlayback();
      },
      onStop: () => {
        // 打断：stop() 不触发 onItemEnd，这里立即复位口型、动作与情绪计时
        adapterRef.current?.revertExpression();
        if (emotionTimerRef.current !== null) {
          window.clearTimeout(emotionTimerRef.current);
          emotionTimerRef.current = null;
        }
        lipDriverRef.current?.onItemEnd(true);
      },
      onQueueEmpty: () => maybeCompletePlayback(),
    });
    audioPlayerRef.current = player;
    return () => {
      player.stop();
      if (emotionTimerRef.current !== null) {
        window.clearTimeout(emotionTimerRef.current);
        emotionTimerRef.current = null;
      }
      lipDriverRef.current?.destroy();
      lipDriverRef.current = null;
      audioPlayerRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // ------------------------------------------------------------------ //
  // WS：契约层注册表分发（Phase 0）
  // ------------------------------------------------------------------ //
  const wsHandlerDeps = useMemo<WsHandlerDeps>(
    () => ({
      dispatch,
      getState: () => stateRef.current,
      ws: () => wsRef.current,
      audioPlayer: () => audioPlayerRef.current,
      adapter: () => adapterRef.current,
      setSynthComplete: (v: boolean) => {
        synthCompleteRef.current = v;
      },
      bumpPendingAudio: (delta: number) => {
        pendingAudioRef.current = Math.max(0, pendingAudioRef.current + delta);
      },
      resetPendingAudio: () => {
        pendingAudioRef.current = 0;
      },
      maybeCompletePlayback,
    }),
    [dispatch, maybeCompletePlayback],
  );

  const handleServerMessage = useCallback(
    (msg: import('@/types/ws').ServerMessage): void => {
      dispatchServerMessage(msg, wsHandlerDeps);
    },
    [wsHandlerDeps],
  );

  useEffect(() => {
    const ws = new WSClient({
      onStatusChange: (status) => dispatch({ type: 'SET_CONN_STATUS', status }),
      onMessage: handleServerMessage,
    });
    wsRef.current = ws;
    ws.connect();
    return () => {
      ws.disconnect();
      wsRef.current = null;
    };
  }, [dispatch, handleServerMessage]);

  // ------------------------------------------------------------------ //
  // Proactive / screen awareness
  // ------------------------------------------------------------------ //
  const settings = state.settings;
  const triggerProactive = useCallback((): void => {
    wsRef.current?.sendAiSpeakSignal();
  }, []);

  // UX 修复（2026-08-10）：主动找话题加「模式闸门」——proactivePetModeOnly
  // 开启时（默认）只有桌宠模式才触发；窗口模式（用户等待任务/思考输入）
  // 不得打扰。关闭该开关则恢复任意模式触发（兼容想全局使用的用户）。
  const petModeOnly = settings.proactivePetModeOnly && state.mode !== 'pet';

  useScreenAwareness({
    enabled: settings.screenAwareEnabled,
    pollIntervalSec: settings.screenPollIntervalSec,
    proactiveEnabled: settings.proactiveEnabled && !petModeOnly,
    proactiveIdleSec: settings.proactiveIdleSec,
    onProactiveTrigger: settings.autoSpeakOnIdle ? triggerProactive : () => undefined,
  });

  // ------------------------------------------------------------------ //
  // Tray / window shell
  // ------------------------------------------------------------------ //
  useEffect(() => {
    return window.moonlight?.onToggleVisibility(() => undefined);
  }, []);

  const [settingsOpen, setSettingsOpen] = useReactState(false);

  useEffect(() => {
    return window.moonlight?.onOpenSettings(() => setSettingsOpen(true));
  }, []);

  const toggleMode = useCallback((): void => {
    const mode = stateRef.current.mode === 'pet' ? 'window' : 'pet';
    dispatch({ type: 'SET_MODE', mode });
    window.moonlight?.setWindowMode(mode);
  }, [dispatch]);

  // ------------------------------------------------------------------ //
  // Maximized (窗口模式标题栏最大化/还原 → 移除圆角边框)
  // ------------------------------------------------------------------ //
  const [maximized, setMaximized] = useReactState(false);

  useEffect(() => window.moonlight?.onMaximizeChanged(setMaximized), []);

  // ------------------------------------------------------------------ //
  // Drag / resize (frameless window)
  // ------------------------------------------------------------------ //
  const drag = useWindowDrag();
  // 窗口模式才允许边缘拖拽缩放；桌宠模式禁 resize（悬浮小窗拖大后无法自动
  // 恢复，表现为「桌宠模式突然变大」）。
  const resize = useWindowResize(state.mode !== 'pet');

  // 全局兜底：任何路径下丢失 pointerup（指针逃逸出窗口、窗口移动/缩放导致
  // 捕获被平台取消、窗口失焦等）都会让 drag/resize 的 active 状态泄漏，之后
  // 鼠标只要在窗口内 hover 移动就会触发窗口跟随移动（「吸附」卡死）。
  // 在 window blur 与 document pointercancel 时强制重置两个手势状态。
  useEffect(() => {
    const reset = (): void => {
      resize.onPointerUp();
      drag.onPointerUp();
    };
    window.addEventListener('blur', reset);
    document.addEventListener('pointercancel', reset);
    return () => {
      window.removeEventListener('blur', reset);
      document.removeEventListener('pointercancel', reset);
    };
  }, [drag, resize]);

  const handlePointerDown = useCallback(
    (e: React.PointerEvent): void => {
      if (resize.onPointerDown(e)) return;
      drag.onPointerDown(e);
    },
    [drag, resize],
  );
  const handlePointerMove = useCallback(
    (e: React.PointerEvent): void => {
      if (resize.onPointerMove(e)) return;
      drag.onPointerMove(e);
    },
    [drag, resize],
  );
  const handlePointerUp = useCallback((): void => {
    resize.onPointerUp();
    drag.onPointerUp();
  }, [drag, resize]);

  return {
    state,
    adapterRef,
    audioPlayerRef,
    wsRef,
    toggleMode,
    settingsOpen,
    setSettingsOpen,
    handlePointerDown,
    handlePointerMove,
    handlePointerUp,
    petVisible: state.mode === 'pet',
    maximized,
  };
}

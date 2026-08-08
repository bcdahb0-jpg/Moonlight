import { useEffect, useRef } from 'react';
import { useAppState } from '@/state/AppStateContext';

export interface ScreenAwarenessOptions {
  enabled: boolean;
  pollIntervalSec: number;
  onProactiveTrigger: () => void;
  proactiveEnabled: boolean;
  proactiveIdleSec: number;
}

/**
 * Polls the Electron main process for the foreground window + system idle time,
 * writes the result into global state, and fires the proactive-speak trigger
 * when the user has been idle long enough.
 */
export function useScreenAwareness({
  enabled,
  pollIntervalSec,
  proactiveEnabled,
  proactiveIdleSec,
  onProactiveTrigger,
}: ScreenAwarenessOptions): void {
  const { dispatch } = useAppState();
  const triggeredRef = useRef(false);
  const triggerFnRef = useRef(onProactiveTrigger);
  triggerFnRef.current = onProactiveTrigger;

  useEffect(() => {
    if (!enabled || !window.moonlight) return;

    let disposed = false;
    let timer: number | null = null;
    let idling = false;

    const poll = async (): Promise<void> => {
      if (disposed) return;
      try {
        const [activeWindow, idleTime] = await Promise.all([
          window.moonlight.getActiveWindow(),
          window.moonlight.getIdleTime(),
        ]);
        dispatch({
          type: 'SET_ACTIVE_WINDOW',
          info: {
            title: activeWindow.title ?? '',
            app: activeWindow.app ?? '',
            idleTime,
            capturedAt: Date.now(),
          },
        });

        const shouldTrigger = proactiveEnabled && idleTime >= proactiveIdleSec;
        if (shouldTrigger && !idling && !triggeredRef.current) {
          idling = true;
          triggeredRef.current = true;
          triggerFnRef.current();
        } else if (!shouldTrigger && idling) {
          idling = false;
        } else if (!shouldTrigger) {
          triggeredRef.current = false;
        }
      } catch {
        // IPC unavailable (e.g. running in a plain browser); ignore.
      }
    };

    void poll();
    timer = window.setInterval(() => void poll(), Math.max(1, pollIntervalSec) * 1000);

    return () => {
      disposed = true;
      if (timer !== null) window.clearInterval(timer);
    };
  }, [enabled, pollIntervalSec, proactiveEnabled, proactiveIdleSec, dispatch]);
}

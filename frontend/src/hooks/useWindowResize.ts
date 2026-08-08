import { useCallback, useEffect, useRef } from 'react';
import type { ResizeDirection } from '@/types/moonlight';

/** Width of the grab zone along each window edge, in CSS pixels. */
const EDGE_PX = 5;

/** Movement (screen px) before an edge press is treated as an intentional resize
 * drag — a plain click near the edge (e.g. to interact with the canvas/chat)
 * must NOT resize the window. */
const RESIZE_THRESHOLD_PX = 5;

const CURSOR_BY_DIRECTION: Record<ResizeDirection, string> = {
  n: 'n-resize',
  s: 's-resize',
  e: 'e-resize',
  w: 'w-resize',
  ne: 'ne-resize',
  nw: 'nw-resize',
  se: 'se-resize',
  sw: 'sw-resize',
};

interface ResizeState {
  active: boolean;
  direction: ResizeDirection | null;
  lastX: number;
  lastY: number;
  startX: number;
  startY: number;
  moved: boolean;
}

interface WindowResizeHandlers {
  /**
   * Start a resize drag if the pointer is over a window edge. Returns true when
   * the gesture was claimed (callers should skip the move-drag in that case).
   */
  onPointerDown: (e: React.PointerEvent) => boolean;
  /** Feed resize deltas while active; also refreshes the hover cursor. */
  onPointerMove: (e: React.PointerEvent) => boolean;
  onPointerUp: () => void;
}

/**
 * Edge-drag resizing for the frameless pet window.
 *
 * Hovering within `EDGE_PX` of a window edge switches the cursor to the matching
 * resize arrow. Pressing on an edge claims the gesture and sends incremental
 * screen-space deltas through the `win:resize` IPC channel, where the main
 * process adjusts bounds (and shifts the origin for the left/top edges) so the
 * opposite edge stays anchored. Delta increments use screen coordinates, so the
 * drag tracks the pointer 1:1 even as the viewport size changes.
 */
export function useWindowResize(enabled = true): WindowResizeHandlers {
  const stateRef = useRef<ResizeState>({
    active: false,
    direction: null,
    lastX: 0,
    lastY: 0,
    startX: 0,
    startY: 0,
    moved: false,
  });

  const detectEdge = useCallback(
    (clientX: number, clientY: number): ResizeDirection | null => {
      // 桌宠模式禁用手势缩放：悬浮小窗不应被拖拽放大（拖大后没有自动恢复
      // 机制，用户会误以为窗口「变大了」）。窗口模式才允许边缘拖拽。
      if (!enabled) return null;
      const { innerWidth: w, innerHeight: h } = window;
      const left = clientX <= EDGE_PX;
      const right = clientX >= w - EDGE_PX;
      const top = clientY <= EDGE_PX;
      const bottom = clientY >= h - EDGE_PX;

      if (top && left) return 'nw';
      if (top && right) return 'ne';
      if (bottom && left) return 'sw';
      if (bottom && right) return 'se';
      if (top) return 'n';
      if (bottom) return 's';
      if (left) return 'w';
      if (right) return 'e';
      return null;
    },
    [enabled],
  );

  const applyCursor = useCallback((direction: ResizeDirection | null): void => {
    document.body.style.cursor = direction ? CURSOR_BY_DIRECTION[direction] : '';
  }, []);

  // Hover feedback: show the resize cursor when the pointer crosses an edge.
  useEffect(() => {
    const onMove = (e: PointerEvent): void => {
      if (stateRef.current.active) return;
      // Don't override the cursor on interactive controls sitting near an edge
      // (e.g. the overlay buttons in the bottom-right corner).
      const target = e.target;
      if (
        target instanceof Element &&
        target.closest(
          'button, input, textarea, select, a, [data-nodrag], [contenteditable]',
        )
      ) {
        applyCursor(null);
        return;
      }
      applyCursor(detectEdge(e.clientX, e.clientY));
    };
    document.addEventListener('pointermove', onMove);
    document.addEventListener('pointerleave', () => {
      if (!stateRef.current.active) applyCursor(null);
    });
    return () => {
      document.removeEventListener('pointermove', onMove);
      applyCursor(null);
    };
  }, [applyCursor, detectEdge]);

  const onPointerDown = useCallback(
    (e: React.PointerEvent): boolean => {
      if (e.button !== 0) return false;
      const direction = detectEdge(e.clientX, e.clientY);
      if (!direction) return false;

      const target = e.target as HTMLElement;
      if (
        target.closest(
          'button, input, textarea, select, a, [data-nodrag], [contenteditable]',
        )
      ) {
        return false;
      }

      const state = stateRef.current;
      state.active = true;
      state.direction = direction;
      state.lastX = e.screenX;
      state.lastY = e.screenY;
      state.startX = e.screenX;
      state.startY = e.screenY;
      state.moved = false;
      applyCursor(direction);
      try {
        (e.currentTarget as HTMLElement).setPointerCapture?.(e.pointerId);
      } catch {
        // capture is best-effort; screen-space deltas still work without it
      }
      return true;
    },
    [applyCursor, detectEdge],
  );

  const onPointerMove = useCallback((e: React.PointerEvent): boolean => {
    const state = stateRef.current;
    if (!state.active || !state.direction) return false;

    // 兜底：pointerup 丢失（指针逃逸 / 窗口移动中断捕获）时，检测到按键已
    // 松开立即终止缩放，避免窗口边缘持续跟随鼠标。返回 true 认领手势，
    // 防止该次 move 再转给窗口拖拽。
    if (e.buttons === 0) {
      state.active = false;
      state.direction = null;
      state.moved = false;
      return true;
    }

    // 忽略按下后的微小抖动：只有从边缘真正拖动才缩放。普通点击边缘（如想点画布/聊天）
    // 不会误触发缩放。
    if (!state.moved) {
      if (
        Math.hypot(e.screenX - state.startX, e.screenY - state.startY) <
        RESIZE_THRESHOLD_PX
      ) {
        state.lastX = e.screenX;
        state.lastY = e.screenY;
        return true; // 认领手势（阻止窗口拖动），但不发送缩放
      }
      state.moved = true;
    }

    const dx = e.screenX - state.lastX;
    const dy = e.screenY - state.lastY;
    state.lastX = e.screenX;
    state.lastY = e.screenY;
    if (dx !== 0 || dy !== 0) {
      void window.moonlight?.resizeWindow(state.direction, dx, dy);
    }
    return true;
  }, []);

  const onPointerUp = useCallback((): void => {
    stateRef.current.active = false;
    stateRef.current.direction = null;
    stateRef.current.moved = false;
  }, []);

  return { onPointerDown, onPointerMove, onPointerUp };
}

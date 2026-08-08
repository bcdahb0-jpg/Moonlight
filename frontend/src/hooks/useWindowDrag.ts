import { useCallback, useRef } from 'react';

const DRAG_THRESHOLD_PX = 5;

interface DragState {
  active: boolean;
  moved: boolean;
  captured: boolean;
  startX: number;
  startY: number;
  lastX: number;
  lastY: number;
}

interface WindowDragHandlers {
  onPointerDown: (e: React.PointerEvent) => void;
  onPointerMove: (e: React.PointerEvent) => void;
  onPointerUp: () => void;
  isDragging: () => boolean;
}

/**
 * Drag-to-move for the frameless pet window.
 *
 * Uses screen-space pointer coordinates (screenX/screenY) and sends incremental
 * deltas through the `win:move-by` IPC channel. Because the deltas come from
 * absolute screen coordinates, the window tracks the cursor 1:1 without the
 * jitter that window-relative coordinates would cause.
 *
 * A small movement threshold means a plain click still reaches the Live2D
 * canvas (tap interaction) instead of being swallowed by the drag.
 */
export function useWindowDrag(): WindowDragHandlers {
  const dragRef = useRef<DragState>({
    active: false,
    moved: false,
    captured: false,
    startX: 0,
    startY: 0,
    lastX: 0,
    lastY: 0,
  });
  const movedRef = useRef(false);

  const onPointerDown = useCallback((e: React.PointerEvent): void => {
    if (e.button !== 0) return;
    // Never start a drag from an interactive control (buttons, inputs, links…).
    const target = e.target as HTMLElement;
    if (
      target.closest(
        'button, input, textarea, select, a, [data-nodrag], [contenteditable]',
      )
    ) {
      return;
    }
    const d = dragRef.current;
    d.active = true;
    d.moved = false;
    d.captured = false;
    d.startX = e.screenX;
    d.startY = e.screenY;
    d.lastX = e.screenX;
    d.lastY = e.screenY;
    movedRef.current = false;
    // 注意：此处【不】立即 setPointerCapture —— pointerdown 时就捕获会把后续
    // click 事件的派发目标锁定到 .app（Pointer Capture 生效期间 click 派发给
    // 捕获元素），导致所有 div.onClick（角色卡、Live2D 交互等）静默失效。
    // 改为在真正越过拖拽阈值、确认是拖拽时（onPointerMove 内）再捕获。
  }, []);

  const onPointerMove = useCallback((e: React.PointerEvent): void => {
    const d = dragRef.current;
    if (!d.active) return;
    // 兜底：若 pointerup 因指针逃逸 / 捕获被平台取消而丢失（无边框透明窗口在
    // Windows 上移动时容易发生），下一个 move 事件会发现按键已松开
    // （buttons === 0），此时立即终止拖拽，防止窗口持续「吸附」鼠标。
    if (e.buttons === 0) {
      d.active = false;
      d.captured = false;
      return;
    }
    // Ignore sub-threshold jitter so clicks are not misread as drags.
    if (!d.moved && Math.hypot(e.screenX - d.startX, e.screenY - d.startY) < DRAG_THRESHOLD_PX) {
      return;
    }
    // 确认是拖拽（越过阈值）后才捕获指针：窗口移动/指针逃逸时仍能收到
    // pointermove/pointerup，防「active 状态泄漏」；且普通点击不触发 capture，
    // click 事件正常派发到目标元素（div.onClick 不再被吞）。
    if (!d.captured) {
      d.captured = true;
      try {
        (e.currentTarget as HTMLElement).setPointerCapture?.(e.pointerId);
      } catch {
        // capture 是尽力而为；screen-space delta 在没有捕获时也能工作
      }
    }
    const dx = e.screenX - d.lastX;
    const dy = e.screenY - d.lastY;
    if (dx === 0 && dy === 0) return;
    d.lastX = e.screenX;
    d.lastY = e.screenY;
    d.moved = true;
    movedRef.current = true;
    void window.moonlight?.moveBy(dx, dy);
  }, []);

  const onPointerUp = useCallback((): void => {
    dragRef.current.active = false;
    dragRef.current.captured = false;
    // movedRef stays set for the remainder of the click cycle so callers can
    // decide whether to suppress a post-drag tap.
  }, []);

  const isDragging = useCallback((): boolean => movedRef.current, []);

  return { onPointerDown, onPointerMove, onPointerUp, isDragging };
}

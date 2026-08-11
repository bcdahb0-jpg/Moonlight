/**
 * taskDisplay — 任务展示层工具（UX 改造 M1 · 标题兜底契约）。
 *
 * 「未命名任务」是不可识别的占位（历史遗留 + 异常路径），展示层统一映射为
 * 带时间的「我的任务 · MM-DD HH:mm」，让用户能认领历史任务。只读映射，不改库。
 */
import type { Task } from './types';

const FALLBACK_TITLES = new Set(['未命名任务', '']);

export function displayTaskTitle(task: Pick<Task, 'title' | 'goal' | 'created_at'>): string {
  const raw = (task.title ?? '').trim();
  if (raw && !FALLBACK_TITLES.has(raw)) return raw;

  const goal = (task.goal ?? '').trim();
  if (goal) return goal.length > 30 ? `${goal.slice(0, 30)}…` : goal;

  const ts = Date.parse(task.created_at ?? '');
  if (Number.isFinite(ts) && ts > 0) {
    const d = new Date(ts);
    const mm = String(d.getMonth() + 1).padStart(2, '0');
    const dd = String(d.getDate()).padStart(2, '0');
    const hh = String(d.getHours()).padStart(2, '0');
    const mi = String(d.getMinutes()).padStart(2, '0');
    return `我的任务 · ${mm}-${dd} ${hh}:${mi}`;
  }
  return '我的任务';
}

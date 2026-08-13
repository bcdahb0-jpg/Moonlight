/**
 * 「用户询问时识别」的按需采集（Phase 5 + 修复 2026-08-11）。
 *
 * 任何采集模式下，用户消息命中屏幕关键词（与后端 _SCREEN_LOOK_KEYWORDS
 * 一致）时：先按需采集一帧并等待后端分析出新快照（resolve=true），
 * 再让调用方发送消息 —— 保证 LLM 回合开始时屏幕摘要有效（摘要 TTL 45s
 * 过期 / 静态画面去重后依然能拿到新鲜上下文）。
 */

import { screenActions } from '@/screen/screenActions';

const SCREEN_LOOK_KEYWORDS = [
  '屏幕',
  '看下屏幕',
  '看看屏幕',
  '看屏幕',
  '我的屏幕',
  '我屏幕',
  '屏幕上',
  '正在看',
  '这个页面',
  '这个窗口',
  '这里',
  '这报错',
  '这个报错',
  '报错',
  '看一下',
  '帮我看看',
  '你看',
];

/** 命中屏幕关键词时触发一次按需采集（幂等：一次消息最多触发一次）。
 *  resolve=true 表示命中关键词且已采集到新快照；未命中立即 resolve(false)。 */
export async function maybeCaptureOnDemand(text: string): Promise<boolean> {
  const t = (text || '').trim().toLowerCase();
  if (!t) return false;
  if (SCREEN_LOOK_KEYWORDS.some((k) => t.includes(k))) {
    return screenActions.captureOnce();
  }
  return false;
}

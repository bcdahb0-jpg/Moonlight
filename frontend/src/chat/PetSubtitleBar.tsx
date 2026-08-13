/**
 * PetSubtitleBar — 桌宠字幕条（Phase 3 pet-ptt-workflow）。
 *
 * 数据源：state.petSubtitle（最近一条流式 AI full-text，messageHandlers 维护），
 * 与 state.subtitle（audio 翻译副文本）分离，不混用。
 *
 * 生命周期：文本到达 → 显示（shown）；无文本 → 隐藏；最后一段在
 * SUBTITLE_FADE_DELAY_MS 后淡出（fading → hidden）。会话结束/打断/切换
 * history/错误时后端已清空 petSubtitle，组件直接隐藏。
 */
import { useEffect, useRef, useState, type ReactElement } from 'react';

/** 字幕停留时长（最后一段文本淡出前的显示时间）。 */
export const SUBTITLE_FADE_DELAY_MS = 8000;
/** 淡出过渡时长。 */
const SUBTITLE_FADE_MS = 500;

type Phase = 'hidden' | 'shown' | 'fading';

export function PetSubtitleBar({ text }: { text: string }): ReactElement | null {
  const [phase, setPhase] = useState<Phase>('hidden');
  const timerRef = useRef<number | null>(null);

  // 文本变化：显示并重启淡出计时。
  useEffect(() => {
    if (timerRef.current !== null) window.clearTimeout(timerRef.current);
    if (!text) {
      setPhase('hidden');
      return;
    }
    setPhase('shown');
    timerRef.current = window.setTimeout(() => setPhase('fading'), SUBTITLE_FADE_DELAY_MS);
  }, [text]);

  // fading → hidden（淡出动画完成后卸载）。
  useEffect(() => {
    if (phase !== 'fading') return;
    const t = window.setTimeout(() => setPhase('hidden'), SUBTITLE_FADE_MS);
    return () => window.clearTimeout(t);
  }, [phase]);

  // 卸载清理。
  useEffect(() => {
    return () => {
      if (timerRef.current !== null) window.clearTimeout(timerRef.current);
    };
  }, []);

  if (phase === 'hidden') return null;
  return (
    <div className={`pet-subtitle-bar ${phase}`} role="status" aria-live="polite">
      {text}
    </div>
  );
}

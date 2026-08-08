import type { ReactElement } from 'react';
import type { Emotion } from '@/state/types';

const EMOTION_LABELS: Record<Emotion, string> = {
  neutral: '平静',
  joy: '开心',
  amusement: '好笑',
  affection: '亲昵',
  surprise: '惊讶',
  confusion: '困惑',
  sad: '难过',
  anger: '生气',
  fear: '害怕',
  gratitude: '感谢',
  admiration: '敬佩',
  annoyance: '烦躁',
  approval: '赞同',
  caring: '关心',
  curiosity: '好奇',
  desire: '渴望',
  disappointment: '失望',
  disapproval: '不满',
  disgust: '嫌弃',
  embarrassment: '害羞',
  excitement: '兴奋',
  grief: '悲伤',
  love: '深爱',
  nervousness: '紧张',
  optimism: '乐观',
  pride: '自豪',
  realization: '恍然',
  relief: '安心',
  remorse: '懊悔',
  angry: '生气',
  smirk: '得意',
};

export interface EmotionBadgeProps {
  emotion: Emotion;
}

export function EmotionBadge({ emotion }: EmotionBadgeProps): ReactElement {
  return <span className={`emotion-badge ${emotion}`}>{EMOTION_LABELS[emotion] ?? emotion}</span>;
}

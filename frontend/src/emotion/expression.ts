import type { Emotion } from '@/state/types';
import type { ModelInfo } from '@/types/ws';

const EMOTION_KEYS = new Set<Emotion>([
  'neutral',
  'joy',
  'amusement',
  'affection',
  'surprise',
  'confusion',
  'sad',
  'anger',
  'fear',
  'gratitude',
  'admiration',
  'annoyance',
  'approval',
  'caring',
  'curiosity',
  'desire',
  'disappointment',
  'disapproval',
  'disgust',
  'embarrassment',
  'excitement',
  'grief',
  'love',
  'nervousness',
  'optimism',
  'pride',
  'realization',
  'relief',
  'remorse',
  'angry',
  'smirk',
]);

/**
 * Convert a backend expression token (string emotion name or numeric index into
 * the model's expression list) into a displayable Emotion for the badge.
 */
export function expressionToEmotion(
  expression: string | number | undefined | null,
  modelInfo: ModelInfo | null,
): Emotion {
  if (expression === undefined || expression === null) return 'neutral';
  const emotionMap = modelInfo?.emotionMap ?? {};

  if (typeof expression === 'string') {
    if (EMOTION_KEYS.has(expression as Emotion)) return expression as Emotion;
    const idx = emotionMap[expression];
    if (idx !== undefined) return emotionAtIndex(idx, emotionMap);
    return 'neutral';
  }

  return emotionAtIndex(expression, emotionMap);
}

function emotionAtIndex(index: number, emotionMap: Record<string, number>): Emotion {
  for (const [key, value] of Object.entries(emotionMap)) {
    if (value === index && EMOTION_KEYS.has(key as Emotion)) return key as Emotion;
  }
  return 'neutral';
}

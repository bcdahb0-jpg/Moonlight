import { getVADPreset, type EmotionIntent } from '@soullink-emotion/engine';

/**
 * Maps backend emotion tokens to Soullink Emotion SDK archetype names and
 * default intensities.
 *
 * 后端情绪集来自 `emotion_analyzer.py` 的 EMOTIONS（31 个）+ model_dict.json
 * 的 emotionMap 词汇（含 sad/smirk/sadness 等）。SDK 引擎只认识自己的 15 个
 * 情绪原型（neutral/happy/calm/excited/shy/affectionate/curious/concerned/
 * tired/sad/anxiety/confused/surprised/anger/angry），因此做语义映射。
 *
 * intensity 为该 token 的默认反应强度（0..1），无显式置信度时使用。
 */
interface SdkEmotionSpec {
  emotion: string;
  intensity: number;
}

export const BACKEND_TO_SDK_EMOTION: Record<string, SdkEmotionSpec> = {
  // 中性
  neutral: { emotion: 'neutral', intensity: 0.5 },

  // 快乐 / 积极
  joy: { emotion: 'happy', intensity: 0.7 },
  amusement: { emotion: 'happy', intensity: 0.6 },
  excitement: { emotion: 'excited', intensity: 0.85 },
  optimism: { emotion: 'happy', intensity: 0.6 },
  pride: { emotion: 'happy', intensity: 0.6 },
  gratitude: { emotion: 'happy', intensity: 0.55 },
  approval: { emotion: 'calm', intensity: 0.45 },
  admiration: { emotion: 'curious', intensity: 0.55 },
  relief: { emotion: 'calm', intensity: 0.5 },
  smirk: { emotion: 'happy', intensity: 0.4 },

  // 亲密 / 关爱
  affection: { emotion: 'affectionate', intensity: 0.7 },
  love: { emotion: 'affectionate', intensity: 0.85 },
  caring: { emotion: 'affectionate', intensity: 0.6 },

  // 好奇 / 惊讶
  curiosity: { emotion: 'curious', intensity: 0.6 },
  desire: { emotion: 'curious', intensity: 0.6 },
  surprise: { emotion: 'surprised', intensity: 0.75 },
  realization: { emotion: 'surprised', intensity: 0.5 },

  // 困惑
  confusion: { emotion: 'confused', intensity: 0.6 },

  // 悲伤
  sad: { emotion: 'sad', intensity: 0.65 },
  sadness: { emotion: 'sad', intensity: 0.65 },
  grief: { emotion: 'sad', intensity: 0.85 },
  disappointment: { emotion: 'sad', intensity: 0.5 },
  remorse: { emotion: 'sad', intensity: 0.55 },
  embarrassment: { emotion: 'shy', intensity: 0.6 },

  // 恐惧 / 焦虑
  fear: { emotion: 'anxiety', intensity: 0.65 },
  nervousness: { emotion: 'anxiety', intensity: 0.5 },

  // 愤怒 / 不满
  anger: { emotion: 'anger', intensity: 0.75 },
  angry: { emotion: 'anger', intensity: 0.75 },
  annoyance: { emotion: 'anger', intensity: 0.4 },
  disapproval: { emotion: 'anger', intensity: 0.45 },
  disgust: { emotion: 'anger', intensity: 0.5 },
};

/**
 * Convert a backend expression token (string emotion name or numeric index into
 * the model's expression list) into an SDK EmotionIntent. Returns null when the
 * token cannot be resolved to a known emotion.
 *
 * @param token      后端 `msg.actions.expressions[0]` 的值
 * @param emotionMap 当前模型的 emotionMap（数字索引 → 情绪名反向查找用）
 * @param confidence 可选置信度 0..1，优先作为 intensity
 */
export function toEmotionIntent(
  token: string | number | undefined | null,
  emotionMap?: Record<string, number> | null,
  confidence?: number,
): EmotionIntent | null {
  const emotionName = resolveBackendEmotion(token, emotionMap);
  if (!emotionName) return null;

  const spec = BACKEND_TO_SDK_EMOTION[emotionName];
  if (!spec) {
    // 未知 token 但映射到 neutral，保证引擎总能消化
    return makeIntent('neutral', 0.5, 'neutral');
  }

  const intensity = confidence !== undefined
    ? clamp01(confidence)
    : spec.intensity;
  return makeIntent(spec.emotion, intensity, emotionName);
}

/** 构造一个 neutral 意图（用于 revertExpression / 情绪复位）。 */
export function neutralIntent(): EmotionIntent {
  return makeIntent('neutral', 0.5, 'neutral');
}

function makeIntent(
  emotion: string,
  intensity: number,
  sourceEmotion: string,
): EmotionIntent {
  const vad = getVADPreset(emotion);
  return {
    emotion,
    // 表情可见度保底：低于 0.55 的弱强度会被拉高，保证每次情绪触发
    // 都有肉眼可见的表情（引擎的 FACS 是渐变式，弱强度下几乎不可察觉）
    intensity: clamp01(Math.max(0.55, intensity)),
    naturalVAD: vad,
    contextTags: [`backend:${sourceEmotion}`],
  };
}

/** 数字索引 → emotionMap 反向 → 情绪名；字符串直接返回。 */
function resolveBackendEmotion(
  token: string | number | undefined | null,
  emotionMap?: Record<string, number> | null,
): string | null {
  if (token === undefined || token === null || token === '') return null;

  if (typeof token === 'string') {
    const lower = token.toLowerCase();
    // 直接命中后端情绪集 / 模型 emotionMap 词汇
    if (BACKEND_TO_SDK_EMOTION[lower] || lower === 'neutral') return lower;
    return null;
  }

  if (typeof token === 'number') {
    if (!emotionMap) return null;
    for (const [key, idx] of Object.entries(emotionMap)) {
      if (idx === token) return key.toLowerCase();
    }
    return null;
  }

  return null;
}

function clamp01(value: number): number {
  return Math.max(0, Math.min(1, value));
}

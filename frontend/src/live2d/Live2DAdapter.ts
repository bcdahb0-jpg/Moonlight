/**
 * Live2D 适配器公共接口。
 *
 * `Live2DModelAdapter`（切换式 expression）与 `SoullinkAdapter`（SDK 逐帧引擎）
 * 都实现此接口，上层（App.tsx / Live2DCanvas）无需关心具体实现，可安全地在
 * feature flag 下互相替换。
 */
/**
 * 音素级口型向量：[a, i, u, e, o] 概率（后端 F1/F2 共振峰分析产出，
 * 见 backend/.../utils/viseme.py）。概率和为 1；缺省为 null 表示回退 RMS 口型。
 */
export type VisemeVector = [number, number, number, number, number];

export interface Live2DAdapter {
  load(): Promise<void>;

  /**
   * 设置情绪：后端 emotion token（字符串或数字索引）或 expression 名。
   * @param confidence 可选强度 0..1（Phase 1 emotion_meta.intensity），
   *   Soullink 实现透传给引擎意图；切换式实现忽略。
   */
  setExpression(input: string | number | undefined | null, confidence?: number | null): void;

  /** 复位到 neutral。 */
  revertExpression(): void;

  playMotion(group: string, index?: number): void;
  playTapMotion(hitArea: string): void;
  stopMotion(): void;

  /**
   * 从模型现有动作组随机抽一个动作播放（Phase B：说话/idle 随机动作）。
   * 多组名兼容（Talk/talk/tap_body...），任何模型都能找到可用动作。
   * @returns 是否成功播放（模型无动作组时返回 false，调用方回退固定组）。
   */
  playRandomMotion(mode: 'idle' | 'speaking'): boolean;

  /**
   * 口型同步：0..1 的实时音量。
   * @param value 平滑后的音量（0..1）
   * @param viseme 可选音素级口型向量；实现可以忽略（如 SoullinkAdapter 用
   *   value 驱动引擎口型）。仅在 value > 0 时由驱动层传入。
   */
  setLipSync(value: number, viseme?: VisemeVector | null): void;

  /** 捕获当前渲染帧为 PNG data URL。 */
  capturePng(): string;

  setFocus(x: number, y: number): void;
  hitTest(x: number, y: number): string[];
  resize(width: number, height: number): void;
  destroy(): void;
}

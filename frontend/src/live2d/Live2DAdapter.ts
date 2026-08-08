/**
 * Live2D 适配器公共接口。
 *
 * `Live2DModelAdapter`（切换式 expression）与 `SoullinkAdapter`（SDK 逐帧引擎）
 * 都实现此接口，上层（App.tsx / Live2DCanvas）无需关心具体实现，可安全地在
 * feature flag 下互相替换。
 */
export interface Live2DAdapter {
  load(): Promise<void>;

  /** 设置情绪：后端 emotion token（字符串或数字索引）或 expression 名。 */
  setExpression(input: string | number | undefined | null): void;

  /** 复位到 neutral。 */
  revertExpression(): void;

  playMotion(group: string, index?: number): void;
  playTapMotion(hitArea: string): void;
  stopMotion(): void;

  /** 口型同步：0..1 的实时音量。 */
  setLipSync(value: number): void;

  /** 捕获当前渲染帧为 PNG data URL。 */
  capturePng(): string;

  setFocus(x: number, y: number): void;
  hitTest(x: number, y: number): string[];
  resize(width: number, height: number): void;
  destroy(): void;
}

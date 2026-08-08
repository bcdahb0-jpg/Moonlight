import { Live2DModelAdapter } from './Live2DModelAdapter';

/**
 * 在离屏画布渲染一个 Live2D 模型并捕获为 PNG data URL。
 *
 * 用于给模型生成「完整立绘」缩略图（替代贴图集的拆分配件图）。渲染 idle 动作数帧
 * 后调用 adapter.capturePng() 截图，最后销毁并清理 DOM。
 */
export async function renderModelToPng(modelUrl: string, size = 512): Promise<string> {
  const container = document.createElement('div');
  container.style.cssText =
    `position:fixed;left:-9999px;top:0;width:${size}px;height:${size}px;`;
  const canvas = document.createElement('canvas');
  canvas.width = size;
  canvas.height = size;
  container.appendChild(canvas);
  document.body.appendChild(container);

  const adapter = new Live2DModelAdapter({ canvas, modelUrl });
  try {
    await adapter.load();
    try {
      adapter.playMotion('Idle', 0);
    } catch {
      // some models lack an Idle group; skip
    }
    // 等待模型完成初始布局 + 若干渲染帧
    await new Promise((resolve) => setTimeout(resolve, 1500));
    return adapter.capturePng();
  } finally {
    adapter.destroy();
    if (container.parentElement) container.parentElement.removeChild(container);
  }
}

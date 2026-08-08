/**
 * Ensures the Live2D Cubism 4 runtime (`window.Live2DCubismCore`) is loaded
 * before pixi-live2d-display is imported. Tries the bundled vendor copy first,
 * then the official Live2D CDN as a fallback so the app still works offline of
 * the vendor asset.
 */
// Relative path: resolves to /vendor/... under the Vite dev server and to
// <dist>/vendor/... when the app is served from file:// in production.
const LOCAL_CORE_URL = './vendor/live2dcubismcore.min.js';
const CDN_CORE_URL =
  'https://cubism.live2d.com/sdk-web/cubismcore/live2dcubismcore.min.js';

export async function ensureCubismCore(): Promise<void> {
  if (typeof window !== 'undefined' && window.Live2DCubismCore) return;

  try {
    await loadScript(LOCAL_CORE_URL);
  } catch {
    await loadScript(CDN_CORE_URL);
  }

  if (!window.Live2DCubismCore) {
    throw new Error('无法加载 Live2D Cubism Core runtime');
  }
}

function loadScript(src: string): Promise<void> {
  return new Promise((resolve, reject) => {
    const script = document.createElement('script');
    script.src = src;
    script.async = true;
    script.onload = () => resolve();
    script.onerror = () => reject(new Error(`加载脚本失败: ${src}`));
    document.head.appendChild(script);
  });
}

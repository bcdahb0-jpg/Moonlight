/**
 * 画面变化检测（Phase 2）：8x8 DCT 低频系数 pHash + 汉明距离归一化。
 *
 * 静态画面（pHash diff < 阈值）复用上次摘要，不调用视觉模型（省 token）。
 * 注意：本模块只在渲染进程运行（依赖 canvas），Electron 主进程截图返回
 * data URL 后在这里算 hash；窗口身份去重由调用方（CaptureScheduler）负责。
 */

const DCT_N = 8;
const SAMPLE = 16;

function loadImage(dataUrl: string): Promise<HTMLImageElement> {
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.onload = () => resolve(img);
    img.onerror = () => reject(new Error('image decode failed'));
    img.src = dataUrl;
  });
}

/** 计算 63-bit pHash（DCT 低频系数 vs 均值）。失败返回 ''。 */
export async function imageHash(dataUrl: string): Promise<string> {
  try {
    const img = await loadImage(dataUrl);
    const canvas = document.createElement('canvas');
    canvas.width = SAMPLE;
    canvas.height = SAMPLE;
    const ctx = canvas.getContext('2d', { willReadFrequently: true });
    if (!ctx) return '';
    ctx.drawImage(img, 0, 0, SAMPLE, SAMPLE);
    const { data } = ctx.getImageData(0, 0, SAMPLE, SAMPLE);
    const gray = new Float32Array(SAMPLE * SAMPLE);
    for (let i = 0; i < SAMPLE * SAMPLE; i += 1) {
      gray[i] =
        0.299 * data[i * 4] + 0.587 * data[i * 4 + 1] + 0.114 * data[i * 4 + 2];
    }
    // 2D DCT（只算左上 8x8 低频块）。
    const coefs: number[] = [];
    for (let u = 0; u < DCT_N; u += 1) {
      for (let v = 0; v < DCT_N; v += 1) {
        let sum = 0;
        for (let x = 0; x < DCT_N; x += 1) {
          for (let y = 0; y < DCT_N; y += 1) {
            sum +=
              gray[y * SAMPLE + x] *
              Math.cos(((2 * x + 1) * u * Math.PI) / (2 * DCT_N)) *
              Math.cos(((2 * y + 1) * v * Math.PI) / (2 * DCT_N));
          }
        }
        const cu = u === 0 ? 1 / Math.sqrt(2) : 1;
        const cv = v === 0 ? 1 / Math.sqrt(2) : 1;
        coefs.push(0.25 * cu * cv * sum);
      }
    }
    // 去掉 DC 分量（coefs[0]），用其余 63 个系数均值做阈值。
    const ac = coefs.slice(1);
    const mean = ac.reduce((a, b) => a + b, 0) / ac.length;
    let hash = '';
    for (const c of ac) hash += c >= mean ? '1' : '0';
    return hash;
  } catch {
    return '';
  }
}

/** 汉明距离归一化 (0-1)。0=完全一致，1=完全不同。 */
export function hashDiff(a: string, b: string): number {
  if (!a || !b) return 1;
  const len = Math.max(a.length, b.length);
  let d = 0;
  for (let i = 0; i < len; i += 1) {
    if (a[i] !== b[i]) d += 1;
  }
  return len ? d / len : 0;
}

import { useEffect, useRef, useState, type ReactElement } from 'react';
import { occlusionApi } from '@/api/rest';
import { SettingsGroup, SettingsRow, SettingsStatusBadge } from '@/settings/SettingsConsole';

/**
 * 遮罩与光照（P1.5）。
 *
 * 数据契约：localStorage `moonlight.live2d.fx`（JSON）——
 * { enabled, occlusion: [{x,y}..]|null, brightness, colorTemp, saturation }
 * 渲染层（Live2DCanvas → SoullinkAdapter.applyVisualFx / setOcclusion）读取应用，
 * 控制台窗口修改 → storage 事件 → 桌宠窗口实时同步。
 *
 * 参考：reference/SoulLink_Live2D/frontend-vue/public/legacy/js/live2d/
 *   occlusion-mask.js（多边形节点编辑）+ ambient-lighting.js（亮度/色温滤镜）。
 * 自动估边 / AI 前景提取需桌面截屏源，标注 P1.6。
 */

export interface OcclusionPoint {
  x: number; // 0..1 归一化画布坐标
  y: number;
}

export interface Live2DVisualFx {
  enabled: boolean;
  occlusion: OcclusionPoint[] | null;
  brightness: number; // 0.2-2.0
  colorTemp: number; // 2000-10000 K
  saturation: number; // 0-2
}

const FX_KEY = 'moonlight.live2d.fx';

const DEFAULTS: Live2DVisualFx = {
  enabled: false,
  occlusion: null,
  brightness: 1,
  colorTemp: 6500,
  saturation: 1,
};

function loadFx(): Live2DVisualFx {
  try {
    const raw = localStorage.getItem(FX_KEY);
    return raw ? { ...DEFAULTS, ...(JSON.parse(raw) as Partial<Live2DVisualFx>) } : DEFAULTS;
  } catch {
    return DEFAULTS;
  }
}

/** 示例遮罩：下半屏遮挡（模拟被桌面任务栏/窗口遮挡）。 */
function sampleOcclusion(): OcclusionPoint[] {
  return [
    { x: 0.0, y: 0.62 },
    { x: 1.0, y: 0.62 },
    { x: 1.0, y: 1.0 },
    { x: 0.0, y: 1.0 },
  ];
}

export function OcclusionEditor(): ReactElement {
  const [fx, setFx] = useState<Live2DVisualFx>(DEFAULTS);
  const [notice, setNotice] = useState('');
  const svgRef = useRef<SVGSVGElement | null>(null);
  const dragIdx = useRef<number | null>(null);
  // P6：AI 前景提取（选图 → 后端 rembg/Pillow → 轮廓点）
  const aiFileRef = useRef<HTMLInputElement | null>(null);
  const [extracting, setExtracting] = useState(false);

  useEffect(() => {
    setFx(loadFx());
  }, []);

  const update = (patch: Partial<Live2DVisualFx>): void => {
    setFx((prev) => {
      const next = { ...prev, ...patch };
      try {
        localStorage.setItem(FX_KEY, JSON.stringify(next));
      } catch {
        /* storage unavailable */
      }
      return next;
    });
    setNotice('已保存（桌宠窗口实时同步）');
  };

  /** SVG 点击坐标 → 归一化 0..1（容差 ±2%）。 */
  const toNorm = (e: React.PointerEvent): { x: number; y: number } | null => {
    const svg = svgRef.current;
    if (!svg) return null;
    const rect = svg.getBoundingClientRect();
    if (rect.width <= 0 || rect.height <= 0) return null;
    return {
      x: Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width)),
      y: Math.max(0, Math.min(1, (e.clientY - rect.top) / rect.height)),
    };
  };

  const onSvgClick = (e: React.PointerEvent): void => {
    const p = toNorm(e);
    if (!p) return;
    const pts = fx.occlusion ?? [];
    update({ occlusion: [...pts, p] });
  };

  const onPointerDown = (e: React.PointerEvent, idx: number): void => {
    dragIdx.current = idx;
    (e.currentTarget as Element).setPointerCapture(e.pointerId);
  };

  const onPointerMove = (e: React.PointerEvent, idx: number): void => {
    if (dragIdx.current !== idx) return;
    const p = toNorm(e);
    if (!p || !fx.occlusion) return;
    const pts = fx.occlusion.map((pt, i) => (i === idx ? p : pt));
    update({ occlusion: pts });
  };

  const onPointerUp = (): void => {
    dragIdx.current = null;
  };

  const removePoint = (idx: number): void => {
    if (!fx.occlusion) return;
    update({ occlusion: fx.occlusion.filter((_, i) => i !== idx) });
  };

  const enabled = fx.enabled;
  const points = fx.occlusion ?? [];

  return (
    <SettingsGroup title="遮罩与光照" description="多边形裁剪（前景遮挡模拟）+ 环境光照滤镜，桌宠窗口实时生效">
      <SettingsRow label="遮罩系统" description="模型仅在多边形内可见（模拟被前景遮挡）">
        <label className="toggle-row">
          <input
            type="checkbox"
            checked={enabled}
            onChange={(e) => update({ enabled: e.target.checked })}
          />
          <span>{enabled ? '开' : '关'}</span>
        </label>
      </SettingsRow>

      <SettingsRow label="遮罩编辑器" description="点击画布加节点 · 拖动节点调整 · 点击右侧 ✕ 删除">
        <div className="console-stack">
          <svg
            ref={svgRef}
            viewBox="0 0 100 100"
            preserveAspectRatio="none"
            className="occlusion-svg"
            onPointerUp={onPointerUp}
            onPointerLeave={onPointerUp}
          >
            {/* 遮罩可见区域 */}
            {enabled && points.length >= 3 && (
              <polygon
                points={points.map((p) => `${p.x * 100},${p.y * 100}`).join(' ')}
                fill="rgba(110,231,168,0.18)"
                stroke="#6ee7a8"
                strokeWidth="0.8"
                vectorEffect="non-scaling-stroke"
              />
            )}
            {/* 节点 */}
            {points.map((p, i) => (
              <g
                key={i}
                onPointerDown={(e) => onPointerDown(e, i)}
                onPointerMove={(e) => onPointerMove(e, i)}
                style={{ cursor: 'grab' }}
              >
                <circle cx={p.x * 100} cy={p.y * 100} r={2.2} fill="#6ee7a8" />
                <text
                  x={p.x * 100 + 3}
                  y={p.y * 100 - 2}
                  fontSize={4}
                  fill="rgba(236,231,251,0.7)"
                  style={{ userSelect: 'none' }}
                >
                  {i}
                </text>
              </g>
            ))}
            {/* 整个画布可点击加节点 */}
            <rect
              x={0}
              y={0}
              width={100}
              height={100}
              fill="transparent"
              onPointerDown={(e) => onSvgClick(e)}
            />
          </svg>
          <div className="console-btn-row">
            <button type="button" className="console-btn" onClick={() => update({ occlusion: sampleOcclusion() })}>
              示例遮罩（下半屏）
            </button>
            <button
              type="button"
              className="console-btn"
              onClick={() => aiFileRef.current?.click()}
              disabled={extracting}
              title="选图 → 后端前景提取（rembg / Pillow 兜底）→ 轮廓填入编辑器"
            >
              {extracting ? 'AI 提取中…' : '🪄 AI 提取前景'}
            </button>
            <input
              ref={aiFileRef}
              type="file"
              accept="image/*"
              style={{ display: 'none' }}
              onChange={(e) => {
                const f = e.target.files?.[0];
                if (f) {
                  setExtracting(true);
                  void occlusionApi
                    .extract(f)
                    .then((res) => {
                      if (res.ok && res.points.length >= 3) {
                        const norm = res.points.map((p) => ({ x: p.x / 100, y: p.y / 100 }));
                        update({ occlusion: norm, enabled: true });
                        setNotice(`已填入 ${norm.length} 个轮廓点（${res.engine}）`);
                      } else {
                        setNotice('前景提取失败（背景过于复杂？）');
                      }
                    })
                    .catch((err: unknown) => setNotice(`提取失败: ${err instanceof Error ? err.message : String(err)}`))
                    .finally(() => setExtracting(false));
                }
                e.target.value = '';
              }}
            />
            <button type="button" className="console-btn" onClick={() => update({ occlusion: [] })}>
              清空节点
            </button>
            {points.length > 0 && (
              <button type="button" className="console-btn" onClick={() => removePoint(points.length - 1)}>
                删除最后一个
              </button>
            )}
          </div>
          {points.length > 0 && points.length < 3 && (
            <p className="console-row-description">至少 3 个节点才生效（当前 {points.length}）。</p>
          )}
        </div>
      </SettingsRow>

      <SettingsRow label="环境光照" description="亮度 / 色温 / 饱和度滤镜（ColorMatrixFilter）">
        <label className="toggle-row">
          <input
            type="checkbox"
            checked={enabled}
            onChange={(e) => update({ enabled: e.target.checked })}
          />
          <span>随遮罩开关</span>
        </label>
      </SettingsRow>

      <SettingsRow label="亮度补偿" description="1.0 = 不变">
        <div className="range-row">
          <input
            type="range"
            min={0.2}
            max={2}
            step={0.05}
            value={fx.brightness}
            onChange={(e) => update({ brightness: Number(e.target.value) })}
          />
          <span className="range-val">{fx.brightness.toFixed(2)}×</span>
        </div>
      </SettingsRow>

      <SettingsRow label="目标色温" description="6500K = 不变">
        <div className="range-row">
          <input
            type="range"
            min={2000}
            max={10000}
            step={100}
            value={fx.colorTemp}
            onChange={(e) => update({ colorTemp: Number(e.target.value) })}
          />
          <span className="range-val">{fx.colorTemp}K</span>
        </div>
      </SettingsRow>

      <SettingsRow label="饱和度" description="1.0 = 不变">
        <div className="range-row">
          <input
            type="range"
            min={0}
            max={2}
            step={0.05}
            value={fx.saturation}
            onChange={(e) => update({ saturation: Number(e.target.value) })}
          />
          <span className="range-val">{fx.saturation.toFixed(2)}</span>
        </div>
      </SettingsRow>

      <SettingsRow label="状态">
        <SettingsStatusBadge tone="ok">
          {notice || (enabled ? '已启用 · 桌宠窗口实时生效' : '已保存（未启用）')}
        </SettingsStatusBadge>
      </SettingsRow>
      <p className="console-row-description">
        AI 前景提取 / 背景自动采样（需桌面截屏源）列为 P1.6；当前为手动多边形 + 手动光照。
      </p>
    </SettingsGroup>
  );
}

export default OcclusionEditor;

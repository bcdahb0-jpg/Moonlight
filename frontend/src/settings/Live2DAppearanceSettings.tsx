import { useEffect, useState, type ReactElement } from 'react';
import { SettingsGroup, SettingsRow, SettingsStatusBadge } from '@/settings/SettingsConsole';

/**
 * Live2D 外观设置（P0 接线工程）。
 *
 * 数据契约：读写 localStorage `moonlight.live2d.appearance`（JSON）。
 * 桌宠模式的 Live2D 渲染层（P0.5）将读取同一 key 应用 scale/opacity/drag/click，
 * 当前版本先做到「设置可编辑 + 持久化到本机」。
 */
export interface Live2DAppearance {
  scale: number; // 40-200 %
  posX: number; // 0-100 %
  posY: number; // 0-100 %
  opacity: number; // 20-100 %
  drag: boolean;
  click: boolean;
}

const STORAGE_KEY = 'moonlight.live2d.appearance';

const DEFAULTS: Live2DAppearance = {
  scale: 100,
  posX: 50,
  posY: 60,
  opacity: 95,
  drag: true,
  click: true,
};

function loadSaved(): Live2DAppearance {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return DEFAULTS;
    const parsed = JSON.parse(raw) as Partial<Live2DAppearance>;
    return { ...DEFAULTS, ...parsed };
  } catch {
    return DEFAULTS;
  }
}

function SliderRow({
  label,
  description,
  value,
  min,
  max,
  step,
  suffix,
  onChange,
}: {
  label: string;
  description?: string;
  value: number;
  min: number;
  max: number;
  step: number;
  suffix: string;
  onChange: (v: number) => void;
}): ReactElement {
  return (
    <SettingsRow label={label} description={description}>
      <div className="range-row">
        <input
          type="range"
          min={min}
          max={max}
          step={step}
          value={value}
          onChange={(e) => onChange(Number(e.target.value))}
          aria-label={label}
        />
        <span className="range-val">
          {value}
          {suffix}
        </span>
      </div>
    </SettingsRow>
  );
}

export function Live2DAppearanceSettings(): ReactElement {
  const [appearance, setAppearance] = useState<Live2DAppearance>(DEFAULTS);
  const [savedAt, setSavedAt] = useState<number>(0);

  useEffect(() => {
    setAppearance(loadSaved());
  }, []);

  const update = (patch: Partial<Live2DAppearance>): void => {
    setAppearance((prev) => {
      const next = { ...prev, ...patch };
      try {
        localStorage.setItem(STORAGE_KEY, JSON.stringify(next));
      } catch {
        /* storage unavailable */
      }
      return next;
    });
    setSavedAt(Date.now());
  };

  return (
    <SettingsGroup title="Live2D 外观" description="模型在桌面上的呈现参数，保存于本机">
      <SliderRow
        label="模型缩放"
        value={appearance.scale}
        min={40}
        max={200}
        step={1}
        suffix="%"
        onChange={(v) => update({ scale: v })}
      />
      <SliderRow
        label="水平位置 X"
        value={appearance.posX}
        min={0}
        max={100}
        step={1}
        suffix="%"
        onChange={(v) => update({ posX: v })}
      />
      <SliderRow
        label="垂直位置 Y"
        value={appearance.posY}
        min={0}
        max={100}
        step={1}
        suffix="%"
        onChange={(v) => update({ posY: v })}
      />
      <SliderRow
        label="不透明度"
        value={appearance.opacity}
        min={20}
        max={100}
        step={1}
        suffix="%"
        onChange={(v) => update({ opacity: v })}
      />
      <SettingsRow label="桌面拖拽" description="允许用鼠标拖动桌宠位置">
        <label className="toggle-row">
          <input
            type="checkbox"
            checked={appearance.drag}
            onChange={(e) => update({ drag: e.target.checked })}
          />
          <span>{appearance.drag ? '开' : '关'}</span>
        </label>
      </SettingsRow>
      <SettingsRow label="点击互动" description="点击模型触发随机反应">
        <label className="toggle-row">
          <input
            type="checkbox"
            checked={appearance.click}
            onChange={(e) => update({ click: e.target.checked })}
          />
          <span>{appearance.click ? '开' : '关'}</span>
        </label>
      </SettingsRow>
      <SettingsRow label="保存状态">
        <SettingsStatusBadge tone="ok">
          {savedAt > 0
            ? `已保存 ${new Date(savedAt).toLocaleTimeString()}`
            : '默认值（未修改）'}
        </SettingsStatusBadge>
      </SettingsRow>
      <p className="console-row-description" style={{ marginTop: 4 }}>
        当前版本持久化到本机；桌宠渲染层应用（缩放/透明度/拖拽开关）随 P0.5 上线。
      </p>
    </SettingsGroup>
  );
}

export default Live2DAppearanceSettings;

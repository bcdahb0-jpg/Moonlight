import { useEffect, useState, type ReactElement } from 'react';
import { expressionApi, type ExpressionConfig, type ExpressionFrame } from '@/api/rest';
import { SettingsGroup, SettingsRow, SettingsStatusBadge } from '@/settings/SettingsConsole';

/**
 * 口型与连续动作（P1/P1.5）。
 *
 * 配置（灵敏度/幅度/平滑）读 /api/expression/config（与 AI 驱动表情共用）。
 * 「试听并预览动作」→ /api/expression/motion-plan 生成逐秒参数帧 →
 * 帧序列可视化（secondIndex + 动作 + 参数表）。
 * 渲染层播放器（MotionPlayer + useAppShell.onItemStart）已在 P1 落地，
 * 每次 TTS 播放自动应用本卡配置的幅度/平滑。
 */
export function MotionPreviewSettings(): ReactElement {
  const [cfg, setCfg] = useState<ExpressionConfig | null>(null);
  const [text, setText] = useState('哇，这也太厉害了吧！');
  const [frames, setFrames] = useState<ExpressionFrame[] | null>(null);
  const [source, setSource] = useState('');
  const [loading, setLoading] = useState(false);
  const [notice, setNotice] = useState('');

  useEffect(() => {
    void expressionApi
      .config()
      .then(setCfg)
      .catch(() => setNotice('无法连接后端'));
  }, []);

  const preview = async (): Promise<void> => {
    if (!text.trim()) return;
    setLoading(true);
    setFrames(null);
    try {
      const res = await expressionApi.motionPlan({ text, duration_sec: Math.max(2, Math.min(20, Math.ceil(text.length / 4))) });
      setFrames(res.frames);
      setSource(res.source === 'llm' ? 'LLM 生成' : '兜底（LLM 超时/不可用）');
    } catch {
      setNotice('生成失败（后端不可用？）');
    } finally {
      setLoading(false);
    }
  };

  return (
    <SettingsGroup title="口型与连续动作" description="说话时嘴型同步 + LLM 逐秒动作帧，播放器已接入 TTS 链路">
      <SettingsRow label="口型同步" description="TTS 播放期间嘴型跟随音频（音量 + 音素驱动）">
        <SettingsStatusBadge tone="ok">已内置（LipSyncDriver + 引擎口型）</SettingsStatusBadge>
      </SettingsRow>

      <SettingsRow label="动作幅度" description="帧参数缩放（后端 /api/expression/config）">
        <div className="range-row">
          <input
            type="range"
            min={0}
            max={100}
            value={cfg?.amplitude ?? 70}
            onChange={(e) => {
              void expressionApi.saveConfig({ amplitude: Number(e.target.value) }).then(setCfg);
            }}
          />
          <span className="range-val">{cfg?.amplitude ?? 70}%</span>
        </div>
      </SettingsRow>

      <SettingsRow label="平滑过渡" description="帧间 easeInOutCubic 渐变">
        <label className="toggle-row">
          <input
            type="checkbox"
            checked={cfg?.easing ?? true}
            onChange={(e) => {
              void expressionApi.saveConfig({ easing: e.target.checked }).then(setCfg);
            }}
          />
          <span>{cfg?.easing ? '开' : '关'}</span>
        </label>
      </SettingsRow>

      <SettingsRow label="动作预览" description="输入台词 → LLM 生成逐秒动作帧并可视化">
        <div className="console-stack">
          <input
            type="text"
            className="console-input"
            value={text}
            onChange={(e) => setText(e.target.value)}
            placeholder="输入台词…"
          />
          <button type="button" className="console-btn" disabled={loading} onClick={() => void preview()}>
            {loading ? '生成中…' : '试听并预览动作'}
          </button>
        </div>
      </SettingsRow>

      {frames && (
        <SettingsRow label="帧序列">
          <div className="console-stack" style={{ width: '100%' }}>
            <SettingsStatusBadge tone={source === 'LLM 生成' ? 'ok' : 'neutral'}>
              {frames.length} 帧 · {source}（MouthOpen 已过滤，口型交 TTS）
            </SettingsStatusBadge>
            <div className="motion-frame-list">
              {frames.map((f) => (
                <div className="motion-frame" key={f.secondIndex}>
                  <span className="motion-frame-sec">{f.secondIndex}s</span>
                  <span className="motion-frame-action">{f.action || '—'}</span>
                  <span className="motion-frame-params">
                    {Object.entries(f.parameters)
                      .slice(0, 3)
                      .map(([k, v]) => `${k}=${v}`)
                      .join(' · ') || '保持上一帧'}
                    {Object.keys(f.parameters).length > 3 && ` · +${Object.keys(f.parameters).length - 3}`}
                  </span>
                </div>
              ))}
            </div>
          </div>
        </SettingsRow>
      )}

      <SettingsRow label="状态">
        <SettingsStatusBadge tone="ok">{notice || '播放器已接入：TTS 播放自动应用动作帧'}</SettingsStatusBadge>
      </SettingsRow>
    </SettingsGroup>
  );
}

export default MotionPreviewSettings;

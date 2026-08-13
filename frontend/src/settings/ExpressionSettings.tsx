import { useEffect, useState, type ReactElement } from 'react';
import { expressionApi, type ExpressionConfig } from '@/api/rest';
import { SettingsGroup, SettingsRow, SettingsStatusBadge } from '@/settings/SettingsConsole';

/**
 * AI 驱动表情（P1）。
 * 开关/灵敏度/幅度/平滑 → conf system_config.expression；
 * 「试一下」→ /api/expression/generate 文本情绪分类（写情绪跟踪器，
 * 桌宠前端表情链路自动消费——与主对话同一套 emotion → setExpression 通道）。
 */
export function ExpressionSettings(): ReactElement {
  const [cfg, setCfg] = useState<ExpressionConfig | null>(null);
  const [testText, setTestText] = useState('哇，今天也太开心了吧！');
  const [testResult, setTestResult] = useState('');
  const [testing, setTesting] = useState(false);
  const [notice, setNotice] = useState('');

  useEffect(() => {
    void expressionApi
      .config()
      .then(setCfg)
      .catch(() => setNotice('无法连接后端'));
  }, []);

  const update = async (patch: Partial<ExpressionConfig>): Promise<void> => {
    try {
      const next = await expressionApi.saveConfig(patch);
      setCfg(next);
      setNotice('已保存（无需重启）');
    } catch {
      setNotice('保存失败');
    }
  };

  const runTest = async (): Promise<void> => {
    if (!testText.trim()) return;
    setTesting(true);
    try {
      const res = await expressionApi.generate(testText);
      setTestResult(
        `→ ${res.emotion}（强度 ${res.intensity} · 持续 ${Math.round(res.duration_ms / 1000)}s · ${res.source}）`,
      );
    } catch {
      setTestResult('分类失败（后端不可用？）');
    } finally {
      setTesting(false);
    }
  };

  if (!cfg) {
    return (
      <SettingsGroup title="AI 驱动表情" description="加载中…">
        <SettingsStatusBadge tone="neutral">配置读取中…</SettingsStatusBadge>
      </SettingsGroup>
    );
  }

  return (
    <SettingsGroup title="AI 驱动表情" description="LLM 理解对话情感 → 表情参数自动映射（Soullink 引擎逐帧过渡）">
      <SettingsRow label="AI 表情引擎" description="情感 → 表情参数自动映射">
        <label className="toggle-row">
          <input type="checkbox" checked={cfg.enabled} onChange={(e) => void update({ enabled: e.target.checked })} />
          <span>{cfg.enabled ? '开' : '关'}</span>
        </label>
      </SettingsRow>

      <SettingsRow label="参数平滑过渡" description="表情切换 easeInOutCubic 渐变">
        <label className="toggle-row">
          <input type="checkbox" checked={cfg.easing} onChange={(e) => void update({ easing: e.target.checked })} />
          <span>{cfg.easing ? '开' : '关'}</span>
        </label>
      </SettingsRow>

      <SettingsRow label="表情灵敏度" description="情绪触发的敏感程度">
        <div className="range-row">
          <input
            type="range"
            min={0}
            max={100}
            value={cfg.sensitivity}
            onChange={(e) => void update({ sensitivity: Number(e.target.value) })}
          />
          <span className="range-val">{cfg.sensitivity}%</span>
        </div>
      </SettingsRow>

      <SettingsRow label="动作幅度" description="表情/动作参数的缩放幅度">
        <div className="range-row">
          <input
            type="range"
            min={0}
            max={100}
            value={cfg.amplitude}
            onChange={(e) => void update({ amplitude: Number(e.target.value) })}
          />
          <span className="range-val">{cfg.amplitude}%</span>
        </div>
      </SettingsRow>

      <SettingsRow label="情绪集" description="引擎支持的核心情绪原型">
        <div className="console-tag-wrap">
          {['neutral', 'happy', 'excited', 'shy', 'sad', 'anger', 'surprised', 'confused'].map((e) => (
            <span className="console-tag" key={e}>
              {e}
            </span>
          ))}
        </div>
      </SettingsRow>

      <SettingsRow label="表情测试" description="输入一句话，看 LLM 判定什么情绪（写情绪跟踪器，桌宠表情实时跟随）">
        <div className="console-stack">
          <input
            type="text"
            className="console-input"
            value={testText}
            onChange={(e) => setTestText(e.target.value)}
            placeholder="输入台词…"
          />
          <button type="button" className="console-btn" disabled={testing} onClick={() => void runTest()}>
            {testing ? '分析中…' : '试一下'}
          </button>
          {testResult && <SettingsStatusBadge tone="neutral">{testResult}</SettingsStatusBadge>}
        </div>
      </SettingsRow>

      <SettingsRow label="状态">
        <SettingsStatusBadge tone="ok">{notice || '配置已连接'}</SettingsStatusBadge>
      </SettingsRow>
    </SettingsGroup>
  );
}

export default ExpressionSettings;

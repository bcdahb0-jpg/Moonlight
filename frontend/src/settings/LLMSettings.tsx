import { useCallback, useEffect, useState, type ReactElement } from 'react';
import {
  llmConfigApi,
  perfApi,
  ApiError,
  type LlmConfig,
  type OllamaModelsResult,
  type PerfResult,
} from '@/api/rest';

const PROVIDERS = [
  { value: 'openai', label: 'OpenAI' },
  { value: 'claude', label: 'Claude' },
  { value: 'gemini', label: 'Gemini' },
  { value: 'ollama', label: 'Ollama (本地)' },
];

const PRESET_LABELS: Record<string, string> = {
  light: '轻量预设',
  standard: '标准预设',
  high: '高性能预设',
};

export function LLMSettings(): ReactElement {
  const [config, setConfig] = useState<LlmConfig | null>(null);
  const [provider, setProvider] = useState('openai');
  const [baseUrl, setBaseUrl] = useState('');
  const [model, setModel] = useState('');
  const [apiKey, setApiKey] = useState('');
  const [ollama, setOllama] = useState<OllamaModelsResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [testing, setTesting] = useState(false);
  const [status, setStatus] = useState('');
  /** 性能与预设（keep_alive / 一键预设，原属于 perf_route；记忆整理已收敛到「记忆」页）。 */
  const [perf, setPerf] = useState<PerfResult | null>(null);

  const load = useCallback(async (): Promise<void> => {
    try {
      const [cfg, perfRes] = await Promise.all([llmConfigApi.get(), perfApi.get()]);
      setConfig(cfg);
      setPerf(perfRes);
      setBaseUrl(cfg.base_url);
      setModel(cfg.model);
      // Infer provider from base_url (crude but sufficient for the picker).
      if (cfg.base_url.includes('ollama') || cfg.base_url.includes('11434')) {
        setProvider('ollama');
      } else if (cfg.base_url.includes('anthropic')) {
        setProvider('claude');
      } else if (cfg.base_url.includes('googleapis')) {
        setProvider('gemini');
      } else {
        setProvider('openai');
      }
    } catch (err) {
      setStatus(errorMessage(err));
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const probeOllama = async (): Promise<void> => {
    setBusy(true);
    setStatus('正在探测本地 Ollama…');
    try {
      const result = await llmConfigApi.ollamaModels();
      setOllama(result);
      setStatus(result.available ? `Ollama 可用，发现 ${result.models.length} 个模型` : 'Ollama 未运行');
    } catch (err) {
      setStatus(errorMessage(err));
    } finally {
      setBusy(false);
    }
  };

  /** 保存性能/记忆设置：成功提示 + 失败显示错误。 */
  const apply = (fn: () => Promise<unknown>): void => {
    void fn()
      .then(() => setStatus('已保存（重启或重新选择角色后生效）'))
      .catch((err: unknown) => setStatus(errorMessage(err)));
  };

  const testConnection = async (): Promise<void> => {
    if (!baseUrl || !model || (!apiKey && provider !== 'ollama')) {
      setStatus('请填写 base_url、model 和 API key');
      return;
    }
    setTesting(true);
    setStatus('正在测试连接…');
    try {
      const res = await llmConfigApi.save({
        provider,
        api_key: provider === 'ollama' ? 'ollama' : apiKey,
        model,
        base_url: baseUrl,
      });
      setStatus(
        res.ok
          ? `连接成功！${res.restart_required ? '（重启后端后生效）' : ''}`
          : '测试失败',
      );
    } catch (err) {
      setStatus(errorMessage(err));
    } finally {
      setTesting(false);
    }
  };

  return (
    <div className="settings-section">
      <h3>LLM 配置</h3>
      <label className="field">
        <span>服务商</span>
        <select value={provider} onChange={(e) => setProvider(e.target.value)}>
          {PROVIDERS.map((p) => (
            <option key={p.value} value={p.value}>
              {p.label}
            </option>
          ))}
        </select>
      </label>
      <label className="field">
        <span>Base URL</span>
        <input value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)} placeholder="https://api.openai.com/v1" />
      </label>
      <label className="field">
        <span>模型</span>
        <input value={model} onChange={(e) => setModel(e.target.value)} placeholder="gpt-4o-mini" />
      </label>
      <label className="field">
        <span>API Key {config?.api_key_masked ? `（已存：${config.api_key_masked}）` : ''}</span>
        <input
          type="password"
          value={apiKey}
          onChange={(e) => setApiKey(e.target.value)}
          placeholder={provider === 'ollama' ? '本地无需 key' : 'sk-...'}
        />
      </label>
      <div className="btn-row">
        <button className="btn" onClick={() => void testConnection()} disabled={testing || busy}>
          {testing ? '测试中…' : '测试并保存'}
        </button>
        <button className="btn" onClick={() => void probeOllama()} disabled={busy}>
          探测 Ollama
        </button>
        {ollama?.available && (
          <select
            className="field-select-inline"
            value={model}
            onChange={(e) => {
              setModel(e.target.value);
              setBaseUrl('http://localhost:11434/v1');
              setProvider('ollama');
            }}
          >
            <option value="">选择本地模型…</option>
            {ollama.models.map((m) => (
              <option key={m} value={m}>
                {m}
              </option>
            ))}
          </select>
        )}
      </div>
      {status && <div className="setting-status">{status}</div>}

      {/* 性能与预设：本地模型驻留 / 一键预设（记忆整理已移到「记忆」页统一管理） */}
      {perf && (
        <div className="llm-perf-block">
          <h4 className="engine-section-title">性能与预设</h4>
          <div className="engine-more-panel">
            <label className="field">
              <span>Ollama keep_alive（秒，-1 常驻）</span>
              <input
                type="number"
                value={perf.keep_alive}
                min={perf.keep_alive_min}
                max={perf.keep_alive_max}
                onChange={(e) =>
                  apply(() => perfApi.setKeepAlive({ keep_alive: Number(e.target.value) }))
                }
              />
            </label>
            <div className="engine-presets">
              <span className="engine-presets-label">一键预设（批量调整引擎 / 记忆 / 性能，记忆相关项在「记忆」页生效）</span>
              <div className="btn-row">
                {perf.presets.map((name) => (
                  <button
                    key={name}
                    className="btn"
                    onClick={() => apply(() => perfApi.applyPreset({ name }))}
                  >
                    {PRESET_LABELS[name] ?? name}
                  </button>
                ))}
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function errorMessage(err: unknown): string {
  if (err instanceof ApiError) return err.message;
  return err instanceof Error ? err.message : '发生未知错误';
}

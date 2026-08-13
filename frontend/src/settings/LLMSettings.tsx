import { useCallback, useEffect, useState, type ReactElement } from 'react';
import {
  llmConfigApi,
  perfApi,
  ApiError,
  type LlmConfig,
  type OllamaModelsResult,
  type PerfResult,
} from '@/api/rest';
import {
  SettingsActionBar,
  SettingsGroup,
  SettingsMetricStrip,
  SettingsRow,
} from './SettingsConsole';

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
    <div className="settings-section settings-console-root">
      <SettingsMetricStrip
        metrics={[
          { label: '连接状态', value: config ? '已配置' : '待配置', tone: config ? 'ok' : 'warn' },
          { label: '当前模型', value: config?.model || '未选择' },
          { label: '服务商', value: provider.toUpperCase() },
        ]}
      />

      <SettingsGroup
        title="对话模型"
        description="配置对话服务商和当前使用的模型"
        className="console-group-primary"
      >
        <SettingsRow label="服务商" description="选择云端 API 或本地 Ollama" settingKey="setting-llm-provider">
        <select value={provider} onChange={(e) => setProvider(e.target.value)}>
          {PROVIDERS.map((p) => (
            <option key={p.value} value={p.value}>
              {p.label}
            </option>
          ))}
        </select>
        </SettingsRow>
        <SettingsRow label="Base URL" description="模型服务接口地址" settingKey="setting-llm-base-url">
          <input value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)} placeholder="https://api.openai.com/v1" />
        </SettingsRow>
        <SettingsRow label="模型" description="当前对话模型名称" settingKey="setting-llm-model">
          <input value={model} onChange={(e) => setModel(e.target.value)} placeholder="gpt-4o-mini" />
        </SettingsRow>
        <SettingsRow
          label="API Key"
          description={config?.api_key_masked ? `已保存：${config.api_key_masked}` : '仅保存在本地配置中'}
          settingKey="setting-llm-api-key"
        >
        <input
          type="password"
          value={apiKey}
          onChange={(e) => setApiKey(e.target.value)}
          placeholder={provider === 'ollama' ? '本地无需 key' : 'sk-...'}
        />
        </SettingsRow>
        <SettingsActionBar note={status || '修改模型连接后，后端可能需要重启才能完全生效'}>
          <button className="btn btn-primary" onClick={() => void testConnection()} disabled={testing || busy}>
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
        </SettingsActionBar>
        {status && <div className="setting-status">{status}</div>}
      </SettingsGroup>

      {/* 性能与预设：本地模型驻留 / 一键预设（折叠收纳，重设计 v4） */}
      {perf && (
        <SettingsGroup title="性能与预设" description="Ollama 常驻、性能档位和批量优化">
          <SettingsRow
            label="Ollama keep_alive"
            description="秒数；-1 表示常驻内存"
            settingKey="setting-llm-keep-alive"
          >
              <input
                type="number"
                value={perf.keep_alive}
                min={perf.keep_alive_min}
                max={perf.keep_alive_max}
                onChange={(e) =>
                  apply(() => perfApi.setKeepAlive({ keep_alive: Number(e.target.value) }))
                }
              />
          </SettingsRow>
          <SettingsActionBar note="预设会批量调整引擎、记忆与性能参数">
                {perf.presets.map((name) => (
                  <button
                    key={name}
                    className="btn"
                    onClick={() => apply(() => perfApi.applyPreset({ name }))}
                  >
                    {PRESET_LABELS[name] ?? name}
                  </button>
                ))}
          </SettingsActionBar>
        </SettingsGroup>
      )}
    </div>
  );
}

function errorMessage(err: unknown): string {
  if (err instanceof ApiError) return err.message;
  return err instanceof Error ? err.message : '发生未知错误';
}

import { useCallback, useEffect, useState, type ReactElement } from 'react';
import {
  taskPlatformApi,
  ApiError,
  type AgentSummary,
  type McpServerConfig,
  type McpServerProbeResult,
  type PluginSummary,
  type SkillSummary,
  type TaskPlatformConfig,
} from '@/api/rest';
import { Icon } from '@/ui/icons';
import { SettingStatus } from './SettingStatus';
import { SettingsMetricStrip } from './SettingsConsole';

function errorMessage(err: unknown): string {
  if (err instanceof ApiError) return err.message;
  return err instanceof Error ? err.message : '发生未知错误';
}

/** 各卡片可编辑的标量字段（MCP 服务器列表单独管理，不进标量 diff）。 */
const GENERAL_KEYS: (keyof TaskPlatformConfig)[] = ['enabled', 'tasks_root', 'max_no_progress'];
const SANDBOX_KEYS: (keyof TaskPlatformConfig)[] = [
  'tool_timeout_sec',
  'bash_output_limit',
  'write_limit_bytes',
  'read_limit_bytes',
  'allow_network',
];
const EMBEDDING_KEYS: (keyof TaskPlatformConfig)[] = ['embedding_enabled', 'embedding_top_k'];
// ---- v3 升级（借鉴 deer-flow / pi-agent）----
const EDIT_KEYS: (keyof TaskPlatformConfig)[] = ['read_before_write', 'bash_audit'];
const WEB_KEYS: (keyof TaskPlatformConfig)[] = [
  'web_search_enabled',
  'web_search_provider',
  'web_search_max_results',
  'web_fetch_max_bytes',
  'tavily_api_key',
  'jina_api_key',
];
const CONTEXT_KEYS: (keyof TaskPlatformConfig)[] = [
  'llm_context_window',
  'token_budget_warn_ratio',
  'token_budget_hard_ratio',
  'memory_max_injection_tokens',
];

const TRANSPORTS = ['stdio', 'http', 'sse', 'websocket', 'streamable_http'];

function emptyServer(): McpServerConfig {
  return {
    name: '',
    transport: 'stdio',
    command: '',
    args: [],
    url: '',
    headers: {},
    enabled: true,
  };
}

function parseArgs(text: string): string[] {
  return text
    .split(/[,，\s]+/)
    .map((s) => s.trim())
    .filter(Boolean);
}

function argsToText(args: string[]): string {
  return args.join(' ');
}

function parseHeaders(text: string): Record<string, string> {
  const out: Record<string, string> = {};
  for (const line of text.split('\n')) {
    const idx = line.indexOf(':');
    if (idx <= 0) continue;
    const k = line.slice(0, idx).trim();
    const v = line.slice(idx + 1).trim();
    if (k) out[k] = v;
  }
  return out;
}

function headersToText(headers: Record<string, string>): string {
  return Object.entries(headers)
    .map(([k, v]) => `${k}: ${v}`)
    .join('\n');
}

interface McpFormState {
  mode: 'add' | 'edit';
  index: number;
  server: McpServerConfig;
}

const PROBE_LABELS: Record<McpServerProbeResult['status'], string> = {
  connected: '连接成功',
  disabled: '已禁用',
  misconfigured: '配置不完整',
  error: '连接失败',
};

export function TaskPlatformSettings(): ReactElement {
  const [base, setBase] = useState<TaskPlatformConfig | null>(null);
  const [draft, setDraft] = useState<TaskPlatformConfig | null>(null);
  const [skills, setSkills] = useState<SkillSummary[]>([]);
  const [agents, setAgents] = useState<AgentSummary[]>([]);
  const [plugins, setPlugins] = useState<PluginSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [status, setStatus] = useState<string | null>(null);
  const [form, setForm] = useState<McpFormState | null>(null);
  const [probe, setProbe] = useState<McpServerProbeResult | null>(null);

  const load = useCallback(async (): Promise<void> => {
    setLoading(true);
    try {
      const [cfgRes, skillsRes, agentsRes, pluginsRes] = await Promise.all([
        taskPlatformApi.getConfig(),
        taskPlatformApi.listSkills(),
        taskPlatformApi.listAgents(),
        taskPlatformApi.listPlugins(),
      ]);
      setBase(cfgRes.config);
      setDraft(cfgRes.config);
      setSkills(skillsRes.skills);
      setAgents(agentsRes.agents);
      setPlugins(pluginsRes.plugins);
      setStatus(null);
    } catch (err) {
      setStatus(errorMessage(err));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const dirtyFor = useCallback(
    (keys: (keyof TaskPlatformConfig)[]): (keyof TaskPlatformConfig)[] => {
      if (!base || !draft) return [];
      return keys.filter((k) => base[k] !== draft[k]);
    },
    [base, draft],
  );

  const setScalar = <K extends keyof TaskPlatformConfig>(
    key: K,
    value: TaskPlatformConfig[K],
  ): void => {
    setDraft((d) => (d ? { ...d, [key]: value } : d));
  };

  const setInt = (
    key:
      | 'max_no_progress'
      | 'tool_timeout_sec'
      | 'bash_output_limit'
      | 'write_limit_bytes'
      | 'read_limit_bytes'
      | 'embedding_top_k'
      | 'llm_context_window'
      | 'web_search_max_results'
      | 'web_fetch_max_bytes'
      | 'memory_max_injection_tokens',
    raw: string,
  ): void => {
    const n = Number(raw);
    if (Number.isNaN(n)) return;
    setScalar(key, n as TaskPlatformConfig[typeof key]);
  };

  const setRatio = (key: 'token_budget_warn_ratio' | 'token_budget_hard_ratio', raw: string): void => {
    const n = Number(raw);
    if (Number.isNaN(n)) return;
    setScalar(key, Math.min(1, Math.max(0, n)) as TaskPlatformConfig[typeof key]);
  };

  const saveScalars = async (keys: (keyof TaskPlatformConfig)[]): Promise<void> => {
    if (!draft) return;
    const dirty = dirtyFor(keys);
    if (dirty.length === 0) {
      setStatus('没有需要保存的修改');
      return;
    }
    setSaving(true);
    try {
      const patch = Object.fromEntries(dirty.map((k) => [k, draft[k]]));
      const r = await taskPlatformApi.saveConfig(patch);
      setBase({ ...draft });
      setStatus(r.restart_required ? '已保存，需重启后端后生效' : '已保存');
    } catch (err) {
      setStatus(errorMessage(err));
    } finally {
      setSaving(false);
    }
  };

  // ------------------------------------------------------------------ //
  // MCP 服务器管理
  // ------------------------------------------------------------------ //
  const persistServers = async (servers: McpServerConfig[], msg?: string): Promise<boolean> => {
    try {
      const r = await taskPlatformApi.saveMcpServers(servers);
      setBase((b) => (b ? { ...b, mcp_servers: servers } : b));
      setDraft((d) => (d ? { ...d, mcp_servers: servers } : d));
      setStatus(msg ?? (r.restart_required ? 'MCP 服务器已保存，需重启后端生效' : 'MCP 服务器已保存'));
      return true;
    } catch (err) {
      setStatus(errorMessage(err));
      return false;
    }
  };

  const toggleServer = (index: number, enabled: boolean): void => {
    if (!draft) return;
    const servers = draft.mcp_servers.map((s, i) => (i === index ? { ...s, enabled } : s));
    void persistServers(servers);
  };

  const deleteServer = (index: number): void => {
    if (!draft) return;
    const servers = draft.mcp_servers.filter((_, i) => i !== index);
    void persistServers(servers, 'MCP 服务器已删除，需重启后端生效');
  };

  const submitServer = async (): Promise<void> => {
    if (!draft || !form) return;
    const s = form.server;
    if (!s.name.trim()) {
      setStatus('请填写服务器名称');
      return;
    }
    if (s.transport === 'stdio' && !s.command.trim()) {
      setStatus('stdio 服务器需要填写启动命令');
      return;
    }
    if (s.transport !== 'stdio' && !s.url?.trim()) {
      setStatus('远程服务器需要填写 URL');
      return;
    }
    const servers = [...draft.mcp_servers];
    const clean: McpServerConfig = { ...s, name: s.name.trim() };
    if (form.mode === 'add') servers.push(clean);
    else servers[form.index] = clean;
    setForm(null);
    setProbe(null);
    await persistServers(
      servers,
      form.mode === 'add' ? 'MCP 服务器已添加，需重启后端生效' : 'MCP 服务器已更新，需重启后端生效',
    );
  };

  const probeServer = async (index: number): Promise<void> => {
    if (!draft) return;
    const server = draft.mcp_servers[index];
    if (!server) return;
    setStatus(`正在测试 ${server.name} …`);
    setProbe(null);
    try {
      const r = await taskPlatformApi.probeMcpServer(server);
      setProbe(r.result);
      setStatus(
        r.result.status === 'connected'
          ? `${r.result.name} 连接成功，可用工具 ${r.result.tool_count ?? 0} 个`
          : `${r.result.name}：${r.result.error ?? PROBE_LABELS[r.result.status]}`,
      );
    } catch (err) {
      setStatus(errorMessage(err));
    }
  };

  const startAdd = (): void => {
    setForm({ mode: 'add', index: -1, server: emptyServer() });
    setProbe(null);
  };

  const startEdit = (index: number): void => {
    if (!draft) return;
    setForm({ mode: 'edit', index, server: { ...draft.mcp_servers[index] } });
    setProbe(null);
  };

  // ------------------------------------------------------------------ //
  // 渲染
  // ------------------------------------------------------------------ //
  if (loading) {
    return (
      <div className="settings-section task-platform-settings">
        <div className="setting-status">正在加载任务平台配置…</div>
      </div>
    );
  }
  const generalDirty = dirtyFor(GENERAL_KEYS).length;
  const sandboxDirty = dirtyFor(SANDBOX_KEYS).length;
  const embeddingDirty = dirtyFor(EMBEDDING_KEYS).length;
  const editDirty = dirtyFor(EDIT_KEYS).length;
  const webDirty = dirtyFor(WEB_KEYS).length;
  const contextDirty = dirtyFor(CONTEXT_KEYS).length;

  return (
    <div className="settings-section task-platform-settings settings-console-section settings-task-console">
      <SettingsMetricStrip
        metrics={[
          { label: '任务平台', value: draft?.enabled ? '已启用' : '已停用', tone: draft?.enabled ? 'ok' : 'neutral' },
          { label: 'MCP 服务器', value: `${draft?.mcp_servers.length ?? 0} 个` },
          { label: '技能 / Agent / 插件', value: `${skills.length} / ${agents.length} / ${plugins.length}` },
          { label: '联网能力', value: draft?.allow_network && draft?.web_search_enabled ? '已开启' : '受限', tone: draft?.allow_network && draft?.web_search_enabled ? 'ok' : 'warn' },
        ]}
      />
      {/* 卡片 1：通用 */}
      <section className="settings-card settings-primary-card" data-setting-key="setting-task-general">
        <div className="settings-card-head">
          <span className="settings-card-icon">
            <Icon name="tool" size={16} />
          </span>
          <div className="settings-card-title">
            <h3>通用</h3>
            <p>任务模式总开关 · 默认目录 · 无进展上限</p>
          </div>
        </div>
        <div className="settings-card-body">
          <label className="toggle-row">
            <span>启用任务平台</span>
            <input
              type="checkbox"
              checked={draft?.enabled ?? false}
              onChange={(e) => setScalar('enabled', e.target.checked)}
            />
          </label>
          <label className="field">
            <span>默认任务根目录</span>
            <input
              type="text"
              value={draft?.tasks_root ?? ''}
              placeholder="tasks/"
              onChange={(e) => setScalar('tasks_root', e.target.value)}
            />
          </label>
          <label className="field">
            <span>无进展轮数上限（1-100）</span>
            <input
              type="number"
              min={1}
              max={100}
              value={draft?.max_no_progress ?? 5}
              onChange={(e) => setInt('max_no_progress', e.target.value)}
            />
          </label>
          <div className="btn-row">
            <button className="btn btn-primary btn-sm" disabled={saving} onClick={() => void saveScalars(GENERAL_KEYS)}>
              {saving ? '保存中…' : '保存通用配置'}
            </button>
            {generalDirty > 0 && <span className="setting-note">有 {generalDirty} 项未保存</span>}
          </div>
          <div className="setting-note">任务平台配置改完需重启后端生效；关闭后任务模式将不可用。</div>
        </div>
      </section>

      {/* 卡片 2：沙箱限制 */}
      <section className="settings-card" data-setting-key="setting-task-sandbox">
        <div className="settings-card-head">
          <span className="settings-card-icon">
            <Icon name="monitor" size={16} />
          </span>
          <div className="settings-card-title">
            <h3>沙箱限制</h3>
            <p>任务智能体执行工具时的资源上限</p>
          </div>
        </div>
        <div className="settings-card-body">
          <label className="field">
            <span>bash 超时（秒）</span>
            <input
              type="number"
              min={1}
              max={3600}
              value={draft?.tool_timeout_sec ?? 120}
              onChange={(e) => setInt('tool_timeout_sec', e.target.value)}
            />
          </label>
          <label className="field">
            <span>bash 输出截断（字节）</span>
            <input
              type="number"
              min={1024}
              max={10000000}
              value={draft?.bash_output_limit ?? 65536}
              onChange={(e) => setInt('bash_output_limit', e.target.value)}
            />
          </label>
          <label className="field">
            <span>写文件上限（字节）</span>
            <input
              type="number"
              min={1024}
              max={100000000}
              value={draft?.write_limit_bytes ?? 1048576}
              onChange={(e) => setInt('write_limit_bytes', e.target.value)}
            />
          </label>
          <label className="field">
            <span>读文件上限（字节）</span>
            <input
              type="number"
              min={1024}
              max={100000000}
              value={draft?.read_limit_bytes ?? 524288}
              onChange={(e) => setInt('read_limit_bytes', e.target.value)}
            />
          </label>
          <label className="toggle-row">
            <span>允许任务 agent 联网</span>
            <input
              type="checkbox"
              checked={draft?.allow_network ?? true}
              onChange={(e) => setScalar('allow_network', e.target.checked)}
            />
          </label>
          <div className="btn-row">
            <button className="btn btn-primary btn-sm" disabled={saving} onClick={() => void saveScalars(SANDBOX_KEYS)}>
              {saving ? '保存中…' : '保存沙箱限制'}
            </button>
            {sandboxDirty > 0 && <span className="setting-note">有 {sandboxDirty} 项未保存</span>}
          </div>
          <div className="setting-note">这些限制防止任务智能体误操作把机器搞挂；改完需重启后端生效。</div>
        </div>
      </section>

      {/* 卡片 3：智能体能力（v3：文件编辑 / bash 审计） */}
      <section className="settings-card" data-setting-key="setting-task-mcp">
        <div className="settings-card-head">
          <span className="settings-card-icon">
            <Icon name="zap" size={16} />
          </span>
          <div className="settings-card-title">
            <h3>智能体能力</h3>
            <p>写前读版本门 · bash 高危命令审计（借鉴 deer-flow / pi-agent）</p>
          </div>
        </div>
        <div className="settings-card-body">
          <label className="toggle-row">
            <span>写前必须读过（read_before_write）</span>
            <input
              type="checkbox"
              checked={draft?.read_before_write ?? true}
              onChange={(e) => setScalar('read_before_write', e.target.checked)}
            />
          </label>
          <div className="setting-note">
            修改已有文件前必须先 read_file 读取当前版本（内容哈希比对），防止覆盖外部改动。误伤可关闭。
          </div>
          <label className="toggle-row">
            <span>bash 高危命令审计</span>
            <input
              type="checkbox"
              checked={draft?.bash_audit ?? true}
              onChange={(e) => setScalar('bash_audit', e.target.checked)}
            />
          </label>
          <div className="setting-note">
            拦截 <code>rm -rf /</code>、<code>curl | sh</code>、fork 炸弹等高危命令；pip/sudo 等高风险操作执行但追加警告。
          </div>
          <div className="btn-row">
            <button className="btn btn-primary btn-sm" disabled={saving} onClick={() => void saveScalars(EDIT_KEYS)}>
              {saving ? '保存中…' : '保存能力设置'}
            </button>
            {editDirty > 0 && <span className="setting-note">有 {editDirty} 项未保存</span>}
          </div>
          <div className="setting-note">改完需重启后端生效。</div>
        </div>
      </section>

      {/* 卡片 4：网页搜索（v3） */}
      <section className="settings-card" data-setting-key="setting-task-network">
        <div className="settings-card-head">
          <span className="settings-card-icon">
            <Icon name="search" size={16} />
          </span>
          <div className="settings-card-title">
            <h3>网页搜索</h3>
            <p>任务智能体联网查资料：Tavily（推荐，有 key）→ DuckDuckGo 免费兜底 + Jina 抓正文</p>
          </div>
        </div>
        <div className="settings-card-body">
          <label className="toggle-row">
            <span>启用网页搜索（web_search / web_fetch 工具）</span>
            <input
              type="checkbox"
              checked={draft?.web_search_enabled ?? true}
              onChange={(e) => setScalar('web_search_enabled', e.target.checked)}
            />
          </label>
          <label className="field">
            <span>搜索源</span>
            <select
              value={draft?.web_search_provider ?? 'auto'}
              onChange={(e) => setScalar('web_search_provider', e.target.value as TaskPlatformConfig['web_search_provider'])}
            >
              <option value="auto">auto（有 Tavily key 用 Tavily，否则 DDG）</option>
              <option value="tavily">tavily（仅 Tavily）</option>
              <option value="ddg">ddg（仅 DuckDuckGo，无需 key）</option>
            </select>
          </label>
          <label className="field">
            <span>Tavily API Key</span>
            <input
              type="password"
              value={draft?.tavily_api_key ?? ''}
              placeholder="tvly-…（留空则用 DDG；支持环境变量 TAVILY_API_KEY）"
              onChange={(e) => setScalar('tavily_api_key', e.target.value)}
            />
          </label>
          <label className="field">
            <span>Jina Reader API Key（可选，抓网页正文用）</span>
            <input
              type="password"
              value={draft?.jina_api_key ?? ''}
              placeholder="jina_…（留空匿名，限流更严）"
              onChange={(e) => setScalar('jina_api_key', e.target.value)}
            />
          </label>
          <label className="field">
            <span>单次搜索结果数（1-20）</span>
            <input
              type="number"
              min={1}
              max={20}
              value={draft?.web_search_max_results ?? 5}
              onChange={(e) => setInt('web_search_max_results', e.target.value)}
            />
          </label>
          <label className="field">
            <span>抓取正文上限（字节）</span>
            <input
              type="number"
              min={1024}
              max={10000000}
              value={draft?.web_fetch_max_bytes ?? 524288}
              onChange={(e) => setInt('web_fetch_max_bytes', e.target.value)}
            />
          </label>
          <div className="btn-row">
            <button className="btn btn-primary btn-sm" disabled={saving} onClick={() => void saveScalars(WEB_KEYS)}>
              {saving ? '保存中…' : '保存搜索设置'}
            </button>
            {webDirty > 0 && <span className="setting-note">有 {webDirty} 项未保存</span>}
          </div>
          <div className="setting-note">
            API key 保存在本地 conf.yaml（含真实密钥，永不提交）。依赖「沙箱限制 → 允许任务 agent 联网」。
            改完需重启后端生效。
          </div>
        </div>
      </section>

      {/* 卡片 5：上下文与记忆（v3） */}
      <section className="settings-card">
        <div className="settings-card-head">
          <span className="settings-card-icon">
            <Icon name="database" size={16} />
          </span>
          <div className="settings-card-title">
            <h3>上下文与记忆</h3>
            <p>token 预算前瞻 · 上下文窗口 · 项目级记忆（DeerMem）</p>
          </div>
        </div>
        <div className="settings-card-body">
          <label className="field">
            <span>上下文窗口（token，0=按模型自动探测）</span>
            <input
              type="number"
              min={0}
              max={2000000}
              value={draft?.llm_context_window ?? 0}
              placeholder="0（DeepSeek 自动 1M）"
              onChange={(e) => setInt('llm_context_window', e.target.value)}
            />
          </label>
          <div className="setting-note">0 时按模型名自动探测（deepseek-chat/reasoner = 1M，未知模型回退 64k）。</div>
          <label className="field">
            <span>软警告阈值（0-1，默认 0.8）</span>
            <input
              type="number"
              min={0}
              max={1}
              step={0.05}
              value={draft?.token_budget_warn_ratio ?? 0.8}
              onChange={(e) => setRatio('token_budget_warn_ratio', e.target.value)}
            />
          </label>
          <label className="field">
            <span>硬停阈值（0-1，默认 0.95）</span>
            <input
              type="number"
              min={0}
              max={1}
              step={0.05}
              value={draft?.token_budget_hard_ratio ?? 0.95}
              onChange={(e) => setRatio('token_budget_hard_ratio', e.target.value)}
            />
          </label>
          <div className="setting-note">
            超过软阈值后提示 /compact；超过硬阈值自动暂停任务（剥离工具调用防残缺参数执行）。
          </div>
          <label className="field">
            <span>项目记忆注入上限（token）</span>
            <input
              type="number"
              min={100}
              max={100000}
              value={draft?.memory_max_injection_tokens ?? 1500}
              onChange={(e) => setInt('memory_max_injection_tokens', e.target.value)}
            />
          </label>
          <div className="setting-note">
            记忆存于 <code>{'<工作目录>/.pi/memory/'}</code>（跨任务积累，任务完成自动写入）。
          </div>
          <div className="btn-row">
            <button className="btn btn-primary btn-sm" disabled={saving} onClick={() => void saveScalars(CONTEXT_KEYS)}>
              {saving ? '保存中…' : '保存上下文设置'}
            </button>
            {contextDirty > 0 && <span className="setting-note">有 {contextDirty} 项未保存</span>}
          </div>
          <div className="setting-note">改完需重启后端生效。</div>
        </div>
      </section>

      {/* 卡片 6：MCP 服务器（替代旧 use_mcpp） */}
      <section className="settings-card">
        <div className="settings-card-head">
          <span className="settings-card-icon">
            <Icon name="wifi" size={16} />
          </span>
          <div className="settings-card-title">
            <h3>MCP 服务器</h3>
            <p>外部工具接入（联网抓取 / 时间 / 自定义），替代旧的 use_mcpp 开关</p>
          </div>
        </div>
        <div className="settings-card-body">
          <div className="mcp-server-head">
            <span className="mcp-server-count">{draft?.mcp_servers.length ?? 0} 个服务器</span>
            <button className="btn btn-sm btn-primary" onClick={startAdd}>
              <Icon name="plus" size={14} /> 添加服务器
            </button>
          </div>
          <div className="mcp-server-table">
            {draft?.mcp_servers.map((s, i) => (
              <div className="mcp-server-row" key={`${s.name}-${i}`}>
                <span className={`mcp-status-dot ${s.enabled ? 'on' : 'off'}`} />
                <div className="mcp-server-main">
                  <div className="mcp-server-name">{s.name}</div>
                  <div className="mcp-server-sub">
                    {s.transport}
                    {s.transport === 'stdio'
                      ? ` · ${s.command} ${s.args.join(' ')}`
                      : ` · ${s.url ?? ''}`}
                  </div>
                </div>
                <label className="switch-row mcp-enable" title={s.enabled ? '点击禁用' : '点击启用'}>
                  <input
                    type="checkbox"
                    className="switch"
                    checked={s.enabled}
                    onChange={(e) => toggleServer(i, e.target.checked)}
                  />
                  <span className="switch-ui" aria-hidden="true" />
                </label>
                <div className="mcp-actions">
                  <button className="btn btn-sm" title="测试连接" onClick={() => void probeServer(i)}>
                    <Icon name="refresh" size={13} /> 测试
                  </button>
                  <button className="btn btn-sm" title="编辑" onClick={() => startEdit(i)}>
                    <Icon name="edit" size={13} />
                  </button>
                  <button className="btn btn-sm btn-danger" title="删除" onClick={() => deleteServer(i)}>
                    <Icon name="trash" size={13} />
                  </button>
                </div>
              </div>
            ))}
            {(draft?.mcp_servers.length ?? 0) === 0 && (
              <div className="setting-note">尚未配置 MCP 服务器，点击「添加服务器」接入外部工具。</div>
            )}
          </div>

          {probe && (
            <div className="mcp-probe-result">
              <span className={`mcp-status-dot ${probe.status === 'connected' ? 'on' : 'off'}`} />
              <div>
                <div className="mcp-probe-title">{PROBE_LABELS[probe.status] ?? probe.status}</div>
                <div className="mcp-probe-detail">
                  {probe.status === 'connected' && probe.tools && probe.tools.length > 0
                    ? `可用工具：${probe.tools.join(' · ')}`
                    : (probe.error ?? probe.status)}
                </div>
              </div>
            </div>
          )}

          {form && (
            <div className="mcp-server-form">
              <div className="mcp-form-head">
                <span>{form.mode === 'add' ? '添加 MCP 服务器' : `编辑 ${form.server.name}`}</span>
                <button className="btn btn-sm" title="关闭" onClick={() => setForm(null)}>
                  <Icon name="x" size={14} />
                </button>
              </div>
              <div className="mcp-form-grid">
                <label className="field">
                  <span>名称 *</span>
                  <input
                    type="text"
                    value={form.server.name}
                    placeholder="fetch"
                    onChange={(e) => setForm({ ...form, server: { ...form.server, name: e.target.value } })}
                  />
                </label>
                <label className="field">
                  <span>传输方式</span>
                  <select
                    value={form.server.transport}
                    onChange={(e) => setForm({ ...form, server: { ...form.server, transport: e.target.value } })}
                  >
                    {TRANSPORTS.map((t) => (
                      <option key={t} value={t}>
                        {t}
                      </option>
                    ))}
                  </select>
                </label>
                {form.server.transport === 'stdio' ? (
                  <>
                    <label className="field">
                      <span>启动命令 *</span>
                      <input
                        type="text"
                        value={form.server.command}
                        placeholder="uvx"
                        onChange={(e) => setForm({ ...form, server: { ...form.server, command: e.target.value } })}
                      />
                    </label>
                    <label className="field">
                      <span>参数（空格分隔）</span>
                      <input
                        type="text"
                        value={argsToText(form.server.args)}
                        placeholder="mcp-server-fetch"
                        onChange={(e) => setForm({ ...form, server: { ...form.server, args: parseArgs(e.target.value) } })}
                      />
                    </label>
                  </>
                ) : (
                  <label className="field mcp-form-full">
                    <span>URL *</span>
                    <input
                      type="text"
                      value={form.server.url ?? ''}
                      placeholder="http://127.0.0.1:9000/sse"
                      onChange={(e) => setForm({ ...form, server: { ...form.server, url: e.target.value } })}
                    />
                  </label>
                )}
                <label className="field mcp-form-full">
                  <span>Headers（每行 k: v）</span>
                  <textarea
                    rows={3}
                    value={headersToText(form.server.headers)}
                    onChange={(e) => setForm({ ...form, server: { ...form.server, headers: parseHeaders(e.target.value) } })}
                  />
                </label>
                <label className="toggle-row mcp-form-full">
                  <span>启用该服务器</span>
                  <input
                    type="checkbox"
                    checked={form.server.enabled}
                    onChange={(e) => setForm({ ...form, server: { ...form.server, enabled: e.target.checked } })}
                  />
                </label>
              </div>
              <div className="btn-row">
                <button className="btn btn-primary btn-sm" onClick={() => void submitServer()}>
                  {form.mode === 'add' ? '添加' : '保存'}
                </button>
                <button className="btn btn-sm" onClick={() => setForm(null)}>
                  取消
                </button>
              </div>
            </div>
          )}
          <div className="setting-note">
            stdio 服务器用本地命令启动（如 <code>uvx mcp-server-fetch</code>）；http/sse/websocket 用 URL。
            每个服务器 fail-soft：单个失败不影响主链路。改完需重启后端生效。
          </div>
        </div>
      </section>

      {/* 卡片 4：技能 / 子智能体 / 插件 */}
      <section className="settings-card">
        <div className="settings-card-head">
          <span className="settings-card-icon">
            <Icon name="database" size={16} />
          </span>
          <div className="settings-card-title">
            <h3>技能 / 子智能体 / 插件</h3>
            <p>技能库、sub-agent 目录与 extensions 插件</p>
          </div>
        </div>
        <div className="settings-card-body">
          <div className="field">
            <span>目录</span>
            <div className="mcp-dir-list">
              <span><code>技能库</code> {draft?.skills_root ?? '-'}</span>
              <span><code>子智能体</code> {draft?.agents_root ?? '-'}</span>
              <span><code>插件</code> {draft?.plugins_root ?? '-'}</span>
            </div>
            <div className="setting-note">目录路径在 conf.yaml 的 task_platform 中修改。</div>
          </div>
          <label className="toggle-row">
            <span>技能语义检索（embedding）</span>
            <input
              type="checkbox"
              checked={draft?.embedding_enabled ?? false}
              onChange={(e) => setScalar('embedding_enabled', e.target.checked)}
            />
          </label>
          <label className="field">
            <span>检索 Top-K</span>
            <input
              type="number"
              min={1}
              max={100}
              value={draft?.embedding_top_k ?? 5}
              onChange={(e) => setInt('embedding_top_k', e.target.value)}
            />
          </label>
          <div className="btn-row">
            <button className="btn btn-primary btn-sm" disabled={saving} onClick={() => void saveScalars(EMBEDDING_KEYS)}>
              {saving ? '保存中…' : '保存检索设置'}
            </button>
            {embeddingDirty > 0 && <span className="setting-note">有 {embeddingDirty} 项未保存</span>}
          </div>

          <div className="mcp-dir-section">
            <div className="mcp-dir-head">
              <Icon name="sparkles" size={14} /> 已加载技能（{skills.length}）
            </div>
            {skills.length === 0 ? (
              <div className="setting-note">无技能</div>
            ) : (
              skills.map((s) => (
                <div className="mcp-dir-item" key={s.name}>
                  <span className="mcp-dir-item-name">{s.name}</span>
                  <span className="mcp-dir-item-desc">{s.description}</span>
                </div>
              ))
            )}
          </div>
          <div className="mcp-dir-section">
            <div className="mcp-dir-head">
              <Icon name="user" size={14} /> 子智能体（{agents.length}）
            </div>
            {agents.length === 0 ? (
              <div className="setting-note">无子智能体</div>
            ) : (
              agents.map((a) => (
                <div className="mcp-dir-item" key={a.name}>
                  <span className="mcp-dir-item-name">{a.display_name || a.name}</span>
                  <span className="mcp-dir-item-desc">{a.description}</span>
                </div>
              ))
            )}
          </div>
          <div className="mcp-dir-section">
            <div className="mcp-dir-head">
              <Icon name="zap" size={14} /> 已加载插件（{plugins.length}）
            </div>
            {plugins.length === 0 ? (
              <div className="setting-note">无插件（在 {draft?.plugins_root ?? 'plugins/'} 添加 .py 文件，或改名 .disabled 禁用）</div>
            ) : (
              plugins.map((p) => (
                <div className="mcp-dir-item" key={p.name}>
                  <span className="mcp-dir-item-name">{p.name}</span>
                  <span className="mcp-dir-item-desc">{p.enabled ? '已启用' : '已禁用'}</span>
                </div>
              ))
            )}
          </div>
        </div>
      </section>

      <SettingStatus message={status} />
    </div>
  );
}

/**
 * Typed REST client for the Open-LLM-VTuber backend's localhost-only `/api/*`
 * endpoints (llm-config, character, memory, perf, proactive-topics, ...).
 */

export const API_BASE = 'http://127.0.0.1:12393';

/** 用真实引擎 + 音色合成试听音频（GET /api/tts-voice-sample 返回音频字节）。 */
export const voiceApi = {
  sample: async (engine: string, voice: string, text?: string): Promise<string> => {
    const qs = new URLSearchParams({ engine, voice });
    if (text) qs.set('text', text);
    const resp = await fetch(`${API_BASE}/api/tts-voice-sample?${qs.toString()}`);
    if (!resp.ok) {
      let message = `HTTP ${resp.status}`;
      try {
        const body = (await resp.json()) as { error?: string };
        if (body.error) message = body.error;
      } catch {
        // non-JSON error body; keep the status message
      }
      throw new ApiError(resp.status, message);
    }
    const blob = await resp.blob();
    return URL.createObjectURL(blob);
  },
};

export class ApiError extends Error {
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE}${path}`, {
      ...init,
      headers: {
        'Content-Type': 'application/json',
        ...(init?.headers ?? {}),
      },
    });
  } catch (err) {
    throw new ApiError(0, '无法连接后端服务，请确认后端已启动 (127.0.0.1:12393)');
  }

  if (!response.ok) {
    let message = `HTTP ${response.status}`;
    try {
      const body = (await response.json()) as { error?: string };
      if (body.error) message = body.error;
    } catch {
      // non-JSON error body; keep the status message
    }
    throw new ApiError(response.status, message);
  }

  return (await response.json()) as T;
}

function get<T>(path: string): Promise<T> {
  return request<T>(path, { method: 'GET' });
}

function post<T>(path: string, body?: unknown): Promise<T> {
  return request<T>(path, { method: 'POST', body: JSON.stringify(body ?? {}) });
}

function put<T>(path: string, body?: unknown): Promise<T> {
  return request<T>(path, { method: 'PUT', body: JSON.stringify(body ?? {}) });
}

function del<T>(path: string): Promise<T> {
  return request<T>(path, { method: 'DELETE' });
}

// ------------------------------------------------------------------ //
// 统一配置 API（config_route.py，Phase 1 单一事实源收口）
// ------------------------------------------------------------------ //

/** GET /api/config 返回的配置结构（脱敏后；详见 contracts/config-schema.json 生成的类型）。 */
export interface ConfigPayload {
  system_config?: {
    ui_prefs?: {
      screen_aware_enabled?: boolean;
      screen_poll_interval_sec?: number;
      proactive_enabled?: boolean;
      proactive_idle_sec?: number;
      auto_speak_on_idle?: boolean;
      // UX 修复（2026-08-10）：与后端 UiPrefs 新增字段保持一致
      proactive_pet_mode_only?: boolean;
      subtitle_enabled?: boolean;
    };
    [key: string]: unknown;
  };
  character_config?: Record<string, unknown>;
  live_config?: Record<string, unknown>;
  [key: string]: unknown;
}

export interface UpdateConfigResult {
  ok: boolean;
  warning?: string | null;
  config?: ConfigPayload;
}

export const configApi = {
  /** 读取完整配置（pydantic 校验 + key 脱敏）。 */
  get: (): Promise<ConfigPayload> => get<ConfigPayload>('/api/config'),
  /** JSON Merge Patch 局部更新：合并 -> 校验 -> 原子写盘 -> 热重载 -> 广播 config-updated。 */
  update: (patch: unknown): Promise<UpdateConfigResult> =>
    put<UpdateConfigResult>('/api/config', patch),
};

// ------------------------------------------------------------------ //
// 就绪度检查（readiness_route.py，Phase 3）
// ------------------------------------------------------------------ //

export interface ReadinessItem {
  id: string;
  passed: boolean;
  hint: string;
}

export interface ReadinessResult {
  ready: boolean;
  checks: ReadinessItem[];
}

export const readinessApi = {
  /** 四项后端可判定检查（Live2D / LLM / ASR / Ollama）。 */
  get: (): Promise<ReadinessResult> => get<ReadinessResult>('/api/readiness'),
};

// ------------------------------------------------------------------ //
// LLM config (llm_config_route.py)
// ------------------------------------------------------------------ //

export interface LlmConfig {
  provider: string;
  base_url: string;
  model: string;
  api_key_masked: string;
  is_configured: boolean;
}

export interface OllamaModelsResult {
  available: boolean;
  models: string[];
  recommended: string;
}

export interface SaveLlmConfigResult {
  ok: boolean;
  provider: string;
  model: string;
  base_url: string;
  api_key_masked: string;
  restart_required: boolean;
}

export const llmConfigApi = {
  get: () => get<LlmConfig>('/api/llm-config'),
  save: (body: { provider: string; api_key: string; model: string; base_url?: string }) =>
    post<SaveLlmConfigResult>('/api/llm-config', body),
  ollamaModels: () => get<OllamaModelsResult>('/api/llm-config/ollama-models'),
};

// ------------------------------------------------------------------ //
// Character manager (character_route.py)
// ------------------------------------------------------------------ //

export interface CharacterField {
  filename: string;
  slug: string | null;
  is_base: boolean;
  conf_name: string | null;
  character_name: string | null;
  avatar: string | null;
  conf_uid: string | null;
  live2d_model_name: string | null;
  persona_prompt: string | null;
  /** 角色覆盖的 TTS 引擎名（null = 继承基础配置）。 */
  tts_model: string | null;
  /** 该引擎对应的音色值（语义取决于 tts_model）。 */
  voice: string | null;
}

export interface SkinInfo {
  name: string;
  registered: boolean;
  thumbnail: string | null;
}

export interface VoiceInfo {
  value: string;
  label: string;
  locale: string;
  gender: string;
}

export interface CharactersResult {
  characters: CharacterField[];
}

export interface SkinsResult {
  skins: SkinInfo[];
  newly_registered: string[];
}

export interface VoicesResult {
  voices: VoiceInfo[];
  source: 'curated' | 'edge_tts';
}

export interface SaveCharacterResult {
  ok: boolean;
  filename: string;
  conf_uid: string;
  conf_name: string;
  restart_required: boolean;
}

export const characterApi = {
  list: () => get<CharactersResult>('/api/characters'),
  skins: () => get<SkinsResult>('/api/live2d-skins'),
  voices: () => get<VoicesResult>('/api/voices'),
  create: (body: Record<string, unknown>) => post<SaveCharacterResult>('/api/characters', body),
  update: (filename: string, body: Record<string, unknown>) =>
    put<SaveCharacterResult>(`/api/characters/${encodeURIComponent(filename)}`, body),
  remove: (filename: string) => del<{ ok: boolean }>(`/api/characters/${encodeURIComponent(filename)}`),
};

// ------------------------------------------------------------------ //
// TTS voice catalogs (character_route.py /api/tts-voices)
// ------------------------------------------------------------------ //

/** mode="list" 时：engine 提供的可选音色。 */
export interface TtsVoiceCatalogResult {
  engine: string;
  mode: 'list' | 'input';
  voices: VoiceInfo[];
  /** mode="input" 时：让用户手填的音色字段描述。 */
  input: {
    field: string;
    label: string;
    hint: string;
  } | null;
  /** 当前配置下能否用真实引擎合成试听音频。 */
  previewable: boolean;
  /** 不能试听时的原因说明。 */
  previewNote: string;
}

export const ttsApi = {
  listVoices: (engine: string) =>
    get<TtsVoiceCatalogResult>(`/api/tts-voices?engine=${encodeURIComponent(engine)}`),
};

// ------------------------------------------------------------------ //
// Memory (memory_route.py)
// ------------------------------------------------------------------ //

export interface MemoryResult {
  conf_uid: string;
  enabled: boolean;
  content: string;
  exists: boolean;
  char_count: number;
  cap: number;
  cap_min: number;
  cap_max: number;
  fts_enabled: boolean;
  fts_top_k: number;
  fts_top_k_min: number;
  fts_top_k_max: number;
  fts_indexed: boolean;
  vector_enabled: boolean;
  vector_top_k: number;
  vector_top_k_min: number;
  vector_top_k_max: number;
  vector_count: number;
  /** 向量记忆实际使用的 embedding 端点/模型（vector_embedding_*，UI 显示用）。 */
  vector_embedding: { base_url: string; model: string; api_key: string };
  consolidation_interval: number;
  consolidation_interval_choices: number[];
  // Memory v2 (typed facts + reflections + dreaming)
  v2_enabled: boolean;
  v2_max_facts: number;
  v2_facts: number;
  v2_reflections: number;
  v2_proposals_pending: number;
  v2_proposals_total: number;
  v2_last_dream_at: number;
}

export interface V2Fact {
  id: number;
  text: string;
  importance: number;
  entity: string;
  source: string;
  reinforcement: number;
  disputation: number;
  status: string;
  created_at: number;
  last_seen_at: number;
}

export interface V2Reflection {
  id: number;
  text: string;
  entity: string;
  status: string;
  source_fact_ids: string;
  reinforcement: number;
  disputation: number;
  created_at: number;
  last_signal_at: number;
}

export interface V2Proposal {
  id: number;
  kind: string;
  source_ids: string;
  proposed_text: string;
  confidence: number;
  status: string;
  created_at: number;
}

export interface V2FactsResult {
  ok: boolean;
  conf_uid: string;
  status: string;
  facts: V2Fact[];
}

export interface V2ReflectionsResult {
  ok: boolean;
  conf_uid: string;
  status: string;
  reflections: V2Reflection[];
}

export interface V2ProposalsResult {
  ok: boolean;
  conf_uid: string;
  status: string;
  proposals: V2Proposal[];
}

export interface V2DreamResult {
  ok: boolean;
  conf_uid: string;
  summary: {
    hash_dedup: number;
    archived: number;
    pairs: number;
    auto_merged: number;
    proposals: number;
    reflections_fused: number;
    confirmed: number;
  };
}

export const memoryApi = {
  get: (confUid: string) =>
    get<MemoryResult>(`/api/memory?conf_uid=${encodeURIComponent(confUid)}`),
  save: (body: { conf_uid: string; content: string }) => post<{ ok: boolean }>('/api/memory', body),
  toggle: (body: { conf_uid: string; enabled: boolean }) =>
    post<{ ok: boolean }>('/api/memory/toggle', body),
  clear: (body: { conf_uid: string }) => post<{ ok: boolean; cleared: boolean }>('/api/memory/clear', body),
  setCap: (body: { conf_uid: string; cap: number }) =>
    post<{ ok: boolean; cap: number }>('/api/memory/cap', body),
  setConsolidation: (body: { conf_uid: string; interval: number }) =>
    post<{ ok: boolean }>('/api/memory/consolidation', body),
  setFts: (body: { conf_uid: string; enabled?: boolean; top_k?: number }) =>
    post<{ ok: boolean; fts_enabled: boolean; fts_top_k: number }>('/api/memory/fts', body),
  setVector: (body: { conf_uid: string; enabled?: boolean; top_k?: number }) =>
    post<{ ok: boolean; vector_enabled: boolean; vector_top_k: number }>(
      '/api/memory/vector',
      body,
    ),
  reindex: (body: { conf_uid: string }) =>
    post<{ ok: boolean; indexed_count: number }>('/api/memory/reindex', body),
  // ---- Memory v2 (typed facts + reflections + dreaming) ----
  setV2: (body: { conf_uid: string; enabled?: boolean; max_facts?: number }) =>
    post<{ ok: boolean; v2_enabled: boolean; v2_max_facts: number }>(
      '/api/memory/v2',
      body,
    ),
  dream: (body: { conf_uid: string; promote_min_age_days?: number }) =>
    post<V2DreamResult>('/api/memory/dream', body),
  getFacts: (confUid: string, status = 'active', limit = 50) =>
    get<V2FactsResult>(
      `/api/memory/v2/facts?conf_uid=${encodeURIComponent(confUid)}&status=${encodeURIComponent(status)}&limit=${limit}`,
    ),
  getReflections: (confUid: string, status = '', limit = 50) =>
    get<V2ReflectionsResult>(
      `/api/memory/v2/reflections?conf_uid=${encodeURIComponent(confUid)}&status=${encodeURIComponent(status)}&limit=${limit}`,
    ),
  getProposals: (confUid: string, status = 'pending', limit = 50) =>
    get<V2ProposalsResult>(
      `/api/memory/v2/proposals?conf_uid=${encodeURIComponent(confUid)}&status=${encodeURIComponent(status)}&limit=${limit}`,
    ),
  decideProposal: (body: { conf_uid: string; proposal_id: number; action: 'approve' | 'reject' }) =>
    post<{ ok: boolean }>(`/api/memory/v2/proposals/${body.proposal_id}`, body),
  signalFact: (body: { conf_uid: string; fact_id: number; action: 'reinforce' | 'rebut' }) =>
    post<{ ok: boolean }>('/api/memory/v2/facts/signal', body),
};

// ------------------------------------------------------------------ //
// Performance (perf_route.py)
// ------------------------------------------------------------------ //

export interface PerfResult {
  asr_model: string;
  groq_api_key_masked: string;
  azure_api_key_masked: string;
  azure_region: string;
  tts_model: string;
  tts_voice: string;
  gpt_sovits_api_url: string;
  gpt_sovits_ref_audio_path: string;
  keep_alive: number;
  keep_alive_min: number;
  keep_alive_max: number;
  consolidation_interval: number;
  consolidation_interval_choices: number[];
  asr_models: string[];
  tts_models: string[];
  presets: string[];
}

export const perfApi = {
  get: () => get<PerfResult>('/api/perf'),
  setAsr: (body: Record<string, unknown>) => post<{ ok: boolean }>('/api/perf/asr', body),
  setTts: (body: Record<string, unknown>) => post<{ ok: boolean }>('/api/perf/tts', body),
  setKeepAlive: (body: { keep_alive: number }) =>
    post<{ ok: boolean; keep_alive: number }>('/api/perf/keep-alive', body),
  setConsolidation: (body: { interval: number }) =>
    post<{ ok: boolean }>('/api/perf/consolidation', body),
  applyPreset: (body: { name: string }) =>
    post<{ ok: boolean; preset: string; restart_required: boolean }>('/api/perf/preset', body),
};

// ------------------------------------------------------------------ //
// Engine management (engine_route.py) —— 引擎配置状态 + VOICEVOX 管理
// ------------------------------------------------------------------ //

export interface EngineField {
  key: string;
  label: string;
  type: 'text' | 'password' | 'number';
  placeholder?: string;
  hint?: string;
}

export type EngineKind = 'cloud' | 'local' | 'local_service' | 'builtin';

export interface EngineInfo {
  key: string;
  zh: string;
  kind: EngineKind;
  desc: string;
  configured: boolean;
  reason: string;
  fields: EngineField[];
  voice_field?: string | null;
}

export interface EnginesResult {
  tts: EngineInfo[];
  asr: EngineInfo[];
}

export interface ConfiguredEnginesResult {
  scope: string;
  engines: EngineInfo[];
}

export interface VoiceVoxStatus {
  state: 'missing' | 'downloading' | 'downloaded' | 'failed';
  running: boolean;
  progress: number;
  phase?: string;
  size_mb?: number;
}

export interface VoiceVoxResult {
  ok: boolean;
  error?: string;
  msg?: string;
  running?: boolean;
}

export const engineApi = {
  /** 全部引擎 + 配置状态 + 字段 schema（设置面板用）。 */
  list: (scope: 'tts' | 'asr' | 'all' = 'all'): Promise<EnginesResult> =>
    get<EnginesResult>(`/api/engines?scope=${scope}`),
  /** 已配置可用的引擎（角色卡下拉用）。 */
  configured: (scope: 'tts' | 'asr' = 'tts'): Promise<ConfiguredEnginesResult> =>
    get<ConfiguredEnginesResult>(`/api/engines/configured?scope=${scope}`),
  /** 保存某引擎子块的配置字段。 */
  save: (
    engine: string,
    scope: 'tts' | 'asr',
    fields: Record<string, string>,
  ): Promise<{ ok: boolean; engine?: EngineInfo; restart_required?: boolean }> =>
    post(`/api/engines/${encodeURIComponent(engine)}`, { scope, fields }),
  voicevoxStatus: (): Promise<{ ok: boolean; voicevox: VoiceVoxStatus }> =>
    get('/api/engines/voicevox/status'),
  voicevoxDownload: (useMirror = false): Promise<VoiceVoxResult> =>
    post('/api/engines/voicevox/download', { use_mirror: useMirror }),
  voicevoxStart: (): Promise<VoiceVoxResult> => post('/api/engines/voicevox/start'),
  voicevoxStop: (): Promise<VoiceVoxResult> => post('/api/engines/voicevox/stop'),
};

// ------------------------------------------------------------------ //
// Proactive topics (topics_route.py)
// ------------------------------------------------------------------ //

export interface ProactiveTopicsResult {
  topics: string[];
  news: { enabled: boolean; interval_hours: number };
  last_news_refresh: string | null;
  suggestions: string[];
}

export const topicsApi = {
  get: () => get<ProactiveTopicsResult>('/api/proactive-topics'),
  save: (body: { topics?: string[]; news?: { enabled?: boolean; interval_hours?: number } }) =>
    post<{ ok: boolean; composed: boolean; topics: string[] }>('/api/proactive-topics', body),
  refresh: () => post<{ ok: boolean; news_enabled: boolean; news_count: number }>(
    '/api/proactive-topics/refresh',
  ),
};

// ------------------------------------------------------------------ //
// Live2D model info (routes.py)
// ------------------------------------------------------------------ //

export interface Live2dModelEntry {
  name: string;
  avatar: string | null;
  model_path: string;
}

export const live2dApi = {
  info: () => get<{ type: string; count: number; characters: Live2dModelEntry[] }>(
    '/live2d-models/info',
  ),
  /** 保存前端渲染生成的模型立绘缩略图（存为 live2d-models/<name>/<name>.png）。 */
  saveThumbnail: (name: string, data: string) =>
    post<{ ok: boolean }>('/api/live2d-skins/thumbnail', { name, data }),
};

// ------------------------------------------------------------------ //
// Translator (translator_route.py)
// ------------------------------------------------------------------ //

export interface TranslatorConfig {
  enabled: boolean;
  engine: 'llm' | 'deeplx';
  llm_target_lang: string;
  llm_endpoint: string;
  llm_model: string;
  deeplx_target_lang: string;
  deeplx_endpoint: string;
  translate_subtitle: boolean;
  subtitle_target_lang: string;
}

export const translatorApi = {
  get: () => get<TranslatorConfig>('/api/translator-config'),
  save: (body: Record<string, unknown>) =>
    post<{ ok: boolean; restart_required: boolean }>('/api/translator-config', body),
};

// ------------------------------------------------------------------ //
// Player language / prompt / background (translator_route.py)
// ------------------------------------------------------------------ //

export const playerApi = {
  getLanguage: () => get<{ language: string }>('/api/player-language'),
  setLanguage: (body: { language: string }) =>
    post<{ ok: boolean; language: string }>('/api/player-language', body),
};

export const playerPromptApi = {
  get: () => get<{ prompt: string }>('/api/player-prompt'),
  save: (body: { prompt: string }) => post<{ ok: boolean; prompt: string }>('/api/player-prompt', body),
};

export const defaultBgApi = {
  get: () => get<{ background: string }>('/api/default-background'),
};

// ------------------------------------------------------------------ //
// Agent / MCP toggle (translator_route.py)
// ------------------------------------------------------------------ //

export const agentApi = {
  get: () => get<{ use_mcpp: boolean }>('/api/agent-config/use-mcpp'),
  save: (body: { use_mcpp: boolean }) =>
    post<{ ok: boolean; use_mcpp: boolean; restart_required: boolean }>(
      '/api/agent-config/use-mcpp',
      body,
    ),
};

// ------------------------------------------------------------------ //
// Task platform config (task_platform/task_config_route.py)
// ------------------------------------------------------------------ //

/** 单个 MCP 服务器配置（conf.yaml task_platform.mcp.servers 项）。 */
export interface McpServerConfig {
  name: string;
  transport: string;
  command: string;
  args: string[];
  url?: string | null;
  headers: Record<string, string>;
  enabled: boolean;
}

/** GET /api/task-platform/config 返回的扁平化配置（Phase 0-6 + v3 全部可编辑字段）。 */
export interface TaskPlatformConfig {
  enabled: boolean;
  tasks_root: string;
  tool_timeout_sec: number;
  bash_output_limit: number;
  write_limit_bytes: number;
  read_limit_bytes: number;
  allow_network: boolean;
  max_no_progress: number;
  skills_root: string;
  agents_root: string;
  plugins_root: string;
  embedding_enabled: boolean;
  embedding_top_k: number;
  // ---- v3 升级（借鉴 deer-flow / pi-agent）----
  llm_context_window: number;
  read_before_write: boolean;
  web_search_enabled: boolean;
  web_search_provider: 'auto' | 'ddg' | 'tavily';
  web_search_max_results: number;
  web_fetch_max_bytes: number;
  tavily_api_key: string;
  jina_api_key: string;
  bash_audit: boolean;
  token_budget_warn_ratio: number;
  token_budget_hard_ratio: number;
  memory_max_injection_tokens: number;
  mcp_servers: McpServerConfig[];
}

/** POST /api/task-platform/mcp/probe 的探测结果（fail-soft）。 */
export interface McpServerProbeResult {
  name: string;
  transport: string;
  enabled: boolean;
  status: 'connected' | 'error' | 'disabled' | 'misconfigured';
  tool_count?: number;
  tools?: string[];
  error?: string;
}

export interface SaveTaskPlatformConfigResult {
  ok: boolean;
  updated?: string[];
  restart_required: boolean;
}

/** 技能索引摘要（GET /api/skills，不含正文）。 */
export interface SkillSummary {
  name: string;
  description: string;
  allowed_tools: string[];
  required_secrets: string[];
}

/** sub-agent 目录条目（GET /api/agents）。 */
export interface AgentSummary {
  name: string;
  description: string;
  display_name: string;
  tools: string[];
  prompt_mode: string;
}

/** 已加载插件条目（GET /api/plugins）。 */
export interface PluginSummary {
  name: string;
  enabled: boolean;
}

export const taskPlatformApi = {
  /** 读取任务平台配置（force_reload：总是磁盘最新值）。 */
  getConfig: (): Promise<{ ok: boolean; config: TaskPlatformConfig }> =>
    get('/api/task-platform/config'),
  /** JSON Merge Patch 保存标量配置（写盘需重启生效）。 */
  saveConfig: (patch: Partial<TaskPlatformConfig>): Promise<SaveTaskPlatformConfigResult> =>
    put('/api/task-platform/config', patch),
  /** 整体替换 MCP 服务器列表。 */
  saveMcpServers: (servers: McpServerConfig[]): Promise<SaveTaskPlatformConfigResult> =>
    post('/api/task-platform/mcp/servers', servers),
  /** 测试单个 MCP 服务器连接并列出工具。 */
  probeMcpServer: (server: McpServerConfig): Promise<{ ok: boolean; result: McpServerProbeResult }> =>
    post('/api/task-platform/mcp/probe', server),
  /** 技能索引（只读摘要）。 */
  listSkills: (): Promise<{ ok: boolean; skills: SkillSummary[] }> => get('/api/skills'),
  /** sub-agent 目录（只读摘要）。 */
  listAgents: (): Promise<{ ok: boolean; agents: AgentSummary[] }> => get('/api/agents'),
  /** 已加载插件（只读摘要）。 */
  listPlugins: (): Promise<{ ok: boolean; plugins: PluginSummary[] }> => get('/api/plugins'),
};

// ------------------------------------------------------------------ //
// Emotion (emotion_route.py)
// ------------------------------------------------------------------ //

export interface EmotionState {
  emotion: string;
  confidence: number;
  histogram: Record<string, number>;
}

export interface EmotionEvent {
  emotion: string;
  confidence: number;
  source: string;
  timestamp: string;
}

export const emotionApi = {
  get: () => get<EmotionState>('/api/emotion'),
  getHistory: (limit = 10) =>
    get<{ events: EmotionEvent[] }>(`/api/emotion/history?limit=${limit}`),
  override: (emotion: string) => post<{ ok: boolean; emotion: string }>('/api/emotion/override', { emotion }),
  reset: () => post<{ ok: boolean; emotion: string }>('/api/emotion/reset'),
};

import { useEffect, useMemo, useRef, useState, type ReactElement } from 'react';
import { useAppState } from '@/state/AppStateContext';
import { LLMSettings } from '@/settings/LLMSettings';
import { CharacterSettings } from '@/settings/CharacterSettings';
import { PlayerPromptCard } from '@/settings/PlayerPromptCard';
import { MemorySettings } from '@/settings/MemorySettings';
import { VoiceSettingsPanel } from '@/settings/VoiceLanguageSettings';
import { ScreenAwareSettings } from '@/settings/ScreenAwareSettings';
import { ProactiveSettings } from '@/settings/ProactiveSettings';
import { TaskPlatformSettings } from '@/settings/TaskPlatformSettings';
import { EmotionDebug } from './EmotionDebug';
import { SystemInfo } from './SystemInfo';
import { Icon, type IconName } from '@/ui/icons';
import type { WSClient } from '@/api/wsClient';
import type { SettingsSyncState } from '@/hooks/useSettingsSync';

export type DashboardSection =
  | 'role'
  | 'brain'
  | 'voice'
  | 'sense'
  | 'proactive'
  | 'task'
  | 'system';

interface NavItem {
  id: DashboardSection;
  label: string;
  description: string;
  icon: IconName;
  group: string;
  keywords: string;
}

interface SettingSearchEntry {
  id: string;
  section: DashboardSection;
  category: string;
  card: string;
  label: string;
  description: string;
  keywords: string;
  target?: string;
  value?: string;
}

const LAST_SECTION_KEY = 'moonlight.settings.lastSection';

const NAV: NavItem[] = [
  { id: 'role', label: '角色', description: '角色卡、提示词与外观', icon: 'sparkles', group: '入门', keywords: '角色 persona 外观 提示词' },
  { id: 'voice', label: '回复', description: '语言、语音与字幕', icon: 'volume', group: '入门', keywords: '语音 asr tts 语言 翻译 字幕' },
  { id: 'brain', label: '模型', description: 'LLM、服务商与 API', icon: 'brain', group: '入门', keywords: 'llm 模型 服务商 api key 密钥 ollama base url' },
  { id: 'sense', label: '感知', description: '屏幕识别、隐私与记忆', icon: 'eye', group: '体验', keywords: '屏幕 感知 窗口 轮询 隐私 记忆 memory' },
  { id: 'proactive', label: '陪伴', description: '空闲搭话与话题策略', icon: 'zap', group: '体验', keywords: '主动 话题 新闻 空闲 情绪' },
  { id: 'task', label: '任务', description: '任务平台、MCP 与联网', icon: 'tool', group: '工作区', keywords: '任务 平台 mcp 工具 沙箱 联网 技能' },
  { id: 'system', label: '系统', description: '性能、隐私与开发者信息', icon: 'cpu', group: '系统', keywords: '系统 连接 状态 性能 日志 版本 开发者 隐私 重启' },
];

const SEARCH_INDEX: SettingSearchEntry[] = [
  { id: 'llm-provider', section: 'brain', category: '连接与模型', card: 'LLM 配置', label: '服务商', description: '选择 OpenAI、Claude、Gemini 或 Ollama', keywords: 'provider service', target: 'setting-llm-provider' },
  { id: 'llm-base-url', section: 'brain', category: '连接与模型', card: 'LLM 配置', label: 'Base URL', description: '模型服务接口地址', keywords: 'url endpoint', target: 'setting-llm-base-url' },
  { id: 'llm-model', section: 'brain', category: '连接与模型', card: 'LLM 配置', label: '模型', description: '当前对话模型名称', keywords: 'model deepseek gpt ollama', target: 'setting-llm-model' },
  { id: 'llm-api-key', section: 'brain', category: '连接与模型', card: 'LLM 配置', label: 'API Key', description: '服务商访问密钥（仅显示脱敏状态）', keywords: 'key api 密钥 token', target: 'setting-llm-api-key' },
  { id: 'llm-performance', section: 'brain', category: '连接与模型', card: '性能与预设', label: 'keep_alive', description: '控制 Ollama 模型是否常驻内存', keywords: '性能 预设 ollama 常驻', target: 'setting-llm-keep-alive' },
  { id: 'reply-language', section: 'voice', category: '回复方式', card: '回复语言', label: '回复语言', description: '设置文字和语音回复使用的语言', keywords: 'language 中文 English 日本語', target: 'setting-reply-language' },
  { id: 'subtitle-enabled', section: 'voice', category: '回复方式', card: '双语气泡', label: '双语气泡', description: '控制聊天气泡中的双语字幕显示', keywords: 'subtitle caption 双语', target: 'setting-subtitle-enabled' },
  { id: 'voice-translation', section: 'voice', category: '回复方式', card: '跨语音翻译', label: '语音翻译', description: '将回复翻译成角色的目标语音后再合成', keywords: 'translation DeepLX LLM voice', target: 'setting-voice-translation' },
  { id: 'tts-engine', section: 'voice', category: '回复方式', card: '语音引擎', label: 'TTS / ASR 引擎', description: '配置语音识别与语音合成引擎', keywords: 'engine VOICEVOX edge tts asr', target: 'setting-tts-engine' },
  { id: 'character-cards', section: 'role', category: '角色与外观', card: '角色卡', label: '角色卡', description: '切换、编辑或创建桌宠角色', keywords: 'persona character live2d skin', target: 'setting-character-cards' },
  { id: 'character-persona', section: 'role', category: '角色与外观', card: '角色卡编辑', label: '人设提示词', description: '定义角色的性格、表达方式和行为边界', keywords: 'persona prompt 性格', target: 'setting-character-persona' },
  { id: 'player-prompt', section: 'role', category: '角色与外观', card: '玩家提示词', label: '全局上下文', description: '告诉角色关于你的稳定信息', keywords: 'user context profile 玩家', target: 'setting-player-prompt' },
  { id: 'screen-enabled', section: 'sense', category: '屏幕感知与记忆', card: '屏幕感知', label: '启用屏幕感知', description: '允许小月读取经过隐私过滤的前台窗口', keywords: 'screen capture awareness', target: 'setting-screen-enabled' },
  { id: 'screen-on-demand', section: 'sense', category: '屏幕感知与记忆', card: '屏幕感知', label: '仅用户询问时识别', description: '关闭自动采集，只有主动询问时识别', keywords: 'on demand privacy', target: 'setting-screen-on-demand' },
  { id: 'screen-privacy', section: 'sense', category: '屏幕感知与记忆', card: '隐私与排除', label: '应用黑名单与标题关键词', description: '阻止敏感窗口截图和上传', keywords: 'privacy blacklist password payment', target: 'setting-screen-privacy' },
  { id: 'screen-schedule', section: 'sense', category: '屏幕感知与记忆', card: '采集调度', label: '采集频率与敏感度', description: '调整自动识别的轮询间隔、变化阈值和空闲降频', keywords: 'screen poll threshold idle 调度', target: 'setting-screen-schedule' },
  { id: 'memory', section: 'sense', category: '屏幕感知与记忆', card: '记忆', label: '记忆管理', description: '编辑核心画像、审核事实和整理反思', keywords: 'memory facts reflections proposals 画像', target: 'setting-memory' },
  { id: 'proactive-enabled', section: 'proactive', category: '主动陪伴', card: '主动对话', label: '启用主动对话', description: '允许小月在空闲时主动发起交流', keywords: 'proactive idle speak', target: 'setting-proactive-enabled' },
  { id: 'proactive-scan', section: 'proactive', category: '主动陪伴', card: '定时屏幕巡检', label: '屏幕巡检', description: '按周期检查新画面，再由策略决定是否搭话', keywords: 'screen proactive scan巡检', target: 'setting-proactive-scan' },
  { id: 'proactive-topics', section: 'proactive', category: '主动陪伴', card: '主动话题', label: '话题来源', description: '管理角色主动搭话时使用的主题', keywords: 'topics news 话题', target: 'setting-proactive-topics' },
  { id: 'task-network', section: 'task', category: '任务与工具', card: '网页搜索', label: '网页搜索', description: '任务智能体使用联网工具的能力', keywords: 'web search tavily ddg network', target: 'setting-task-network' },
  { id: 'task-general', section: 'task', category: '任务与工具', card: '通用', label: '任务平台', description: '任务模式开关、默认目录和执行轮数', keywords: 'task platform root directory', target: 'setting-task-general' },
  { id: 'task-sandbox', section: 'task', category: '任务与工具', card: '沙箱限制', label: '沙箱限制', description: '控制任务工具的超时、输出和文件读写上限', keywords: 'sandbox bash timeout file limit', target: 'setting-task-sandbox' },
  { id: 'task-mcp', section: 'task', category: '任务与工具', card: 'MCP 服务器', label: 'MCP 服务器', description: '添加、测试和管理外部工具服务', keywords: 'mcp server tools stdio sse', target: 'setting-task-mcp' },
  { id: 'system-restart', section: 'system', category: '系统与诊断', card: '连接状态', label: '重启后端', description: '部分模型和任务配置需要重启后端生效', keywords: 'restart backend reload', target: 'setting-system' },
  { id: 'system-errors', section: 'system', category: '系统与诊断', card: '最近错误', label: '最近错误', description: '查看运行异常并清除已处理的问题', keywords: 'error exception diagnosis 日志', target: 'setting-system-errors' },
];

const LEGACY: Record<string, DashboardSection> = {
  llm: 'brain', general: 'role', memory: 'sense', screen: 'sense', agent: 'task', voice: 'voice', proactive: 'proactive',
};

function normalizeSection(s: string | undefined): DashboardSection {
  if (s && LEGACY[s]) return LEGACY[s];
  return NAV.some((n) => n.id === s) ? (s as DashboardSection) : 'role';
}

export interface DashboardProps {
  onClose: () => void;
  initialSection?: DashboardSection;
  ws?: () => WSClient | null;
  settingsSync?: SettingsSyncState;
}

export function Dashboard({ onClose, initialSection, ws, settingsSync }: DashboardProps): ReactElement {
  const { state, dispatch } = useAppState();
  const [section, setSection] = useState<DashboardSection>(() => {
    try {
      const saved = localStorage.getItem(LAST_SECTION_KEY) ?? undefined;
      return normalizeSection(initialSection ?? saved);
    } catch {
      return normalizeSection(initialSection);
    }
  });
  const [query, setQuery] = useState('');
  const [devMode, setDevMode] = useState<boolean>(() => {
    try { return localStorage.getItem('moonlight.devmode') === '1'; } catch { return false; }
  });
  const [focusTarget, setFocusTarget] = useState<string | null>(null);
  const contentRef = useRef<HTMLElement | null>(null);

  useEffect(() => {
    try { localStorage.setItem(LAST_SECTION_KEY, section); } catch { /* storage unavailable */ }
  }, [section]);

  useEffect(() => {
    if (!focusTarget) return;
    const timer = window.setTimeout(() => {
      const target = document.querySelector<HTMLElement>(`[data-setting-key="${focusTarget}"]`);
      const details = target?.closest('details');
      if (details) details.open = true;
      target?.scrollIntoView({ behavior: 'smooth', block: 'center' });
      target?.focus({ preventScroll: true });
      setFocusTarget(null);
    }, 80);
    return () => window.clearTimeout(timer);
  }, [focusTarget, section]);

  const toggleDevMode = (): void => {
    const next = !devMode;
    setDevMode(next);
    try { localStorage.setItem('moonlight.devmode', next ? '1' : '0'); } catch { /* ignore */ }
  };

  const toggleTheme = (): void => {
    dispatch({ type: 'SET_THEME', theme: state.theme === 'dark' ? 'light' : 'dark' });
  };

  const normalizedQuery = query.trim().toLowerCase();
  const searchResults = useMemo(() => {
    if (!normalizedQuery) return [];
    return SEARCH_INDEX.filter((item) =>
      `${item.category} ${item.card} ${item.label} ${item.description} ${item.keywords}`.toLowerCase().includes(normalizedQuery),
    );
  }, [normalizedQuery]);
  const current = NAV.find((n) => n.id === section) ?? NAV[0];
  const syncLabel: Record<string, string> = { clean: '已同步', dirty: '有待保存修改', saving: '保存中…', saved: '已保存', error: '保存失败' };
  const sectionSummary: Record<DashboardSection, string> = {
    role: '当前角色与外观正在运行中',
    voice: '语言、字幕和语音链路',
    brain: '对话模型连接配置',
    sense: state.settings.screenAwareEnabled ? '屏幕感知已启用' : '屏幕感知未启用',
    proactive: state.settings.proactiveEnabled ? '主动陪伴已启用' : '主动陪伴未启用',
    task: '任务平台与外部工具能力',
    system: state.connStatus === 'connected' ? '后端连接正常' : '后端连接需要检查',
  };
  const selectSearchResult = (result: SettingSearchEntry): void => {
    setSection(result.section);
    setFocusTarget(result.target ?? null);
    setQuery('');
  };

  return (
    <div className="dashboard" data-testid="settings-shell" aria-label="Moonlight 设置中心">
      <header className="dashboard-header">
        <div className="dashboard-brand">
          <span className="dashboard-brand-mark"><Icon name="moon" size={18} /></span>
          <span className="dashboard-brand-name">Moonlight</span>
          <span className="dashboard-brand-sub">设置中心</span>
        </div>
        <label className="set-search">
          <Icon name="search" size={13} />
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="搜索设置、字段或功能"
            aria-label="搜索设置项"
          />
          {query && <button type="button" className="set-search-clear" onClick={() => setQuery('')} aria-label="清除搜索">×</button>}
        </label>
        <div className="dashboard-header-actions">
          <span className={`settings-sync-pill ${settingsSync?.status ?? 'clean'}`} role="status">
            <span className="settings-sync-dot" aria-hidden />{syncLabel[settingsSync?.status ?? 'clean']}
          </span>
          <div className="dashboard-header-status"><span className="conn-dot" data-status={state.connStatus} /><span>{state.confName || '未连接'}</span></div>
          <button className="dashboard-theme-toggle" onClick={toggleTheme} title={state.theme === 'dark' ? '切换到浅色模式' : '切换到深色模式'} aria-label={state.theme === 'dark' ? '切换到浅色模式' : '切换到深色模式'}><Icon name={state.theme === 'dark' ? 'sun' : 'moon'} size={16} /></button>
          <button className="dashboard-close" onClick={onClose} title="关闭设置" aria-label="关闭设置"><Icon name="x" size={16} /></button>
        </div>
      </header>

      <div className="dashboard-body">
        <nav className="dashboard-sidebar" aria-label="设置分类" data-testid="settings-navigation">
          {NAV.map((item) => (
            <button
              key={item.id}
              className={`dashboard-nav-item ${item.id === 'task' ? 'task-active' : ''} ${section === item.id ? 'active' : ''}`}
              onClick={() => setSection(item.id)}
              title={item.description}
              aria-label={item.label}
              aria-current={section === item.id ? 'page' : undefined}
            >
              <span className="dashboard-nav-icon"><Icon name={item.icon} size={18} /></span>
              <span className="dashboard-nav-label">{item.label}</span>
            </button>
          ))}
        </nav>

        <main className="dashboard-content" ref={contentRef}>
          {normalizedQuery ? (
            <div className="settings-search-view" data-testid="settings-search-results">
              <div className="dashboard-section-head"><div><h2>搜索设置</h2><p className="dashboard-section-description">匹配“{query}”的字段与功能</p></div></div>
              {searchResults.length > 0 ? (
                <div className="settings-search-results">{searchResults.map((result) => (
                  <button className="settings-search-result" key={result.id} onClick={() => selectSearchResult(result)}>
                    <span className="settings-search-result-icon"><Icon name="search" size={15} /></span>
                    <span className="settings-search-result-copy"><strong>{result.label}</strong><span>{result.description}</span></span>
                    <span className="settings-search-result-meta">{result.category} · {result.card}</span>
                    <Icon name="chevronDown" size={14} />
                  </button>
                ))}</div>
              ) : (
                <div className="settings-search-empty"><Icon name="search" size={24} /><strong>没有找到匹配设置</strong><span>可以尝试搜索“屏幕”“语音”“API Key”或“重启”。</span><button className="btn btn-primary" onClick={() => setQuery('')}>返回设置</button></div>
              )}
            </div>
          ) : (
            <>
              <div className="dashboard-section-head">
                <div>
                  {section !== 'role' && section !== 'voice' && <div className="dashboard-section-kicker">{current.group} / 设置</div>}
                  <h2>
                    {current.label}
                    {section !== 'role' && section !== 'voice' && <span className="dashboard-section-summary-inline">{sectionSummary[section]}</span>}
                  </h2>
                </div>
                <div className="dashboard-section-head-status">
                  <span className={`dashboard-section-state ${state.connStatus === 'connected' ? 'ok' : state.connStatus === 'disconnected' ? 'err' : ''}`} title={`连接状态：${state.connStatus}`} />
                  {settingsSync?.status === 'error' && <button className="settings-retry-btn" type="button" onClick={settingsSync.retry}>重试保存</button>}
                </div>
              </div>
              {settingsSync?.error && <div className="settings-sync-error" role="alert">{settingsSync.error}</div>}
              <div className={`dashboard-section dashboard-section-${section}`} data-testid={`settings-section-${section}`} aria-label={`${current.label}设置`}>
                {section === 'role' && <><CharacterSettings ws={ws} /><PlayerPromptCard /></>}
                {section === 'brain' && <LLMSettings />}
                {section === 'voice' && <VoiceSettingsPanel />}
                {section === 'sense' && <><ScreenAwareSettings /><details open className="settings-advanced-panel"><summary><span>记忆管理</span><small>核心画像、事实、反思与整理策略</small></summary><MemorySettings confUid={state.confUid} /></details></>}
                {section === 'proactive' && <ProactiveSettings />}
                {section === 'task' && <TaskPlatformSettings />}
                {section === 'system' && <><div className="settings-card" data-setting-key="setting-system"><div className="settings-card-body"><label className="toggle-row"><span>开发者模式</span><input type="checkbox" className="switch" checked={devMode} onChange={toggleDevMode} /></label></div></div><SystemInfo />{devMode && <EmotionDebug />}</>}
              </div>
            </>
          )}
        </main>
      </div>
      {settingsSync && (
        <footer className={`settings-save-bar ${settingsSync.status}`} data-testid="settings-save-bar" role="status" aria-live="polite">
          <span className="settings-save-bar-message">
            <span className="settings-sync-dot" aria-hidden />
            {settingsSync.error || syncLabel[settingsSync.status]}
          </span>
          {settingsSync.status === 'error' && <button type="button" className="settings-retry-btn" onClick={settingsSync.retry}>重试保存</button>}
          <span className="settings-save-bar-note">设置会自动同步到后端</span>
        </footer>
      )}
    </div>
  );
}

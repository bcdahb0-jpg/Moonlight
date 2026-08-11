import { useEffect, useState, type ReactElement } from 'react';
import { useAppState } from '@/state/AppStateContext';
import { LLMSettings } from '@/settings/LLMSettings';
import { CharacterSettings } from '@/settings/CharacterSettings';
import { PlayerPromptCard } from '@/settings/PlayerPromptCard';
import { MemorySettings } from '@/settings/MemorySettings';
import { PerfSettings } from '@/settings/PerfSettings';
import { VoiceLanguageSettings } from '@/settings/VoiceLanguageSettings';
import { ScreenAwareSettings } from '@/settings/ScreenAwareSettings';
import { ProactiveSettings } from '@/settings/ProactiveSettings';
import { TaskPlatformSettings } from '@/settings/TaskPlatformSettings';
import { EmotionDebug } from './EmotionDebug';
import { SystemInfo } from './SystemInfo';
import { Icon, type IconName } from '@/ui/icons';
import type { WSClient } from '@/api/wsClient';

/** 扁平分区：7 个，无分组、无描述（重设计 v4）。 */
type DashboardSection = 'role' | 'brain' | 'voice' | 'sense' | 'proactive' | 'task' | 'system';

export type { DashboardSection };

interface NavItem {
  id: DashboardSection;
  label: string;
  icon: IconName;
  /** 搜索关键词（label 之外的别名，如 key/密钥 → brain）。 */
  keywords: string;
}

const LAST_SECTION_KEY = 'moonlight.settings.lastSection';

const NAV: NavItem[] = [
  { id: 'role', label: '角色', icon: 'sparkles', keywords: '角色卡 人设 persona 音色 模型 提示词' },
  { id: 'brain', label: '大脑', icon: 'brain', keywords: 'llm 模型 服务商 key 密钥 ollama 预设' },
  { id: 'voice', label: '语音', icon: 'volume', keywords: 'asr tts 引擎 音色 语言 翻译 字幕' },
  { id: 'sense', label: '感知', icon: 'eye', keywords: '屏幕 窗口 轮询 记忆 memory 画像 检索' },
  { id: 'proactive', label: '主动', icon: 'zap', keywords: '话题 搭话 新闻 空闲 情绪' },
  { id: 'task', label: '任务', icon: 'tool', keywords: '平台 mcp 工具 沙箱 联网 技能' },
  { id: 'system', label: '系统', icon: 'cpu', keywords: '连接 状态 开发者 错误 日志 版本' },
];

/** 旧分区名 → 新分区（错误修复卡仍传旧值）。 */
const LEGACY: Record<string, DashboardSection> = {
  llm: 'brain',
  general: 'role',
  memory: 'sense',
  screen: 'sense',
  agent: 'task',
  voice: 'voice',
  proactive: 'proactive',
};

function normalizeSection(s: string | undefined): DashboardSection {
  if (s && LEGACY[s]) return LEGACY[s];
  return NAV.some((n) => n.id === s) ? (s as DashboardSection) : 'role';
}

export interface DashboardProps {
  onClose: () => void;
  /** 打开时定位到的分区（错误修复卡跳转）；省略则记住上次分区。 */
  initialSection?: DashboardSection;
  /** 当前 WS 连接（角色卡切换 / 立即生效用）。 */
  ws?: () => WSClient | null;
}

/**
 * Full-window configuration dashboard（重设计 v4）：
 * 纯色面板 + 扁平 icon rail（7 分区，无分组无描述）+ 顶部搜索 + 内容区。
 * 开发者模式开关已移入「系统」页；主题切换在 Header 右上角。
 */
export function Dashboard({ onClose, initialSection, ws }: DashboardProps): ReactElement {
  const { state, dispatch } = useAppState();
  const [section, setSection] = useState<DashboardSection>(() => {
    try {
      const saved = localStorage.getItem(LAST_SECTION_KEY) ?? undefined;
      if (initialSection) return normalizeSection(initialSection);
      return normalizeSection(saved);
    } catch {
      return 'role';
    }
  });
  const [query, setQuery] = useState('');
  const [devMode, setDevMode] = useState<boolean>(() => {
    try {
      return localStorage.getItem('moonlight.devmode') === '1';
    } catch {
      return false;
    }
  });

  // 记住上次分区（S6：替代每次默认回角色卡）
  useEffect(() => {
    try {
      localStorage.setItem(LAST_SECTION_KEY, section);
    } catch {
      // storage 不可用时静默
    }
  }, [section]);

  const toggleDevMode = (): void => {
    const next = !devMode;
    setDevMode(next);
    try {
      localStorage.setItem('moonlight.devmode', next ? '1' : '0');
    } catch {
      // storage may be unavailable; ignore
    }
  };

  const toggleTheme = (): void => {
    dispatch({ type: 'SET_THEME', theme: state.theme === 'dark' ? 'light' : 'dark' });
  };

  const q = query.trim().toLowerCase();
  const visibleNav = NAV.filter((n) => !q || (n.label + n.keywords).toLowerCase().includes(q));
  const current = NAV.find((n) => n.id === section);

  return (
    <div className="dashboard">
      {/* 头部：品牌 + 搜索 + 状态 + 主题 + 关闭 */}
      <header className="dashboard-header">
        <div className="dashboard-brand">
          <span className="dashboard-brand-mark">
            <Icon name="moon" size={18} />
          </span>
          <span className="dashboard-brand-name">Moonlight</span>
          <span className="dashboard-brand-sub">设置</span>
        </div>
        <label className="set-search">
          <Icon name="search" size={13} />
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="搜索设置项"
            aria-label="搜索设置项"
          />
        </label>
        <div className="dashboard-header-actions">
          <div className="dashboard-header-status">
            <span className="conn-dot" data-status={state.connStatus} />
            <span>{state.confName || '未连接'}</span>
          </div>
          <button
            className="dashboard-theme-toggle"
            onClick={toggleTheme}
            title={state.theme === 'dark' ? '切换到浅色模式' : '切换到深色模式'}
            aria-label={state.theme === 'dark' ? '切换到浅色模式' : '切换到深色模式'}
          >
            <Icon name={state.theme === 'dark' ? 'sun' : 'moon'} size={16} />
          </button>
          <button className="dashboard-close" onClick={onClose} title="关闭设置">
            <Icon name="x" size={16} />
          </button>
        </div>
      </header>

      <div className="dashboard-body">
        {/* 扁平 icon rail */}
        <nav className="dashboard-sidebar">
          {visibleNav.map((n) => (
            <button
              key={n.id}
              className={`dashboard-nav-item ${n.id === 'task' ? 'task-active' : ''} ${section === n.id ? 'active' : ''}`}
              onClick={() => setSection(n.id)}
              title={n.label}
            >
              <span className="dashboard-nav-icon">
                <Icon name={n.icon} size={18} />
              </span>
              <span className="dashboard-nav-label">{n.label}</span>
            </button>
          ))}
        </nav>

        {/* 内容区 */}
        <main className="dashboard-content">
          {current && (
            <div className="dashboard-section-head">
              <h2>{current.label}</h2>
              {section === 'system' && (
                <span className={`dashboard-section-state ${state.connStatus === 'connected' ? 'ok' : state.connStatus === 'disconnected' ? 'err' : ''}`} />
              )}
            </div>
          )}
          <div className="dashboard-section">
            {section === 'role' && (
              <>
                <CharacterSettings ws={ws} />
                <PlayerPromptCard />
              </>
            )}
            {section === 'brain' && <LLMSettings />}
            {section === 'voice' && (
              <>
                <VoiceLanguageSettings />
                <PerfSettings />
              </>
            )}
            {section === 'sense' && (
              <>
                <ScreenAwareSettings />
                <MemorySettings confUid={state.confUid} />
              </>
            )}
            {section === 'proactive' && <ProactiveSettings />}
            {section === 'task' && <TaskPlatformSettings />}
            {section === 'system' && (
              <>
                <div className="settings-card">
                  <div className="settings-card-body">
                    <label className="toggle-row">
                      <span>开发者模式</span>
                      <input type="checkbox" className="switch" checked={devMode} onChange={toggleDevMode} />
                    </label>
                  </div>
                </div>
                <SystemInfo />
                {devMode && <EmotionDebug />}
              </>
            )}
          </div>
        </main>
      </div>
    </div>
  );
}

import { useState, type ReactElement } from 'react';
import { useAppState } from '@/state/AppStateContext';
import { LLMSettings } from '@/settings/LLMSettings';
import { CharacterSettings } from '@/settings/CharacterSettings';
import { MemorySettings } from '@/settings/MemorySettings';
import { PerfSettings } from '@/settings/PerfSettings';
import { ScreenAwareSettings } from '@/settings/ScreenAwareSettings';
import { ProactiveSettings } from '@/settings/ProactiveSettings';
import { GeneralSettings } from './GeneralSettings';
import { AgentSettings } from './AgentSettings';
import { EmotionDebug } from './EmotionDebug';
import { SystemInfo } from './SystemInfo';
import { Icon, type IconName } from '@/ui/icons';
import type { WSClient } from '@/api/wsClient';

type DashboardSection =
  | 'general'
  | 'llm'
  | 'agent'
  | 'character'
  | 'voice'
  | 'memory'
  | 'screen'
  | 'proactive'
  | 'emotion'
  | 'system';

export type { DashboardSection };

interface NavItem {
  id: DashboardSection;
  label: string;
  desc: string;
  icon: IconName;
}

interface NavGroup {
  id: string;
  label: string;
  desc: string;
  icon: IconName;
  /** 仅开发者模式可见的分组。 */
  devOnly?: boolean;
  items: NavItem[];
}

const DEV_MODE_KEY = 'moonlight.devmode';

/**
 * 扁平化分组导航：
 * 5 个平级业务分组——角色 / 对话 / 声音 / 感知 / 系统（系统需开发者模式）。
 * 外观切换收敛到 Header 右上角主题图标，不再占「通用」页空间。
 */
const GROUPS: NavGroup[] = [
  {
    id: 'character',
    label: '角色',
    desc: '她是谁、长什么样',
    icon: 'sparkles',
    items: [
      { id: 'character', label: '角色卡', desc: '形象 · 人设 · 音色', icon: 'sparkles' },
      { id: 'general', label: '通用', desc: '语言 · 翻译 · 提示词', icon: 'palette' },
    ],
  },
  {
    id: 'chat',
    label: '对话',
    desc: '她的大脑与说话方式',
    icon: 'message',
    items: [
      { id: 'llm', label: '对话大脑 LLM', desc: '模型 · 接口 · 密钥', icon: 'brain' },
      { id: 'proactive', label: '主动话题', desc: '搭话频率 · 话题库', icon: 'zap' },
      { id: 'emotion', label: '情绪反馈', desc: '情绪调试 · 直方图', icon: 'face' },
    ],
  },
  {
    id: 'voice',
    label: '语音',
    desc: 'ASR 识别与 TTS 合成',
    icon: 'volume',
    items: [
      { id: 'voice', label: 'ASR / TTS', desc: '引擎 · 预设 · 性能', icon: 'volume' },
    ],
  },
  {
    id: 'sense',
    label: '感知',
    desc: '她如何理解你与屏幕',
    icon: 'eye',
    items: [
      { id: 'screen', label: '屏幕感知', desc: '活动窗口轮询', icon: 'monitor' },
      { id: 'memory', label: '记忆', desc: '画像 · 事实 · 检索 · 策略', icon: 'database' },
    ],
  },
  {
    id: 'system',
    label: '系统',
    desc: '开发者工具与运行状态',
    icon: 'cpu',
    devOnly: true,
    items: [
      { id: 'agent', label: '工具 / MCP', desc: 'Agent 工具调用', icon: 'tool' },
      { id: 'system', label: '系统信息', desc: '连接 · 角色 · 错误', icon: 'info' },
    ],
  },
];

const SECTION_META: Record<DashboardSection, { label: string; desc: string }> = Object.fromEntries(
  GROUPS.flatMap((g) => g.items.map((it) => [it.id, { label: it.label, desc: it.desc }])),
) as Record<DashboardSection, { label: string; desc: string }>;

export interface DashboardProps {
  onClose: () => void;
  /** 打开时定位到的分区（如错误修复卡跳转 LLM）；省略则默认「角色卡」。 */
  initialSection?: DashboardSection;
  /** 当前 WS 连接（角色卡切换 / 立即生效用）。 */
  ws?: () => WSClient | null;
}

/**
 * Full-window configuration dashboard. 玻璃质感头部（右上角主题切换）+ 扁平分组侧边栏 + 内容区。
 * Sections read `useAppState()` directly and call the backend REST API (src/api/rest.ts).
 */
export function Dashboard({ onClose, initialSection, ws }: DashboardProps): ReactElement {
  const { state, dispatch } = useAppState();
  const [section, setSection] = useState<DashboardSection>(initialSection ?? 'character');
  const [devMode, setDevMode] = useState<boolean>(() => {
    try {
      return localStorage.getItem(DEV_MODE_KEY) === '1';
    } catch {
      return false;
    }
  });

  const toggleDevMode = (): void => {
    const next = !devMode;
    setDevMode(next);
    try {
      localStorage.setItem(DEV_MODE_KEY, next ? '1' : '0');
    } catch {
      // storage may be unavailable; ignore
    }
    // 关闭开发者模式时，若当前停在开发者分区则回到默认分区。
    if (!next && (section === 'agent' || section === 'system')) {
      setSection('character');
    }
  };

  const toggleTheme = (): void => {
    dispatch({ type: 'SET_THEME', theme: state.theme === 'dark' ? 'light' : 'dark' });
  };

  const visibleGroups = GROUPS.filter((g) => !g.devOnly || devMode);
  const meta = SECTION_META[section];

  return (
    <div className="dashboard">
      {/* 玻璃头部 */}
      <header className="dashboard-header">
        <div className="dashboard-brand">
          <span className="dashboard-brand-mark">
            <Icon name="moon" size={18} />
          </span>
          <span className="dashboard-brand-name">Moonlight</span>
          <span className="dashboard-brand-sub">AI 桌宠设置</span>
        </div>
        <div className="dashboard-header-actions">
          <div className="dashboard-header-status">
            <span className="conn-dot" data-status={state.connStatus} />
            <span>{state.confName || '未连接'}</span>
          </div>
          {/* 主题切换：一个图标即完成深浅色切换 */}
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
        {/* 扁平分组侧边栏 */}
        <nav className="dashboard-sidebar">
          {visibleGroups.map((group) => (
            <div key={group.id} className="dashboard-group">
              <div className="dashboard-group-head">
                <Icon name={group.icon} size={14} />
                <span>{group.label}</span>
              </div>
              <div className="dashboard-group-desc">{group.desc}</div>
              {group.items.map((item) => (
                <button
                  key={item.id}
                  className={`dashboard-nav-item ${section === item.id ? 'active' : ''}`}
                  onClick={() => setSection(item.id)}
                >
                  <span className="dashboard-nav-icon">
                    <Icon name={item.icon} size={16} />
                  </span>
                  <span className="dashboard-nav-label">{item.label}</span>
                </button>
              ))}
            </div>
          ))}

          <div className="dashboard-dev-toggle">
            <label className="switch-row">
              <span className="switch-label">
                <Icon name="tool" size={14} />
                开发者模式
              </span>
              <input
                type="checkbox"
                className="switch"
                checked={devMode}
                onChange={toggleDevMode}
              />
              <span className="switch-ui" aria-hidden="true" />
            </label>
            <span className="dashboard-dev-hint">工具 MCP / 系统信息</span>
          </div>
        </nav>

        {/* 内容区 */}
        <main className="dashboard-content">
          <div className="dashboard-section-head">
            <h2>{meta.label}</h2>
            <p>{meta.desc}</p>
          </div>
          <div className="dashboard-section">
            {section === 'general' && <GeneralSettings />}
            {section === 'llm' && <LLMSettings />}
            {section === 'agent' && <AgentSettings />}
            {section === 'character' && <CharacterSettings ws={ws} />}
            {section === 'voice' && <PerfSettings />}
            {section === 'memory' && <MemorySettings confUid={state.confUid} />}
            {section === 'screen' && <ScreenAwareSettings />}
            {section === 'proactive' && <ProactiveSettings />}
            {section === 'emotion' && <EmotionDebug />}
            {section === 'system' && <SystemInfo />}
          </div>
        </main>
      </div>
    </div>
  );
}

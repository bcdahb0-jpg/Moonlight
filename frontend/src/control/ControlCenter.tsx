import { useCallback, useEffect, useMemo, useState } from 'react';
import type { ReactElement } from 'react';
import { Icon, type IconName } from '@/ui/icons';
import type { WSClient } from '@/api/wsClient';
import type { SettingsSyncState } from '@/hooks/useSettingsSync';
import { consoleApi, screenApi, type ConsoleOverview, type ScreenMetricsPayload } from '@/api/rest';
import { CharacterSettings } from '@/settings/CharacterSettings';
import { PlayerPromptCard } from '@/settings/PlayerPromptCard';
import { LLMSettings } from '@/settings/LLMSettings';
import { MemorySettings } from '@/settings/MemorySettings';
import { VoiceSettingsPanel } from '@/settings/VoiceLanguageSettings';
import { ScreenAwareSettings } from '@/settings/ScreenAwareSettings';
import { ProactiveSettings } from '@/settings/ProactiveSettings';
import { TaskPlatformSettings } from '@/settings/TaskPlatformSettings';
import { Live2DAppearanceSettings } from '@/settings/Live2DAppearanceSettings';
import { MultiModelSettings } from '@/settings/MultiModelSettings';
import { ExpressionSettings } from '@/settings/ExpressionSettings';
import { EmotionStateMachine } from '@/settings/EmotionStateMachine';
import { ConversationStateMachine } from '@/settings/ConversationStateMachine';
import { OcclusionEditor } from '@/settings/OcclusionEditor';
import { MotionPreviewSettings } from '@/settings/MotionPreviewSettings';
import { SingingRequestSettings, SingingQueueSettings, SingingEngineSettings } from '@/settings/SingingSettings';
import { LiveBiliSettings, LiveDanmakuSettings, LiveObsSettings } from '@/settings/LiveSettings';
import { PlaymateGameSettings, PlaymateVisionSettings, PlaymateKbSettings, PlaymateCheerSettings } from '@/settings/PlaymateSettings';
import { PluginManagerSettings } from '@/settings/PluginManagerSettings';
import { SkillMarketplaceSettings } from '@/settings/SkillMarketplaceSettings';
import { ExportImportSettings } from '@/settings/ExportImportSettings';
import { IntentSettings } from '@/settings/IntentSettings';
import { MultimodalInputSettings } from '@/settings/MultimodalInputSettings';
import { SkillLibrarySettings } from '@/settings/SkillLibrarySettings';
import { QqConnectorSettings } from '@/settings/QqConnectorSettings';
import { EmotionDebug } from '@/dashboard/EmotionDebug';
import { SystemInfo } from '@/dashboard/SystemInfo';
import { CONTROL_SECTIONS } from './controlData';
import type {
  ControlCard,
  ControlComponentKey,
  ControlField,
  ControlSection,
} from './controlData';
import './ControlCenter.css';

/* ============================== 小部件 ============================== */

/** 展位交互：所有按钮点击仅给视觉反馈，无实际行为 */
function useShowcaseClick(): (e: React.MouseEvent) => void {
  return (e) => {
    const el = e.currentTarget as HTMLElement;
    el.classList.remove('cc-ripple');
    void el.offsetWidth;
    el.classList.add('cc-ripple');
  };
}

function RenderControl({
  field,
  onFieldClick,
}: {
  field: ControlField;
  onFieldClick: (fieldId: string, e: React.MouseEvent) => void;
}): ReactElement {
  const className = `cc-ctrl cc-ctrl-${field.type}`;
  const handle = (e: React.MouseEvent): void => onFieldClick(field.id, e);

  switch (field.type) {
    case 'toggle':
      return (
        <button
          type="button"
          className={`${className} cc-toggle${field.value ? ' on' : ''}`}
          role="switch"
          aria-checked={field.value}
          onClick={handle}
          tabIndex={0}
        >
          <span className="cc-toggle-track">
            <span className="cc-toggle-thumb" />
          </span>
          <span className="cc-toggle-text">{field.value ? '开' : '关'}</span>
        </button>
      );

    case 'select':
      return (
        <button type="button" className={`${className} cc-select`} onClick={handle}>
          <span>{field.textValue ?? field.options?.[0] ?? '—'}</span>
          <svg viewBox="0 0 16 16" width="14" height="14" aria-hidden>
            <path d="M4 6l4 4 4-4" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
        </button>
      );

    case 'text':
      return (
        <div className={`${className} cc-text`}>
          <span className="cc-text-value">{field.textValue ?? ''}</span>
          {field.badge && <span className="cc-mini-badge">{field.badge}</span>}
        </div>
      );

    case 'slider':
      return (
        <div className={`${className} cc-slider`}>
          <div className="cc-slider-track">
            <div
              className="cc-slider-fill"
              style={{ width: `${((field.valueNum ?? 0) / (field.max ?? 1)) * 100}%` }}
            />
            <span className="cc-slider-thumb" style={{ left: `${((field.valueNum ?? 0) / (field.max ?? 1)) * 100}%` }} />
          </div>
          <span className="cc-slider-value">
            {field.valueNum}
            {field.suffix ?? ''}
          </span>
        </div>
      );

    case 'progress':
      return (
        <div className={`${className} cc-progress`}>
          <div className="cc-progress-track">
            <div
              className={`cc-progress-fill ${field.tone ?? 'neutral'}`}
              style={{ width: `${((field.valueNum ?? 0) / (field.max ?? 100)) * 100}%` }}
            />
          </div>
          <span className="cc-progress-value">
            {field.valueNum}
            {field.suffix ?? ''}
          </span>
        </div>
      );

    case 'stat':
      return (
        <div className={`${className} cc-stat`}>
          <span className={`cc-stat-value ${field.tone ?? 'neutral'}`}>{field.textValue ?? '—'}</span>
          {field.badge && <span className="cc-mini-badge">{field.badge}</span>}
        </div>
      );

    case 'tags':
      return (
        <div className={`${className} cc-tags`}>
          {(field.tags ?? []).map((tag) => (
            <span className="cc-tag" key={tag}>
              {tag}
            </span>
          ))}
        </div>
      );

    case 'button':
      return (
        <div className={`${className} cc-btns`}>
          {(field.actions ?? []).map((action) => (
            <button type="button" className="cc-btn" key={action} onClick={handle}>
              {action}
            </button>
          ))}
        </div>
      );

    default:
      return <span className="cc-ctrl-unknown">—</span>;
  }
}

/* ============================== 首页快捷入口 ============================== */

const QUICK_ENTRIES: Array<{ id: string; label: string }> = [
  { id: 'hq-voice', label: '换声音' },
  { id: 'hq-role', label: '换形象' },
  { id: 'hq-memory', label: '管理记忆' },
  { id: 'hq-live', label: '打开直播' },
  { id: 'hq-sing', label: '点歌唱曲' },
  { id: 'hq-task', label: '任务工作区' },
];

function HomeQuickGrid({
  onNavigate,
}: {
  onNavigate: (fieldId: string, e: React.MouseEvent) => void;
}): ReactElement {
  return (
    <div className="cc-quick-grid">
      {QUICK_ENTRIES.map((e) => (
        <button type="button" key={e.id} className="cc-quick-item" onClick={(ev) => onNavigate(e.id, ev)}>
          <span className="cc-quick-dot" />
          <span>{e.label}</span>
          <span className="cc-quick-arrow">›</span>
        </button>
      ))}
    </div>
  );
}

/* ============================== 实时数据映射 ============================== */

/** system 分区「性能」卡：CPU/内存来自 psutil 采样。 */
function applyPerfField(field: ControlField, ov: ConsoleOverview | null): ControlField {
  if (!ov) return field;
  switch (field.id) {
    case 'sp-cpu': {
      const v = ov.system.cpu_percent;
      return v == null
        ? { ...field, valueNum: 0, tone: 'neutral' }
        : { ...field, valueNum: v, tone: v >= 85 ? 'warn' : 'ok' };
    }
    case 'sp-mem': {
      const v = ov.system.mem_percent;
      return v == null
        ? { ...field, valueNum: 0, tone: 'neutral' }
        : { ...field, valueNum: v, tone: v >= 90 ? 'warn' : 'ok' };
    }
    case 'sp-gpu':
      return { ...field, valueNum: 0, tone: 'neutral', badge: '仅 GPU 引擎' };
    case 'sp-latency':
      return { ...field, textValue: '—', tone: 'neutral', badge: '待接入' };
    default:
      return field;
  }
}

/** sense 分区「屏幕回看」卡：识别统计 / 成本来自 screen metrics。 */
function applyHistoryField(field: ControlField, metrics: ScreenMetricsPayload | null): ControlField {
  if (!metrics) return field;
  switch (field.id) {
    case 'sh-recent':
      return { ...field, textValue: `${metrics.analyze_count ?? 0} 次`, tone: 'ok', badge: `成功 ${metrics.analyze_count - (metrics.analyze_errors ?? 0)}` };
    case 'sh-cost':
      return metrics.estimated_cost_usd != null
        ? { ...field, textValue: `$${metrics.estimated_cost_usd.toFixed(4)}`, tone: 'neutral' }
        : { ...field, textValue: '—', tone: 'neutral' };
    default:
      return field;
  }
}

/** 顶栏状态灯：后端 / 前端 / 语音引擎 / 翻译。 */
function statusLights(ov: ConsoleOverview | null): Array<{ key: string; label: string; on: boolean }> {
  if (!ov) {
    return [
      { key: 'be', label: '后端', on: true },
      { key: 'fe', label: '前端', on: false },
      { key: 'voice', label: '语音引擎', on: false },
      { key: 'translate', label: '翻译', on: false },
    ];
  }
  return [
    { key: 'be', label: '后端', on: true },
    { key: 'fe', label: '前端', on: ov.system.frontend_online },
    { key: 'voice', label: '语音引擎', on: ov.engines.voicevox.running },
    { key: 'translate', label: '翻译', on: ov.engines.deeplx.running },
  ];
}

/* ============================== 真实功能组件映射 ============================== */

interface ComponentContext {
  ws?: () => WSClient | null;
  settingsSync?: SettingsSyncState;
  confUid?: string;
}

const COMPONENT_MAP: Record<ControlComponentKey, (ctx: ComponentContext) => ReactElement> = {
  character: (ctx) => <CharacterSettings ws={ctx.ws} />,
  playerPrompt: () => <PlayerPromptCard />,
  llm: () => <LLMSettings />,
  voicePanel: () => <VoiceSettingsPanel />,
  screen: () => <ScreenAwareSettings />,
  memory: (ctx) => <MemorySettings confUid={ctx.confUid ?? 'default_001'} />,
  proactive: () => <ProactiveSettings />,
  task: () => <TaskPlatformSettings />,
  systemInfo: () => <SystemInfo />,
  emotionDebug: () => <EmotionDebug />,
  live2dAppearance: () => <Live2DAppearanceSettings />,
  multimodel: () => <MultiModelSettings />,
  expression: () => <ExpressionSettings />,
  emotionMachine: () => <EmotionStateMachine />,
  conversationStateMachine: (ctx) => <ConversationStateMachine ws={ctx.ws} />,
  occlusionEditor: () => <OcclusionEditor />,
  motionPreview: () => <MotionPreviewSettings />,
  singingRequest: () => <SingingRequestSettings />,
  singingQueue: () => <SingingQueueSettings />,
  singingEngine: () => <SingingEngineSettings />,
  liveBili: () => <LiveBiliSettings />,
  liveDanmaku: () => <LiveDanmakuSettings />,
  liveObs: () => <LiveObsSettings />,
  playmateGame: () => <PlaymateGameSettings />,
  playmateVision: () => <PlaymateVisionSettings />,
  playmateKb: () => <PlaymateKbSettings />,
  playmateCheer: () => <PlaymateCheerSettings />,
  pluginManager: () => <PluginManagerSettings />,
  skillMarketplace: () => <SkillMarketplaceSettings />,
  exportImport: () => <ExportImportSettings />,
  intentSettings: () => <IntentSettings />,
  multimodalInput: () => <MultimodalInputSettings />,
  skillLibrary: () => <SkillLibrarySettings />,
  qqConnector: () => <QqConnectorSettings />,
};

/* ============================== 卡片 ============================== */

function ControlCardView({
  card,
  onFieldClick,
  ctx,
}: {
  card: ControlCard;
  onFieldClick: (fieldId: string, e: React.MouseEvent) => void;
  ctx: ComponentContext;
}): ReactElement {
  return (
    <section className={`cc-card${card.id === 'home-lab' ? ' cc-card-home-lab' : ''}`}>
      <header className="cc-card-head">
        {card.icon && (
          <span className="cc-card-icon" aria-hidden>
            {card.icon}
          </span>
        )}
        <h4>{card.title}</h4>
      </header>
      <div className="cc-card-body">
        {card.id === 'home-quick' ? (
          <HomeQuickGrid onNavigate={onFieldClick} />
        ) : card.id === 'home-lab' ? (
          <div className="cc-lab-strip">
            <span>
              实验室 · {card.fields?.length ?? 0} 项规划中功能（{(card.fields ?? []).map((f) => f.label).join(' / ')}）
            </span>
            <span className="cc-quick-arrow">›</span>
          </div>
        ) : card.component ? (
          <div className="cc-component-host">{COMPONENT_MAP[card.component](ctx)}</div>
        ) : (
          (card.fields ?? []).map((field) => (
            <div className="cc-field" key={field.id}>
              <span className="cc-field-label">{field.label}</span>
              <div className="cc-field-control">
                <RenderControl field={field} onFieldClick={onFieldClick} />
              </div>
            </div>
          ))
        )}
      </div>
    </section>
  );
}

/* ============================== 主框架 ============================== */

export interface ControlCenterProps {
  onClose?: () => void;
  /** 初始分区（FeatureDock 等入口跳转用）；不合法/缺失则回退到本地记忆或首页。 */
  initialSection?: string;
  /** 真实功能组件上下文：WebSocket / 设置同步 / 角色配置 UID */
  ws?: () => WSClient | null;
  settingsSync?: SettingsSyncState;
  confUid?: string;
}

const LAST_SECTION_KEY = 'moonlight.settings.lastSection';
/** 概览轮询间隔（10s）。 */
const OVERVIEW_POLL_MS = 10_000;

export function ControlCenter({ onClose, initialSection, ws, settingsSync, confUid }: ControlCenterProps): ReactElement {
  const [activeId, setActiveId] = useState<ControlSection['id']>(() => {
    if (initialSection && CONTROL_SECTIONS.some((s) => s.id === initialSection)) {
      return initialSection as ControlSection['id'];
    }
    try {
      const saved = localStorage.getItem(LAST_SECTION_KEY);
      if (saved && CONTROL_SECTIONS.some((s) => s.id === saved)) {
        return saved as ControlSection['id'];
      }
    } catch {
      /* storage unavailable */
    }
    return 'home';
  });
  const [overview, setOverview] = useState<ConsoleOverview | null>(null);
  const [screenMetrics, setScreenMetrics] = useState<ScreenMetricsPayload | null>(null);
  const [feedbackOpen, setFeedbackOpen] = useState(false);
  const showRipple = useShowcaseClick();

  // 概览 + 屏幕指标轮询（fail-soft：接口失败保持旧值/静态值）
  useEffect(() => {
    let cancelled = false;
    const refresh = (): void => {
      void consoleApi
        .overview()
        .then((ov) => {
          if (!cancelled) setOverview(ov);
        })
        .catch(() => {
          /* 后端未启动时静默保留静态值 */
        });
      void screenApi
        .metrics()
        .then((m) => {
          if (!cancelled) setScreenMetrics(m);
        })
        .catch(() => {
          /* noop */
        });
    };
    refresh();
    const timer = window.setInterval(refresh, OVERVIEW_POLL_MS);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, []);

  // 记忆最后访问的分区
  useEffect(() => {
    try {
      localStorage.setItem(LAST_SECTION_KEY, activeId);
    } catch {
      /* storage unavailable */
    }
  }, [activeId]);

  /** 字段级动作：首页快捷入口跳转 / 屏幕回看按钮走真实行为，其余展位涟漪。 */
  const handleFieldClick = useCallback(
    (fieldId: string, e: React.MouseEvent): void => {
      switch (fieldId) {
        case 'hq-voice':
          setActiveId('voice');
          return;
        case 'hq-role':
          setActiveId('role');
          return;
        case 'hq-memory':
          setActiveId('sense');
          return;
        case 'hq-live':
        case 'hq-sing':
          setActiveId('entertainment');
          return;
        case 'hq-task':
          setActiveId('task');
          return;
        case 'sh-feedback':
          setFeedbackOpen(true);
          return;
        case 'sh-clear':
          void screenApi
            .clear()
            .then(() => screenApi.metrics())
            .then((m) => setScreenMetrics(m))
            .catch(() => {
              /* noop */
            });
          return;
        default:
          showRipple(e);
      }
    },
    [showRipple],
  );

  const activeSection = CONTROL_SECTIONS.find((s) => s.id === activeId) ?? CONTROL_SECTIONS[0];

  /** 实时数据注入（仅无 component 的卡）。 */
  const filteredCards = useMemo(() => {
    return (activeSection.cards ?? []).map((card) => {
      if (card.component) return card;
      switch (card.id) {
        case 'system-perf':
          return { ...card, fields: (card.fields ?? []).map((f) => applyPerfField(f, overview)) };
        case 'sense-history':
          return { ...card, fields: (card.fields ?? []).map((f) => applyHistoryField(f, screenMetrics)) };
        default:
          return card;
      }
    });
  }, [activeSection, overview, screenMetrics]);

  const lights = useMemo(() => statusLights(overview), [overview]);

  return (
    <div className="cc-root" role="dialog" aria-label="Moonlight 设置控制台">
      {/* 顶部状态条 */}
      <header className="cc-topbar">
        <div className="cc-topbar-brand">
          <span className="cc-logo">🌙</span>
          <h2>Moonlight 控制台</h2>
        </div>
        <div className="cc-topbar-lights">
          {lights.map((l) => (
            <span className="cc-lamp" key={l.key}>
              <span className={`cc-lamp-dot${l.on ? ' on' : ''}`} />
              {l.label}
            </span>
          ))}
        </div>
        {onClose && (
          <button type="button" className="cc-close" onClick={onClose} aria-label="关闭控制台">
            ✕
          </button>
        )}
      </header>

      <div className="cc-body">
        {/* 左侧导航 */}
        <nav className="cc-nav" aria-label="控制台分区">
          {CONTROL_SECTIONS.map((section) => (
            <button
              type="button"
              key={section.id}
              className={`cc-nav-item${section.id === activeId ? ' active' : ''}${section.id === 'system' ? ' cc-nav-item-muted' : ''}`}
              onClick={() => setActiveId(section.id)}
              aria-current={section.id === activeId ? 'page' : undefined}
            >
              <span className="cc-nav-icon">
                <Icon name={section.icon as IconName} size={17} />
              </span>
              <strong>{section.label}</strong>
            </button>
          ))}
        </nav>

        {/* 内容区 */}
        <main className="cc-main">
          <div className="cc-main-head">
            <h3>
              <span className="cc-main-icon">
                <Icon name={activeSection.icon as IconName} size={20} />
              </span>
              {activeSection.label}
            </h3>
          </div>

          <div className="cc-cards">
            {filteredCards.map((card) => (
              <ControlCardView
                card={card}
                key={card.id}
                onFieldClick={handleFieldClick}
                ctx={{ ws, settingsSync, confUid }}
              />
            ))}
          </div>
        </main>
      </div>

      {/* 屏幕识别反馈统计弹层（sh-feedback） */}
      {feedbackOpen && (
        <div className="cc-modal-mask" onClick={() => setFeedbackOpen(false)}>
          <div className="cc-modal" role="dialog" aria-label="识别反馈统计" onClick={(e) => e.stopPropagation()}>
            <h4>屏幕识别反馈统计</h4>
            {screenMetrics ? (
              <ul className="cc-modal-list">
                <li>
                  有用 <strong>{screenMetrics.feedback_useful ?? 0}</strong>
                </li>
                <li>
                  打扰 <strong>{screenMetrics.feedback_disruptive ?? 0}</strong>
                </li>
                <li>
                  误识别 <strong>{screenMetrics.feedback_misrecognition ?? 0}</strong>
                </li>
                <li>
                  累计分析 <strong>{screenMetrics.analyze_count ?? 0}</strong> 次 · 错误{' '}
                  <strong>{screenMetrics.analyze_errors ?? 0}</strong>
                </li>
              </ul>
            ) : (
              <p className="cc-modal-note">暂无数据（屏幕感知未启用或无请求）。</p>
            )}
            <button type="button" className="cc-btn" onClick={() => setFeedbackOpen(false)}>
              关闭
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

export default ControlCenter;

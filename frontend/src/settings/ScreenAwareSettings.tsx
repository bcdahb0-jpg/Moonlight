/**
 * 感知页 · 屏幕感知（screen_awareness Phase 1/2/5）：
 * 启用开关 + 轮询频率 + 变化阈值 + 「仅询问时识别」+ 视觉模型/成本 +
 * 性能面板 + 隐私黑名单 + 采集状态 + 暂停/清除/立即采集 + 识别反馈。
 * 原「主动对话」区块仍在「主动」页（ProactiveSettings）。
 */
import { useEffect, useState, type ReactElement } from 'react';
import { useAppState } from '@/state/AppStateContext';
import { screenApi, type ScreenConfigPayload, type ScreenMetricsPayload } from '@/api/rest';
import {
  SettingsActionBar,
  SettingsGroup,
  SettingsMetricStrip,
  SettingsRow,
  SettingsStatusBadge,
} from './SettingsConsole';

const PAUSE_REASON_LABEL: Record<string, string> = {
  user_disabled: '已手动停用',
  user_paused: '已暂停',
  moonlight_self: 'Moonlight 自身窗口',
  sensitive_app: '敏感应用（密码/支付）',
  title_keyword: '标题命中隐私关键词',
  user_blocklist_app: '应用黑名单',
  user_blocklist_title: '标题黑名单',
  no_active_window: '无前台窗口',
};

const REASON_LABEL: Record<string, string> = {
  useful: '有用',
  disruptive: '打扰',
  misrecognition: '识别错误',
};

export function ScreenAwareSettings(): ReactElement {
  const { state, dispatch } = useAppState();
  const settings = state.settings;
  const st = state.screenStatus;

  const [screenConfig, setScreenConfig] = useState<ScreenConfigPayload | null>(null);
  const [metrics, setMetrics] = useState<ScreenMetricsPayload | null>(null);
  const [feedbackSent, setFeedbackSent] = useState<string | null>(null);

  // 拉取视觉模型配置 + 指标（10s 轮询，关闭时跳过）。
  useEffect(() => {
    let alive = true;
    const load = async (): Promise<void> => {
      try {
        const [cfg, m] = await Promise.all([screenApi.config(), screenApi.metrics()]);
        if (!alive) return;
        setScreenConfig(cfg);
        setMetrics(m);
      } catch {
        // 后端未就绪：静默，下次轮询再试。
      }
    };
    void load();
    const timer = window.setInterval(() => void load(), 10_000);
    return () => {
      alive = false;
      window.clearInterval(timer);
    };
  }, [settings.screenAwareEnabled]);

  const update = (patch: Partial<typeof settings>): void => {
    dispatch({ type: 'UPDATE_SETTINGS', settings: patch });
  };

  const parseList = (raw: string): string[] =>
    raw
      .split(/[,，\n]/)
      .map((s) => s.trim())
      .filter(Boolean);

  const isCapturing = st?.capturing ?? false;
  const pauseReason = st?.pause_reason || '';
  const suppressedReasons = metrics?.proactive_suppressed_reasons ?? {};
  const topReasons = Object.entries(suppressedReasons)
    .sort((a, b) => b[1] - a[1])
    .slice(0, 4);

  const sendFeedback = (kind: 'useful' | 'disruptive' | 'misrecognition'): void => {
    setFeedbackSent(kind);
    void screenApi.feedback(kind).catch(() => undefined);
    window.setTimeout(() => setFeedbackSent(null), 2000);
  };

  return (
    <div className="settings-section settings-console-root settings-sense-console">
      <SettingsMetricStrip
        metrics={[
          {
            label: '感知状态',
            value: pauseReason || (settings.screenAwareEnabled ? (isCapturing ? '正在识别' : '已启用') : '已停用'),
            tone: settings.screenAwareEnabled ? (pauseReason ? 'warn' : 'ok') : 'neutral',
          },
          { label: '当前窗口', value: st?.last_window_app || state.activeWindow?.app || '无前台窗口' },
          { label: '识别次数', value: `${st?.analyze_count ?? 0} 次` },
          { label: '预计成本', value: metrics?.estimated_cost_usd ? `$${metrics.estimated_cost_usd.toFixed(5)}` : '尚未产生' },
        ]}
      />

      <SettingsGroup
        title="屏幕感知"
        description="识别前台窗口，为对话提供屏幕上下文；隐私规则命中时不会上传图像"
        className="console-group-primary"
      >
        <SettingsRow label="启用屏幕感知" description="允许 Moonlight 读取经过隐私过滤的前台窗口" settingKey="setting-screen-enabled">
            <input
              className="switch"
              type="checkbox"
              checked={settings.screenAwareEnabled}
              onChange={(e) => update({ screenAwareEnabled: e.target.checked })}
            />
        </SettingsRow>
        <SettingsRow
          label="仅用户询问时识别"
          description={settings.screenCaptureOnDemand ? '关闭自动采集；说“看看屏幕/报错”时才识别' : '自动感知画面变化并识别'}
          settingKey="setting-screen-on-demand"
        >
            <input
              className="switch"
              type="checkbox"
              checked={settings.screenCaptureOnDemand}
              onChange={(e) => update({ screenCaptureOnDemand: e.target.checked })}
            />
        </SettingsRow>
      </SettingsGroup>

      <SettingsGroup title="采集状态" description="当前窗口、识别结果和采集统计">
        <SettingsRow label="状态" description="实时采集状态">
          <SettingsStatusBadge tone={pauseReason ? 'warn' : isCapturing ? 'ok' : 'neutral'}>
            {pauseReason
              ? (PAUSE_REASON_LABEL[pauseReason] ?? `已暂停（${pauseReason}）`)
              : isCapturing
                ? '正在识别…'
                : settings.screenCaptureOnDemand
                  ? '按需识别（等待询问）'
                  : '等待画面变化'}
          </SettingsStatusBadge>
        </SettingsRow>
        <SettingsRow label="当前窗口" description="最近一次通过隐私规则的窗口">
          <span className="console-value-text">{st?.last_window_title || state.activeWindow?.title || '（无）'}</span>
        </SettingsRow>
        <SettingsRow label="应用" description="前台应用进程名">
          <span className="console-value-text">{st?.last_window_app || state.activeWindow?.app || '（无）'}</span>
        </SettingsRow>
        <SettingsRow label="场景 / 摘要" description="最近一次视觉识别结果">
          <span className="console-value-text">{st?.last_scene ? `${st.last_scene} · ${st.last_summary || '…'}` : '尚未识别'}</span>
        </SettingsRow>
        <SettingsRow label="最近识别" description="本地时间">
          <span className="console-value-text">{st?.last_analyze_at ? new Date(st.last_analyze_at * 1000).toLocaleTimeString() : '—'}</span>
        </SettingsRow>
        <SettingsRow label="累计统计" description="采集 / 去重 / 分析">
          <span className="console-value-text">{st?.frames_captured ?? 0} / {st?.frames_deduped ?? 0} / {st?.analyze_count ?? 0}</span>
        </SettingsRow>
        <SettingsActionBar note="清除会删除后端内存中的图像、摘要和窗口身份">
            <button
              className="btn"
              onClick={() => {
                // 立即采集一帧（用户主动看屏幕 / 手动触发识别）。
                void import('@/screen/screenActions').then(({ screenActions: sa }) => {
                  sa.captureOnce();
                });
              }}
            >
              立即识别
            </button>
            <button
              className="btn"
              onClick={() => {
                // 暂停采集（渲染端调度停 + 后端停用清空）。
                update({ screenAwareEnabled: false });
                void import('@/screen/screenActions').then(({ screenActions: sa }) => {
                  sa.pause('user_paused');
                });
              }}
            >
              暂停
            </button>
            <button
              className="btn btn-danger"
              onClick={() => {
                // 立即清除后端内存上下文（图像+摘要+窗口身份）。
                void import('@/screen/screenActions').then(({ screenActions: sa }) => {
                  sa.clear();
                });
              }}
            >
              清除
            </button>
        </SettingsActionBar>
      </SettingsGroup>

      {!settings.screenCaptureOnDemand && (
        <SettingsGroup title="采集调度" description="自动识别模式下的轮询频率、变化敏感度和空闲降频">
          <SettingsRow label="轮询间隔（秒）" description="自动采集的最小时间间隔" settingKey="setting-screen-schedule">
            <input type="number" min={2} max={60} value={settings.screenPollIntervalSec} onChange={(e) => update({ screenPollIntervalSec: Math.max(2, Number(e.target.value)) })} />
          </SettingsRow>
          <SettingsRow label="画面变化阈值" description="0-1；越小越敏感">
            <input type="number" min={0.02} max={0.3} step={0.01} value={settings.screenChangeThreshold} onChange={(e) => update({ screenChangeThreshold: Math.min(0.3, Math.max(0.02, Number(e.target.value))) })} />
          </SettingsRow>
          <SettingsRow label="空闲降频（秒）" description="超过后仅监听窗口切换">
            <input type="number" min={5} max={120} value={settings.screenIdleThresholdSec} onChange={(e) => update({ screenIdleThresholdSec: Math.max(5, Number(e.target.value)) })} />
          </SettingsRow>
        </SettingsGroup>
      )}
      <SettingsGroup title="视觉模型与成本" description="视觉模型负责识别，主脑负责生成回复；成本按实际 token 估算">
        <SettingsRow label="模型" description="视觉识别模型">
          <span className="console-value-text">{screenConfig?.model || '未配置（继承对话模型）'}</span>
        </SettingsRow>
        <SettingsRow label="Provider" description="视觉模型服务商">
          <span className="console-value-text">{screenConfig?.provider_label || screenConfig?.provider || '—'}</span>
        </SettingsRow>
        <SettingsRow label="视觉 token" description="输入 / 输出">
          <span className="console-value-text">{metrics?.vision_input_tokens ?? 0} / {metrics?.vision_output_tokens ?? 0}</span>
        </SettingsRow>
        <SettingsRow label="预计成本" description="本地估算值">
          <span className="console-value-text">{metrics?.estimated_cost_usd != null && metrics.estimated_cost_usd > 0 ? `$${metrics.estimated_cost_usd.toFixed(5)}` : '—（尚未产生）'}</span>
        </SettingsRow>
      </SettingsGroup>

      <SettingsGroup title="性能与策略指标" description="识别耗时、去重效果、错误和主动陪伴策略统计">
        <SettingsRow label="采集延迟" description="P50 / P95">
          <span className="console-value-text">{metrics?.capture_p50_ms ?? '—'}ms / {metrics?.capture_p95_ms ?? '—'}ms</span>
        </SettingsRow>
        <SettingsRow label="分析延迟" description="P50 / P95">
          <span className="console-value-text">{metrics?.analyze_p50_ms ?? '—'}ms / {metrics?.analyze_p95_ms ?? '—'}ms</span>
        </SettingsRow>
        <SettingsRow label="去重率" description="静态画面跳过次数">
          <span className="console-value-text">{metrics ? `${(metrics.dedupe_ratio * 100).toFixed(1)}% · 跳过 ${metrics.frames_deduped} 次` : '—'}</span>
        </SettingsRow>
        <SettingsRow label="分析错误" description="视觉模型请求失败次数">
          <span className="console-value-text">{metrics?.analyze_errors ?? 0} 次</span>
        </SettingsRow>
        <SettingsRow label="主动发言" description="触发 / 抑制">
          <span className="console-value-text">{metrics?.proactive_count ?? 0} / {metrics?.proactive_suppressed ?? 0}</span>
        </SettingsRow>
        {topReasons.length > 0 && (
          <SettingsRow label="抑制原因" description="出现次数最多的策略原因">
            <span className="console-value-text">{topReasons.map(([r, n]) => `${REASON_LABEL[r] ?? r}×${n}`).join(' · ')}</span>
          </SettingsRow>
        )}
      </SettingsGroup>

      <SettingsGroup title="识别反馈" description="仅记录计数，不保存截图，用于本地策略调优">
        <SettingsActionBar>
            {(['useful', 'disruptive', 'misrecognition'] as const).map((kind) => (
              <button
                key={kind}
                className={`btn ${feedbackSent === kind ? 'btn-primary' : ''}`}
                onClick={() => sendFeedback(kind)}
              >
                {feedbackSent === kind ? '已记录 ✓' : REASON_LABEL[kind]}
              </button>
            ))}
        </SettingsActionBar>
      </SettingsGroup>

      <SettingsGroup title="隐私与排除" description="命中规则的窗口不会截图或上传；密码、登录、支付页面默认排除">
        <SettingsRow label="应用黑名单" description="进程名，逗号分隔，例如 wechat, qq" settingKey="setting-screen-privacy">
            <input
              type="text"
              placeholder="如: wechat, qq"
              value={settings.screenBlockedApps.join(', ')}
              onChange={(e) => update({ screenBlockedApps: parseList(e.target.value) })}
            />
        </SettingsRow>
        <SettingsRow label="标题关键词黑名单" description="逗号分隔，例如工资条、合同、密码">
            <input
              type="text"
              placeholder="如: 工资条, 合同"
              value={settings.screenBlockedTitleKeywords.join(', ')}
              onChange={(e) =>
                update({ screenBlockedTitleKeywords: parseList(e.target.value) })
              }
            />
        </SettingsRow>
      </SettingsGroup>

      {state.activeWindow && (
        <SettingsGroup title="前台窗口（实时）" description="当前客户端上报的窗口状态">
          <SettingsRow label="标题"><span className="console-value-text">{state.activeWindow.title || '（无）'}</span></SettingsRow>
          <SettingsRow label="应用"><span className="console-value-text">{state.activeWindow.app || '（无）'}</span></SettingsRow>
          <SettingsRow label="空闲时间"><span className="console-value-text">{state.activeWindow.idleTime} 秒</span></SettingsRow>
        </SettingsGroup>
      )}
    </div>
  );
}

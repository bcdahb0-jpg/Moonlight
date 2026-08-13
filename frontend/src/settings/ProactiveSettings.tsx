import { useCallback, useEffect, useState, type ReactElement } from 'react';
import { topicsApi, ApiError, type ProactiveTopicsResult } from '@/api/rest';
import { useAppState } from '@/state/AppStateContext';
import { Icon } from '@/ui/icons';
import { SettingsActionBar, SettingsGroup, SettingsMetricStrip, SettingsRow } from './SettingsConsole';

export function ProactiveSettings(): ReactElement {
  const { state, dispatch } = useAppState();
  const [data, setData] = useState<ProactiveTopicsResult | null>(null);
  const [newTopic, setNewTopic] = useState('');
  const [status, setStatus] = useState('');

  const updateSetting = (patch: Partial<typeof state.settings>): void => {
    dispatch({ type: 'UPDATE_SETTINGS', settings: patch });
  };

  const load = useCallback(async (): Promise<void> => {
    try {
      setData(await topicsApi.get());
    } catch (err) {
      setStatus(errorMessage(err));
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const save = async (patch: {
    topics?: string[];
    news?: { enabled?: boolean; interval_hours?: number };
  }): Promise<void> => {
    try {
      await topicsApi.save(patch);
      setStatus('已保存并重组主动话题提示词');
      await load();
    } catch (err) {
      setStatus(errorMessage(err));
    }
  };

  const addTopic = (): void => {
    const t = newTopic.trim();
    if (!t || !data) return;
    void save({ topics: [...data.topics, t] });
    setNewTopic('');
  };

  const removeTopic = (topic: string): void => {
    if (!data) return;
    void save({ topics: data.topics.filter((t) => t !== topic) });
  };

  const refreshNow = async (): Promise<void> => {
    try {
      const res = await topicsApi.refresh();
      setStatus(`刷新完成：${res.news_count} 条新闻`);
    } catch (err) {
      setStatus(errorMessage(err));
    }
  };

  return (
    <div className="settings-section settings-console-root settings-proactive-console">
      <SettingsMetricStrip
        metrics={[
          { label: '主动陪伴', value: state.settings.proactiveEnabled ? '已启用' : '已停用', tone: state.settings.proactiveEnabled ? 'ok' : 'neutral' },
          { label: '空闲触发', value: `${state.settings.proactiveIdleSec} 秒` },
          { label: '屏幕巡检', value: state.settings.screenProactiveIntervalSec > 0 ? `${state.settings.screenProactiveIntervalSec} 秒` : '已关闭' },
          { label: '话题数量', value: data ? `${data.topics.length} 个` : '加载中' },
        ]}
      />

      <SettingsGroup title="主动对话" description="允许 Moonlight 在空闲时主动发起交流" className="console-group-primary">
        <SettingsRow label="启用主动对话" description="控制空闲搭话能力">
            <input
              className="switch"
              type="checkbox"
              checked={state.settings.proactiveEnabled}
              onChange={(e) => updateSetting({ proactiveEnabled: e.target.checked })}
            />
        </SettingsRow>
        <SettingsRow label="空闲触发（秒）" description="无交互达到此时长后允许搭话">
            <input
              type="number"
              min={5}
              max={600}
              value={state.settings.proactiveIdleSec}
              onChange={(e) => updateSetting({ proactiveIdleSec: Math.max(5, Number(e.target.value)) })}
            />
        </SettingsRow>
        <SettingsRow label="自动发送话题" description="让角色主动选择话题并发送">
            <input
              className="switch"
              type="checkbox"
              checked={state.settings.autoSpeakOnIdle}
              onChange={(e) => updateSetting({ autoSpeakOnIdle: e.target.checked })}
            />
        </SettingsRow>
        <SettingsRow label="仅桌宠模式触发" description="窗口模式中不打扰任务或输入流程">
            <input
              className="switch"
              type="checkbox"
              checked={state.settings.proactivePetModeOnly}
              onChange={(e) => updateSetting({ proactivePetModeOnly: e.target.checked })}
            />
        </SettingsRow>
      </SettingsGroup>

      {/* Phase 2（pet-ptt-workflow）：定时屏幕巡检 —— 与「空闲主动」相互独立，
          避免用户误以为同一开关控制两种触发。 */}
      <SettingsGroup title="定时屏幕巡检" description="只在发现新内容时触发策略判断；静态画面、沉浸场景或忙碌时保持安静">
        <SettingsRow label="巡检间隔（秒）" description="0 表示关闭；建议同时开启屏幕感知" settingKey="setting-proactive-scan">
            <input
              type="number"
              min={0}
              max={3600}
              step={60}
              value={state.settings.screenProactiveIntervalSec}
              onChange={(e) => {
                const v = Number(e.target.value);
                updateSetting({
                  screenProactiveIntervalSec: Number.isFinite(v)
                    ? Math.min(3600, Math.max(0, v))
                    : 0,
                });
              }}
              />
        </SettingsRow>
      </SettingsGroup>

      <SettingsGroup title="主动话题" description="管理角色主动搭话时使用的主题" count={data ? `${data.topics.length} 个` : undefined}>
        <div data-setting-key="setting-proactive-topics">
          {data ? (
            <>
              <div className="topic-chips">
                {data.topics.map((t) => (
                  <span key={t} className="topic-chip">
                    {t}
                    <button onClick={() => removeTopic(t)} aria-label={`删除 ${t}`}>
                      ✕
                    </button>
                  </span>
                ))}
              </div>
              <SettingsActionBar>
                <input
                  className="topic-input"
                  value={newTopic}
                  onChange={(e) => setNewTopic(e.target.value)}
                  onKeyDown={(e) => e.key === 'Enter' && addTopic()}
                  placeholder="输入新话题…"
                />
                <button className="btn" onClick={addTopic}>
                  <Icon name="plus" size={13} />
                  添加
                </button>
              </SettingsActionBar>
              {data.suggestions.length > 0 && (
                <div className="suggestions">
                  {data.suggestions.map((s) => (
                    <button key={s} className="chip-btn" onClick={() => setNewTopic(s)}>
                      {s}
                    </button>
                  ))}
                </div>
              )}
            </>
          ) : (
            <div className="empty-state">加载中…</div>
          )}
        </div>
      </SettingsGroup>

      <SettingsGroup title="新闻来源" description="自动拉取新闻，为主动话题提供补充内容">
          {data && (
            <>
              <SettingsRow label="启用 Google News" description="定期拉取新闻并加入话题候选">
                <input
                  className="switch"
                  type="checkbox"
                  checked={data.news.enabled}
                  onChange={(e) => void save({ news: { enabled: e.target.checked } })}
                />
              </SettingsRow>
              <SettingsRow label="刷新间隔（小时）" description="新闻源自动更新频率">
                <input
                  type="number"
                  min={1}
                  max={24}
                  value={data.news.interval_hours}
                  onChange={(e) =>
                    void save({ news: { interval_hours: Number(e.target.value) } })
                  }
                />
              </SettingsRow>
              <SettingsActionBar>
                <button className="btn" onClick={() => void refreshNow()}>
                  <Icon name="refresh" size={13} />
                  立即刷新
                </button>
              </SettingsActionBar>
            </>
          )}
      </SettingsGroup>

      {status && <div className="setting-status">{status}</div>}
    </div>
  );
}

function errorMessage(err: unknown): string {
  if (err instanceof ApiError) return err.message;
  return err instanceof Error ? err.message : '发生未知错误';
}

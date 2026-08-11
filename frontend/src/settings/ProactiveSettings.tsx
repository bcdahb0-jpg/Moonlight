import { useCallback, useEffect, useState, type ReactElement } from 'react';
import { topicsApi, ApiError, type ProactiveTopicsResult } from '@/api/rest';
import { useAppState } from '@/state/AppStateContext';
import { Icon } from '@/ui/icons';

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
    <div className="settings-section general-settings">
      {/* 主动对话开关（原「屏幕感知」页移入） */}
      <section className="settings-card">
        <div className="settings-card-head">
          <span className="settings-card-icon">
            <Icon name="zap" size={16} />
          </span>
          <div className="settings-card-title">
            <h3>主动对话</h3>
          </div>
        </div>
        <div className="settings-card-body">
          <label className="toggle-row">
            <span>启用主动对话</span>
            <input
              type="checkbox"
              checked={state.settings.proactiveEnabled}
              onChange={(e) => updateSetting({ proactiveEnabled: e.target.checked })}
            />
          </label>
          <label className="field">
            <span>空闲触发（秒）</span>
            <input
              type="number"
              min={5}
              max={600}
              value={state.settings.proactiveIdleSec}
              onChange={(e) => updateSetting({ proactiveIdleSec: Math.max(5, Number(e.target.value)) })}
            />
          </label>
          <label className="toggle-row">
            <span>自动发送话题</span>
            <input
              type="checkbox"
              checked={state.settings.autoSpeakOnIdle}
              onChange={(e) => updateSetting({ autoSpeakOnIdle: e.target.checked })}
            />
          </label>
          <label className="toggle-row">
            <span>仅桌宠模式触发</span>
            <input
              type="checkbox"
              checked={state.settings.proactivePetModeOnly}
              onChange={(e) => updateSetting({ proactivePetModeOnly: e.target.checked })}
            />
          </label>
          <div className="field-note">
            开启后，主动找话题只在桌宠模式触发；窗口模式（等待任务/思考输入）不会被打扰。
          </div>
        </div>
      </section>

      <section className="settings-card">
        <div className="settings-card-head">
          <span className="settings-card-icon">
            <Icon name="message" size={16} />
          </span>
          <div className="settings-card-title">
            <h3>主动话题</h3>
          </div>
        </div>
        <div className="settings-card-body">
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
              <div className="btn-row">
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
              </div>
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
      </section>

      <section className="settings-card">
        <div className="settings-card-head">
          <span className="settings-card-icon">
            <Icon name="wifi" size={16} />
          </span>
          <div className="settings-card-title">
            <h3>新闻来源</h3>
          </div>
        </div>
        <div className="settings-card-body">
          {data && (
            <>
              <label className="toggle-row">
                <span>启用 Google News 自动拉取</span>
                <input
                  type="checkbox"
                  checked={data.news.enabled}
                  onChange={(e) => void save({ news: { enabled: e.target.checked } })}
                />
              </label>
              <label className="field">
                <span>刷新间隔（小时）</span>
                <input
                  type="number"
                  min={1}
                  max={24}
                  value={data.news.interval_hours}
                  onChange={(e) =>
                    void save({ news: { interval_hours: Number(e.target.value) } })
                  }
                />
              </label>
              <div className="btn-row">
                <button className="btn" onClick={() => void refreshNow()}>
                  <Icon name="refresh" size={13} />
                  立即刷新
                </button>
              </div>
            </>
          )}
        </div>
      </section>

      {status && <div className="setting-status">{status}</div>}
    </div>
  );
}

function errorMessage(err: unknown): string {
  if (err instanceof ApiError) return err.message;
  return err instanceof Error ? err.message : '发生未知错误';
}

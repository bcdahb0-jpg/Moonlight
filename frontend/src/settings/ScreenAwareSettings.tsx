import type { ReactElement } from 'react';
import { useAppState } from '@/state/AppStateContext';
import { Icon } from '@/ui/icons';

export function ScreenAwareSettings(): ReactElement {
  const { state, dispatch } = useAppState();
  const settings = state.settings;

  const update = (patch: Partial<typeof settings>): void => {
    dispatch({ type: 'UPDATE_SETTINGS', settings: patch });
  };

  return (
    <div className="settings-section general-settings">
      <section className="settings-card">
        <div className="settings-card-head">
          <span className="settings-card-icon">
            <Icon name="monitor" size={16} />
          </span>
          <div className="settings-card-title">
            <h3>屏幕感知</h3>
            <p>她能看到你在看什么窗口</p>
          </div>
        </div>
        <div className="settings-card-body">
          <label className="toggle-row">
            <span>启用屏幕感知（轮询当前活动窗口）</span>
            <input
              type="checkbox"
              checked={settings.screenAwareEnabled}
              onChange={(e) => update({ screenAwareEnabled: e.target.checked })}
            />
          </label>
          <label className="field">
            <span>轮询频率（秒）</span>
            <input
              type="number"
              min={1}
              max={60}
              value={settings.screenPollIntervalSec}
              onChange={(e) =>
                update({ screenPollIntervalSec: Math.max(1, Number(e.target.value)) })
              }
            />
          </label>
        </div>
      </section>

      <section className="settings-card">
        <div className="settings-card-head">
          <span className="settings-card-icon">
            <Icon name="zap" size={16} />
          </span>
          <div className="settings-card-title">
            <h3>主动对话</h3>
            <p>空闲后由 AI 主动开口</p>
          </div>
        </div>
        <div className="settings-card-body">
          <label className="toggle-row">
            <span>启用主动对话</span>
            <input
              type="checkbox"
              checked={settings.proactiveEnabled}
              onChange={(e) => update({ proactiveEnabled: e.target.checked })}
            />
          </label>
          <label className="field">
            <span>空闲触发秒数</span>
            <input
              type="number"
              min={5}
              max={600}
              value={settings.proactiveIdleSec}
              onChange={(e) => update({ proactiveIdleSec: Math.max(5, Number(e.target.value)) })}
            />
          </label>
          <label className="toggle-row">
            <span>自动发送主动话题</span>
            <input
              type="checkbox"
              checked={settings.autoSpeakOnIdle}
              onChange={(e) => update({ autoSpeakOnIdle: e.target.checked })}
            />
          </label>
        </div>
      </section>

      {state.activeWindow && (
        <section className="settings-card">
          <div className="settings-card-head">
            <span className="settings-card-icon">
              <Icon name="eye" size={16} />
            </span>
            <div className="settings-card-title">
              <h3>当前窗口</h3>
              <p>实时感知结果</p>
            </div>
          </div>
          <div className="settings-card-body">
            <div className="screen-info">
              <div>
                <span className="screen-label">当前窗口</span>
                <span className="screen-value">{state.activeWindow.title || '（无）'}</span>
              </div>
              <div>
                <span className="screen-label">应用</span>
                <span className="screen-value">{state.activeWindow.app || '（无）'}</span>
              </div>
              <div>
                <span className="screen-label">空闲</span>
                <span className="screen-value">{state.activeWindow.idleTime} 秒</span>
              </div>
            </div>
          </div>
        </section>
      )}
    </div>
  );
}

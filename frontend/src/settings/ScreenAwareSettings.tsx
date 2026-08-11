/**
 * 感知页 · 屏幕感知（重设计 v4）：启用开关 + 轮询频率 + 当前窗口只读。
 * 原「主动对话」区块已移入「主动」页（ProactiveSettings）。
 */
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
          </div>
        </div>
        <div className="settings-card-body">
          <label className="toggle-row">
            <span>启用屏幕感知</span>
            <input
              type="checkbox"
              checked={settings.screenAwareEnabled}
              onChange={(e) => update({ screenAwareEnabled: e.target.checked })}
            />
          </label>
          <label className="field">
            <span>轮询间隔（秒）</span>
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

      {state.activeWindow && (
        <section className="settings-card">
          <div className="settings-card-head">
            <span className="settings-card-icon">
              <Icon name="eye" size={16} />
            </span>
            <div className="settings-card-title">
              <h3>当前窗口</h3>
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


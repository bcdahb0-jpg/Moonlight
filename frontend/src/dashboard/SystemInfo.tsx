import type { ReactElement } from 'react';
import { useAppState } from '@/state/AppStateContext';
import { Icon } from '@/ui/icons';

const STATUS_LABEL: Record<string, string> = {
  connected: '已连接',
  connecting: '连接中…',
  disconnected: '未连接',
};

export function SystemInfo(): ReactElement {
  const { state, dispatch } = useAppState();

  return (
    <div className="settings-section general-settings">
      <section className="settings-card">
        <div className="settings-card-head">
          <span className="settings-card-icon">
            <Icon name="wifi" size={16} />
          </span>
          <div className="settings-card-title">
            <h3>连接状态</h3>
            <p>WebSocket 与后端的实时连接情况</p>
          </div>
        </div>
        <div className="settings-card-body">
          <div className="system-card">
            <div className="system-card-title">WebSocket 连接</div>
            <div className="system-card-value">
              <span className={`conn-dot inline ${state.connStatus}`} data-status={state.connStatus} />
              {STATUS_LABEL[state.connStatus] ?? state.connStatus}
            </div>
          </div>
        </div>
      </section>

      <section className="settings-card">
        <div className="settings-card-head">
          <span className="settings-card-icon">
            <Icon name="sparkles" size={16} />
          </span>
          <div className="settings-card-title">
            <h3>当前角色</h3>
            <p>正在运行的桌面伙伴</p>
          </div>
        </div>
        <div className="settings-card-body">
          <div className="system-card">
            <div className="system-card-title">角色配置</div>
            <div className="system-card-value">{state.confName || '—'}</div>
            <div className="system-card-title" style={{ marginTop: 8 }}>
              conf_uid
            </div>
            <div className="system-card-value">{state.confUid || '—'}</div>
            <div className="system-card-title" style={{ marginTop: 8 }}>
              Live2D 模型
            </div>
            <div className="system-card-value">{state.modelInfo?.name || '—'}</div>
          </div>
        </div>
      </section>

      <section className="settings-card">
        <div className="settings-card-head">
          <span className="settings-card-icon">
            <Icon name="alert" size={16} />
          </span>
          <div className="settings-card-title">
            <h3>最近错误</h3>
            <p>运行中记录的异常信息</p>
          </div>
        </div>
        <div className="settings-card-body">
          {state.lastError ? (
            <div className="system-card system-card-error">
              <div className="system-card-title">错误信息</div>
              <div className="system-card-value">{state.lastError}</div>
              <div className="btn-row">
                <button className="btn" onClick={() => dispatch({ type: 'SET_ERROR', message: null })}>
                  清除
                </button>
              </div>
            </div>
          ) : (
            <div className="empty-state">无错误记录</div>
          )}
        </div>
      </section>
    </div>
  );
}

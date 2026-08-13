import type { ReactElement } from 'react';
import { useAppState } from '@/state/AppStateContext';
import { SettingsGroup, SettingsMetricStrip, SettingsRow } from '@/settings/SettingsConsole';

const STATUS_LABEL: Record<string, string> = {
  connected: '已连接',
  connecting: '连接中…',
  disconnected: '未连接',
};

export function SystemInfo(): ReactElement {
  const { state, dispatch } = useAppState();

  return (
    <div className="settings-section settings-console-root settings-system-console">
      <SettingsMetricStrip
        metrics={[
          { label: '后端连接', value: STATUS_LABEL[state.connStatus] ?? state.connStatus, tone: state.connStatus === 'connected' ? 'ok' : 'warn' },
          { label: '当前角色', value: state.confName || '未选择' },
          { label: 'Live2D', value: state.modelInfo?.name || '未加载' },
          { label: '最近错误', value: state.lastError ? '有错误' : '正常', tone: state.lastError ? 'danger' : 'ok' },
        ]}
      />

      <SettingsGroup title="连接状态" description="WebSocket 与后端的实时连接情况" className="console-group-primary">
        <SettingsRow label="WebSocket 连接" description="当前实时通信状态">
              <span className={`conn-dot inline ${state.connStatus}`} data-status={state.connStatus} />
              {STATUS_LABEL[state.connStatus] ?? state.connStatus}
        </SettingsRow>
      </SettingsGroup>

      <SettingsGroup title="当前角色" description="正在运行的桌面伙伴">
        <SettingsRow label="角色配置"><span className="console-value-text">{state.confName || '—'}</span></SettingsRow>
        <SettingsRow label="conf_uid"><span className="console-value-text">{state.confUid || '—'}</span></SettingsRow>
        <SettingsRow label="Live2D 模型"><span className="console-value-text">{state.modelInfo?.name || '—'}</span></SettingsRow>
      </SettingsGroup>

      <SettingsGroup title="最近错误" description="运行中记录的异常信息" className="system-error-group">
        <div data-setting-key="setting-system-errors">
          {state.lastError ? (
            <div className="console-error-block">
              <span>错误信息</span>
              <strong>{state.lastError}</strong>
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
      </SettingsGroup>
    </div>
  );
}

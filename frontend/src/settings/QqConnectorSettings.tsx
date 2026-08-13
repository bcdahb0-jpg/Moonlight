import { useEffect, useState, type ReactElement } from 'react';
import { qqApi, type QqConfig, type QqStatus } from '@/api/rest';
import { SettingsGroup, SettingsRow, SettingsStatusBadge } from '@/settings/SettingsConsole';

/**
 * QQ 社交连接器（P6，PetGPT QqConnectorPanel 思路）。
 *
 * OneBot v11（NapCat）WebSocket 客户端：QQ 消息 → 桌宠主对话（AI 回复 + TTS）。
 * NapCat 需用户自行安装运行（https://github.com/NapNeko/NapCatQQ，WebSocket
 * 服务器模式默认 ws://127.0.0.1:3001）；个人使用风控自担。
 *
 * 数据源：GET /api/qq/status（连接/消息计数）、GET/POST /api/qq/config
 * （enabled/ws_url/auto_reply，conf surgical upsert + 运行时热更新）。
 */

export function QqConnectorSettings(): ReactElement {
  const [cfg, setCfg] = useState<QqConfig | null>(null);
  const [status, setStatus] = useState<QqStatus | null>(null);
  const [notice, setNotice] = useState('');

  const refreshStatus = (): void => {
    void qqApi
      .status()
      .then(setStatus)
      .catch(() => setNotice('状态拉取失败（后端未重启？）'));
  };

  useEffect(() => {
    void qqApi.config().then(setCfg).catch(() => undefined);
    refreshStatus();
    const timer = window.setInterval(refreshStatus, 5000);
    return () => window.clearInterval(timer);
  }, []);

  const saveCfg = (patch: Partial<QqConfig>): void => {
    void qqApi.saveConfig(patch).then((c) => {
      setCfg(c);
      setNotice(patch.enabled === true ? '已启用（开始连接 NapCat）' : '配置已保存');
    });
  };

  const toggle = (): void => {
    if (!cfg) return;
    if (cfg.enabled) {
      void qqApi.disconnect().then((s) => {
        setStatus(s);
        setCfg((prev) => (prev ? { ...prev, enabled: false } : prev));
        setNotice('已断开');
      });
    } else {
      void qqApi.connect().then((s) => {
        setStatus(s);
        setCfg((prev) => (prev ? { ...prev, enabled: true } : prev));
        setNotice(s.connected ? '已连接 NapCat' : '连接中…（NapCat 未运行则持续重试）');
      });
    }
  };

  return (
    <SettingsGroup title="QQ 连接器" description="QQ 消息 → 桌宠对话（OneBot v11 / NapCat，个人使用风控自担）">
      <SettingsRow label="启用连接器">
        <label className="toggle-row">
          <input type="checkbox" checked={cfg?.enabled ?? false} onChange={toggle} />
          <span>{cfg?.enabled ? '开' : '关'}</span>
        </label>
      </SettingsRow>

      <SettingsRow label="连接状态">
        <SettingsStatusBadge tone={status?.connected ? 'ok' : status?.enabled ? 'warn' : 'neutral'}>
          {status?.connected ? '已连接' : status?.enabled ? '连接中/重试中' : '未启用'}
          {status?.msg_count ? ` · 已处理 ${status.msg_count} 条消息` : ''}
        </SettingsStatusBadge>
      </SettingsRow>

      <SettingsRow label="OneBot 地址" description="NapCat WebSocket 服务器默认 ws://127.0.0.1:3001">
        <input
          className="console-input"
          style={{ maxWidth: 280 }}
          value={cfg?.ws_url ?? 'ws://127.0.0.1:3001'}
          onChange={(e) => setCfg((prev) => (prev ? { ...prev, ws_url: e.target.value } : prev))}
          onBlur={() => {
            if (cfg?.ws_url) saveCfg({ ws_url: cfg.ws_url });
          }}
        />
      </SettingsRow>

      <SettingsRow label="回复回发" description="AI 回复同时发回 QQ（关闭则只本地对话不出声外发）">
        <label className="toggle-row">
          <input
            type="checkbox"
            checked={cfg?.auto_reply ?? true}
            onChange={(e) => saveCfg({ auto_reply: e.target.checked })}
          />
          <span>{cfg?.auto_reply ? '开' : '关'}</span>
        </label>
      </SettingsRow>

      <SettingsRow label="最近错误">
        <SettingsStatusBadge tone={status?.last_error ? 'warn' : 'ok'}>
          {status?.last_error || '无'}
        </SettingsStatusBadge>
      </SettingsRow>

      <SettingsRow label="使用说明">
        <div className="console-stack">
          <span className="console-tag" style={{ color: 'var(--success,#6ee7a8)' }}>
            1. 安装运行 NapCat（WebSocket 服务器模式）
          </span>
          <span className="console-tag" style={{ color: 'var(--success,#6ee7a8)' }}>
            2. 开启上方开关 → 状态变「已连接」
          </span>
          <span className="console-tag" style={{ color: 'var(--success,#6ee7a8)' }}>
            3. QQ 好友/群发消息 → 桌宠对话回应（需桌宠窗口在线）
          </span>
        </div>
      </SettingsRow>

      <SettingsRow label="状态">
        <SettingsStatusBadge tone={notice ? 'warn' : 'ok'}>
          {notice || '连接器就绪（NapCat 未运行时自动重连）'}
        </SettingsStatusBadge>
      </SettingsRow>
    </SettingsGroup>
  );
}

export default QqConnectorSettings;

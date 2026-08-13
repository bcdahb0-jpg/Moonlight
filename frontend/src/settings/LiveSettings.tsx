import { useEffect, useState, type ReactElement } from 'react';
import { liveApi, type LiveConfig, type LiveStatus, type ObsStatus, type VtsStatus } from '@/api/rest';
import { SettingsGroup, SettingsRow, SettingsStatusBadge } from '@/settings/SettingsConsole';

/**
 * 直播与互动（P3）：B站接入 / 弹幕玩法 / OBS+VTS。
 * 监听依赖 bilibili-api-python（已装）；发弹幕需 Cookie 三件套（用户提供）。
 */

export function LiveBiliSettings(): ReactElement {
  const [cfg, setCfg] = useState<LiveConfig | null>(null);
  const [status, setStatus] = useState<LiveStatus | null>(null);
  const [roomId, setRoomId] = useState('');
  const [notice, setNotice] = useState('');

  const refresh = async (): Promise<void> => {
    try {
      setStatus(await liveApi.status());
      const c = await liveApi.config();
      setCfg(c);
      if (!roomId) setRoomId(String(c.room_id || ''));
    } catch {
      /* noop */
    }
  };

  useEffect(() => {
    void refresh();
    const timer = window.setInterval(refresh, 10_000);
    return () => window.clearInterval(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const connect = async (): Promise<void> => {
    const rid = Number(roomId);
    if (!rid) {
      setNotice('请先填写直播间房号');
      return;
    }
    const res = await liveApi.connect({ room_id: rid });
    setNotice(res.ok ? `已连接直播间 ${rid}` : `连接失败：${res.error ?? ''}`);
    void refresh();
  };

  const saveCookie = async (patch: Partial<LiveConfig>): Promise<void> => {
    try {
      setCfg(await liveApi.saveConfig(patch));
      setNotice('Cookie 已保存（连接时生效）');
    } catch {
      setNotice('保存失败');
    }
  };

  const login = status?.login ?? { sessdata: false, bili_jct: false, buvid3: false };
  const loginComplete = login.sessdata && login.bili_jct && login.buvid3;

  return (
    <SettingsGroup title="B站直播接入" description="监听直播间弹幕并回复（bilibili-api-python）">
      <SettingsRow label="直播间房号">
        <input
          type="text"
          className="console-input"
          value={roomId}
          onChange={(e) => setRoomId(e.target.value)}
          placeholder="如 123456"
        />
      </SettingsRow>

      <SettingsRow label="登录状态" description="发弹幕需要 Cookie 三件套">
        <SettingsStatusBadge tone={loginComplete ? 'ok' : 'warn'}>
          {loginComplete
            ? `已登录（${status?.can_send ? '可发弹幕' : '发送受限'}）`
            : `未完整登录：SESSDATA ${login.sessdata ? '✓' : '✗'} · bili_jct ${login.bili_jct ? '✓' : '✗'} · buvid3 ${login.buvid3 ? '✓' : '✗'}`}
        </SettingsStatusBadge>
      </SettingsRow>

      <SettingsRow label="Cookie 配置" description="sessdata / bili_jct / buvid3（浏览器 F12 → Application → Cookies）">
        <div className="console-stack">
          <input
            type="text"
            className="console-input"
            placeholder="SESSDATA（当前值已打码）"
            defaultValue={cfg?.sessdata ?? ''}
            onBlur={(e) => e.target.value && void saveCookie({ sessdata: e.target.value })}
          />
          <input
            type="text"
            className="console-input"
            placeholder="bili_jct"
            defaultValue={cfg?.bili_jct ?? ''}
            onBlur={(e) => e.target.value && void saveCookie({ bili_jct: e.target.value })}
          />
          <input
            type="text"
            className="console-input"
            placeholder="buvid3"
            defaultValue={cfg?.buvid3 ?? ''}
            onBlur={(e) => e.target.value && void saveCookie({ buvid3: e.target.value })}
          />
        </div>
      </SettingsRow>

      <SettingsRow label="回复方式" description="弹幕回复出口（语音回复 P3.1）">
        <select
          className="console-input"
          style={{ maxWidth: 200 }}
          value={cfg?.reply_mode ?? 'danmaku'}
          onChange={(e) => void saveCookie({ reply_mode: e.target.value })}
        >
          <option value="danmaku">仅弹幕</option>
          <option value="voice+danmaku">语音 + 弹幕</option>
          <option value="voice">仅语音</option>
        </select>
      </SettingsRow>

      <SettingsRow label="连接管理">
        <div className="console-btn-row">
          <button type="button" className="console-btn" onClick={() => void connect()}>
            连接直播间
          </button>
          <button type="button" className="console-btn" onClick={() => void liveApi.disconnect().then(() => void refresh())}>
            断开
          </button>
        </div>
      </SettingsRow>

      <SettingsRow label="状态">
        <SettingsStatusBadge tone={status?.listening ? 'ok' : 'neutral'}>
          {notice ||
            (status?.listening
              ? `监听中 · 已收 ${status.danmaku_count} 条弹幕`
              : status?.biliapi_available
                ? '未连接'
                : 'bilibili-api 未安装')}
        </SettingsStatusBadge>
      </SettingsRow>
    </SettingsGroup>
  );
}

export function LiveDanmakuSettings(): ReactElement {
  const [cfg, setCfg] = useState<LiveConfig | null>(null);
  const [messages, setMessages] = useState<{ kind: string; text: string; uname: string; ts?: number }[]>([]);
  const [notice, setNotice] = useState('');

  useEffect(() => {
    void liveApi.config().then(setCfg).catch(() => undefined);
    const timer = window.setInterval(() => {
      void liveApi.overlayChat(8).then((r) => setMessages(r.messages)).catch(() => undefined);
    }, 3000);
    return () => window.clearInterval(timer);
  }, []);

  const toggle = (key: 'welcome_enabled' | 'gift_enabled' | 'chat_to_conversation', value: boolean): void => {
    void liveApi.saveConfig({ [key]: value } as Partial<LiveConfig>).then(setCfg);
  };

  const testSend = async (): Promise<void> => {
    const res = await liveApi.sendDanmaku('测试弹幕：桌宠在线！');
    setNotice(res.ok ? '已发送测试弹幕' : `发送失败：${res.error ?? ''}`);
  };

  return (
    <SettingsGroup title="弹幕玩法" description="弹幕指令系统：点歌 / 欢迎 / 礼物 / 闲聊">
      <SettingsRow label="弹幕指令集" description="「唱歌+歌名」点歌 · 「切歌」下一首 · 其余入弹幕流">
        <div className="console-tag-wrap">
          {['唱歌+歌名', '切歌', '下一首', '停止学歌', '清空队列'].map((t) => (
            <span className="console-tag" key={t}>
              {t}
            </span>
          ))}
        </div>
      </SettingsRow>

      <SettingsRow label="进房欢迎" description="新观众进入时弹幕打招呼（TTL 10s 去重）">
        <label className="toggle-row">
          <input type="checkbox" checked={cfg?.welcome_enabled ?? true} onChange={(e) => toggle('welcome_enabled', e.target.checked)} />
          <span>{cfg?.welcome_enabled ? '开' : '关'}</span>
        </label>
      </SettingsRow>

      <SettingsRow label="礼物感谢" description="收到礼物弹幕感谢（TTL 10s 去重）">
        <label className="toggle-row">
          <input type="checkbox" checked={cfg?.gift_enabled ?? true} onChange={(e) => toggle('gift_enabled', e.target.checked)} />
          <span>{cfg?.gift_enabled ? '开' : '关'}</span>
        </label>
      </SettingsRow>

      <SettingsRow label="闲聊进对话" description="普通闲聊弹幕 → 桌宠主对话（AI 回复 + 出声，需桌宠窗口在线）">
        <label className="toggle-row">
          <input type="checkbox" checked={cfg?.chat_to_conversation ?? true} onChange={(e) => toggle('chat_to_conversation', e.target.checked)} />
          <span>{cfg?.chat_to_conversation ? '开' : '关'}</span>
        </label>
      </SettingsRow>

      <SettingsRow label="弹幕流" description="最近弹幕（叠加层同源数据，3s 轮询）">
        <div className="console-stack" style={{ width: '100%' }}>
          {messages.length === 0 ? (
            <SettingsStatusBadge tone="neutral">暂无弹幕</SettingsStatusBadge>
          ) : (
            messages.slice(-6).map((m, i) => (
              <div className="motion-frame" key={`${m.ts ?? i}-${i}`} style={{ gridTemplateColumns: '64px 1fr' }}>
                <span className="motion-frame-sec">{m.kind}</span>
                <span className="motion-frame-action">
                  {m.uname ? `${m.uname}：` : ''}
                  {m.text}
                </span>
              </div>
            ))
          )}
        </div>
      </SettingsRow>

      <SettingsRow label="测试弹幕">
        <button type="button" className="console-btn" onClick={() => void testSend()}>
          发送测试弹幕
        </button>
      </SettingsRow>

      <SettingsRow label="状态">
        <SettingsStatusBadge tone="ok">{notice || '指令分发已就绪'}</SettingsStatusBadge>
      </SettingsRow>
    </SettingsGroup>
  );
}

export function LiveObsSettings(): ReactElement {
  const [cfg, setCfg] = useState<LiveConfig | null>(null);
  const [obs, setObs] = useState<ObsStatus | null>(null);
  const [vts, setVts] = useState<VtsStatus | null>(null);
  const [notice, setNotice] = useState('');

  const refresh = async (): Promise<void> => {
    try {
      setCfg(await liveApi.config());
    } catch {
      /* noop */
    }
    try {
      setObs(await liveApi.obsStatus());
    } catch {
      /* noop */
    }
    try {
      setVts(await liveApi.vtsStatus());
    } catch {
      /* noop */
    }
  };

  useEffect(() => {
    void refresh();
    const timer = window.setInterval(refresh, 15_000);
    return () => window.clearInterval(timer);
  }, []);

  const saveObs = async (patch: Partial<LiveConfig>): Promise<void> => {
    try {
      setCfg(await liveApi.saveConfig(patch));
      void refresh();
    } catch {
      /* noop */
    }
  };

  const fire = async (fn: Promise<{ ok: boolean; error?: string }>, okMsg: string): Promise<void> => {
    const res = await fn;
    setNotice(res.ok ? okMsg : res.error ?? '失败');
    void refresh();
  };

  return (
    <SettingsGroup title="OBS 与表情" description="OBS 场景控制 + VTube Studio 表情动作">
      <SettingsRow label="OBS WebSocket">
        <div className="console-stack">
          <input
            type="text"
            className="console-input"
            defaultValue={cfg?.obs_host ?? '127.0.0.1'}
            placeholder="OBS host"
            onBlur={(e) => void saveObs({ obs_host: e.target.value })}
          />
          <input
            type="number"
            className="console-input"
            defaultValue={cfg?.obs_port ?? 4455}
            placeholder="OBS port"
            onBlur={(e) => void saveObs({ obs_port: Number(e.target.value) || 4455 })}
          />
        </div>
      </SettingsRow>

      <SettingsRow label="OBS 状态">
        <SettingsStatusBadge tone={obs?.online ? 'ok' : 'warn'}>
          {obs?.available ? (obs.online ? `在线（场景：${obs.scenes.join(' / ') || '—'}）` : '不可达') : obs?.error ?? '探测中'}
        </SettingsStatusBadge>
      </SettingsRow>

      <SettingsRow label="OBS 动作">
        <div className="console-btn-row">
          <button type="button" className="console-btn" onClick={() => void fire(liveApi.obsAction('change_scene', { scene: cfg?.obs_scene || '直播' }), '已切换场景')}>
            切到直播场景
          </button>
          <button type="button" className="console-btn" onClick={() => void fire(liveApi.obsAction('control_video', { input: '背景音乐', action: 'PAUSE' }), '已暂停 BGM')}>
            暂停 BGM
          </button>
          <button type="button" className="console-btn" onClick={() => void fire(liveApi.obsAction('control_video', { input: '背景音乐', action: 'PLAY' }), '已恢复 BGM')}>
            恢复 BGM
          </button>
        </div>
      </SettingsRow>

      <SettingsRow label="VTube Studio">
        <SettingsStatusBadge tone={vts?.online ? 'ok' : 'neutral'}>
          {vts?.online ? `在线（${vts.url}）` : '未连接（VTS 需打开并确认插件授权）'}
        </SettingsStatusBadge>
      </SettingsRow>

      <SettingsRow label="表情动作">
        <div className="console-btn-row">
          <button type="button" className="console-btn" onClick={() => void fire(liveApi.vtsAction('emote', { hotkey: '开心' }), '已触发表情')}>
            开心
          </button>
          <button type="button" className="console-btn" onClick={() => void fire(liveApi.vtsAction('swing'), '已启动摇摆')}>
            摇摆
          </button>
          <button type="button" className="console-btn" onClick={() => void fire(liveApi.vtsAction('stop'), '已停止')}>
            停止
          </button>
        </div>
      </SettingsRow>

      <SettingsRow label="状态">
        <SettingsStatusBadge tone="ok">{notice || 'OBS 需安装 obs-websocket-py 依赖后可用'}</SettingsStatusBadge>
      </SettingsRow>
    </SettingsGroup>
  );
}

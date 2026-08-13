import { useCallback, useEffect, useRef, useState, type ReactElement } from 'react';
import { singingApi, type SingingConfig, type SingingStatus } from '@/api/rest';
import { SettingsGroup, SettingsRow, SettingsStatusBadge } from '@/settings/SettingsConsole';

/**
 * 唱歌与音乐（P2）：点歌学唱 / 歌单队列 / 翻唱引擎。
 * 学歌依赖外部服务 Auto-Convert-Music（acm_url，GPU 独立部署），
 * 未部署时展示真实错误（fail-soft）；播放走前端 <audio>。
 */

const POLL_MS = 3000;

/** 播放器：当前可播歌曲（前端 <audio>）。 */
function SongPlayer({ song }: { song: SingingStatus['current'] }): ReactElement {
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const [ended, setEnded] = useState(false);
  useEffect(() => {
    setEnded(false);
  }, [song?.audio_url]);
  if (!song) {
    return <SettingsStatusBadge tone="neutral">未在播放</SettingsStatusBadge>;
  }
  return (
    <div className="console-stack">
      <SettingsStatusBadge tone="ok">正在演唱：{song.songname}</SettingsStatusBadge>
      <audio
        ref={audioRef}
        src={song.audio_url}
        controls
        autoPlay
        onEnded={() => setEnded(true)}
        style={{ width: '100%', maxWidth: 320 }}
      />
      {ended && <SettingsStatusBadge tone="neutral">播放完毕，可点「下一首」继续</SettingsStatusBadge>}
    </div>
  );
}

export function SingingRequestSettings(): ReactElement {
  const [cfg, setCfg] = useState<SingingConfig | null>(null);
  const [input, setInput] = useState('唱歌+打上花火');
  const [notice, setNotice] = useState('');
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    void singingApi.config().then(setCfg).catch(() => setNotice('无法连接后端'));
  }, []);

  const request = async (text?: string): Promise<void> => {
    const q = (text ?? input).trim();
    if (!q) return;
    setBusy(true);
    try {
      const res = await singingApi.request({ text: q });
      setNotice(res.ok ? `已入队：${res.songname}` : `未入队：${res.reason ?? '未知原因'}`);
    } catch {
      setNotice('请求失败（后端不可用？）');
    } finally {
      setBusy(false);
    }
  };

  const updateCfg = async (patch: Partial<SingingConfig>): Promise<void> => {
    try {
      setCfg(await singingApi.saveConfig(patch));
      setNotice('配置已保存（无需重启）');
    } catch {
      setNotice('配置保存失败');
    }
  };

  return (
    <SettingsGroup title="点歌学唱" description="「唱歌+歌名」→ 真实歌名校验 → Auto-Convert-Music 学歌 → 角色音色演唱">
      <SettingsRow label="学歌服务地址" description="Auto-Convert-Music（musicInfo / append_song）">
        <input
          type="text"
          className="console-input"
          value={cfg?.acm_url ?? ''}
          onChange={(e) => void updateCfg({ acm_url: e.target.value })}
          placeholder="http://127.0.0.1:1717"
        />
      </SettingsRow>

      <SettingsRow label="学歌超时" description="单曲学歌等待上限">
        <div className="range-row">
          <input
            type="range"
            min={60}
            max={900}
            step={10}
            value={cfg?.create_timeout ?? 500}
            onChange={(e) => void updateCfg({ create_timeout: Number(e.target.value) })}
          />
          <span className="range-val">{cfg?.create_timeout ?? 500}s</span>
        </div>
      </SettingsRow>

      <SettingsRow label="免学歌规则" description="命中正则的歌曲直接下载原曲不转换">
        <input
          type="text"
          className="console-input"
          value={cfg?.song_not_convert ?? ''}
          onChange={(e) => void updateCfg({ song_not_convert: e.target.value })}
          placeholder="如：(粤剧|京剧|易经)"
        />
      </SettingsRow>

      <SettingsRow label="指令演示" description="聊天里直接说「唱歌+歌名」也能触发（对话意图接入已打通）">
        <div className="console-stack">
          <input
            type="text"
            className="console-input"
            value={input}
            onChange={(e) => setInput(e.target.value)}
          />
          <div className="console-btn-row">
            <button type="button" className="console-btn" disabled={busy} onClick={() => void request()}>
              {busy ? '处理中…' : '点歌'}
            </button>
            <button type="button" className="console-btn" onClick={() => void request('唱歌+晴天')}>
              试试「唱歌+晴天」
            </button>
          </div>
        </div>
      </SettingsRow>

      <SettingsRow label="状态">
        <SettingsStatusBadge tone={notice.includes('未') ? 'warn' : 'ok'}>{notice || '就绪'}</SettingsStatusBadge>
      </SettingsRow>
    </SettingsGroup>
  );
}

export function SingingQueueSettings(): ReactElement {
  const [status, setStatus] = useState<SingingStatus | null>(null);
  const [notice, setNotice] = useState('');

  const refresh = useCallback(async (): Promise<void> => {
    try {
      setStatus(await singingApi.status());
    } catch {
      /* noop */
    }
  }, []);

  useEffect(() => {
    void refresh();
    const timer = window.setInterval(refresh, POLL_MS);
    return () => window.clearInterval(timer);
  }, [refresh]);

  const playNext = async (): Promise<void> => {
    const res = await singingApi.next();
    setNotice(res.ok ? `开始演唱：${res.song?.songname}` : `暂无：${res.reason ?? ''}`);
    void refresh();
  };

  const stopLearning = async (): Promise<void> => {
    await singingApi.stopLearning();
    setNotice('已发送停止学歌');
    void refresh();
  };

  const clear = async (): Promise<void> => {
    await singingApi.clear();
    setNotice('队列已清空');
    void refresh();
  };

  const learning = status?.learning ?? false;
  const progress = status?.progress ?? '';

  return (
    <SettingsGroup title="歌单与队列" description="点歌队列 · 学歌进度 · 演唱播放（前端 audio）">
      <SettingsRow label="当前状态">
        <SettingsStatusBadge tone={learning ? 'warn' : status?.current ? 'ok' : 'neutral'}>
          {learning ? '学歌中…' : status?.current ? '演唱中' : status?.state === 'error' ? '出错' : '空闲 IDLE'}
        </SettingsStatusBadge>
        {progress && (
          <span className="console-row-description" style={{ marginTop: 4 }}>
            {progress}
          </span>
        )}
      </SettingsRow>

      <SettingsRow label="正在演唱">
        <SongPlayer song={status?.current ?? null} />
      </SettingsRow>

      <SettingsRow label="队列长度" description={`点歌队列 ${status?.queue.length ?? 0} 首 · 待播放 ${status?.ready.length ?? 0} 首`}>
        <div className="console-tag-wrap">
          {status?.queue.length ? (
            status.queue.map((s) => (
              <span className="console-tag" key={`q-${s}`}>
                {s}
              </span>
            ))
          ) : (
            <SettingsStatusBadge tone="neutral">队列为空</SettingsStatusBadge>
          )}
        </div>
      </SettingsRow>

      <SettingsRow label="播放操作">
        <div className="console-btn-row">
          <button type="button" className="console-btn" onClick={() => void playNext()}>
            下一首
          </button>
          <button type="button" className="console-btn" onClick={() => void stopLearning()} disabled={!learning}>
            停止学歌
          </button>
          <button type="button" className="console-btn" onClick={() => void clear()}>
            清空队列
          </button>
        </div>
      </SettingsRow>

      <SettingsRow label="状态">
        <SettingsStatusBadge tone={status?.last_error ? 'warn' : 'ok'}>
          {notice || status?.last_error || '轮询中（3s）'}
        </SettingsStatusBadge>
      </SettingsRow>
    </SettingsGroup>
  );
}

export function SingingEngineSettings(): ReactElement {
  const [svcUrl, setSvcUrl] = useState('');
  const [engine, setEngine] = useState<{ configured: boolean; online: boolean; error: string | null } | null>(null);
  const [notice, setNotice] = useState('');

  useEffect(() => {
    void singingApi.engineStatus().then((st) => {
      setSvcUrl(st.svc_url);
      setEngine(st);
    });
  }, []);

  const saveAndProbe = async (): Promise<void> => {
    try {
      await singingApi.saveConfig({ svc_url: svcUrl } as never);
    } catch {
      /* noop */
    }
    const st = await singingApi.engineStatus();
    setEngine(st);
    setNotice(st.online ? 'so-vits 服务在线' : st.configured ? '服务不可达（未启动？）' : '未配置（需 GPU 部署）');
  };

  return (
    <SettingsGroup title="翻唱引擎" description="so-vits-svc 音色转换：任意歌曲人声 → 角色音色（需 GPU 独立部署）">
      <SettingsRow label="so-vits API 地址" description="flask_api_full_song 服务">
        <input
          type="text"
          className="console-input"
          value={svcUrl}
          onChange={(e) => setSvcUrl(e.target.value)}
          placeholder="http://127.0.0.1:5000"
        />
      </SettingsRow>

      <SettingsRow label="引擎状态">
        <SettingsStatusBadge tone={engine?.online ? 'ok' : engine?.configured ? 'warn' : 'neutral'}>
          {engine?.online ? '在线' : engine?.configured ? '不可达' : '未部署 · 需 GPU'}
        </SettingsStatusBadge>
        {engine?.error && <span className="console-row-description">{engine.error}</span>}
      </SettingsRow>

      <SettingsRow label="操作">
        <div className="console-btn-row">
          <button type="button" className="console-btn" onClick={() => void saveAndProbe()}>
            保存并探测
          </button>
        </div>
      </SettingsRow>

      <SettingsRow label="音色训练">
        <SettingsStatusBadge tone="neutral">训练向导需 so-vits webUI（GPU 环境）</SettingsStatusBadge>
      </SettingsRow>

      <SettingsRow label="状态">
        <SettingsStatusBadge tone="ok">{notice || '翻唱引擎为外部服务（P2 基础：探测 + 转换接口就绪）'}</SettingsStatusBadge>
      </SettingsRow>
    </SettingsGroup>
  );
}

import { useEffect, useState, type ReactElement } from 'react';
import { playmateApi, type PlaymateConfig, type PlaymateEvent, type PlaymateGame } from '@/api/rest';
import { SettingsGroup, SettingsRow, SettingsStatusBadge } from '@/settings/SettingsConsole';

/**
 * 游戏陪玩（P4）：目标游戏 / 画面事件 / 攻略知识库 / 高光喝彩。
 * 画面捕获复用 screen_awareness（前端 Electron IPC 上报），事件识别复用
 * 视觉链路 + LLM 分类（fail-soft）。
 */

export function PlaymateGameSettings(): ReactElement {
  const [games, setGames] = useState<PlaymateGame[]>([]);
  const [cfg, setCfg] = useState<PlaymateConfig | null>(null);
  const [selected, setSelected] = useState('');
  const [regex, setRegex] = useState('');
  const [notice, setNotice] = useState('');

  const refresh = async (): Promise<void> => {
    try {
      const [g, c] = await Promise.all([playmateApi.games(), playmateApi.config()]);
      setGames(g.games);
      setCfg(c);
      setSelected(c.active_game || '');
      setRegex(c.window_regex || '');
    } catch {
      /* noop */
    }
  };

  useEffect(() => {
    void refresh();
  }, []);

  const bind = async (): Promise<void> => {
    const body: { game_id?: string; window_regex?: string } = {};
    if (selected) body.game_id = selected;
    if (regex.trim()) body.window_regex = regex.trim();
    try {
      const next = await playmateApi.bind(body);
      setCfg(next);
      setNotice(`已绑定「${games.find((g) => g.id === next.active_game)?.name ?? next.active_game}」`);
    } catch {
      setNotice('绑定失败（正则无效？）');
    }
  };

  const update = async (patch: Partial<PlaymateConfig>): Promise<void> => {
    try {
      setCfg(await playmateApi.saveConfig(patch));
    } catch {
      /* noop */
    }
  };

  return (
    <SettingsGroup title="目标游戏" description="绑定游戏窗口，桌宠开始「看」你打游戏（复用屏幕感知链路）">
      <SettingsRow label="支持游戏">
        <div className="console-tag-wrap">
          {games.map((g) => (
            <button
              type="button"
              key={g.id}
              className={`console-tag${selected === g.id ? ' active' : ''}`}
              onClick={() => setSelected(g.id)}
            >
              {g.name}
            </button>
          ))}
        </div>
      </SettingsRow>

      <SettingsRow label="窗口匹配规则" description="自定义正则（覆盖内置，如 Minecraft|我的世界）">
        <input
          type="text"
          className="console-input"
          value={regex}
          onChange={(e) => setRegex(e.target.value)}
          placeholder="留空使用内置规则"
        />
      </SettingsRow>

      <SettingsRow label="画面采样频率">
        <div className="range-row">
          <input
            type="range"
            min={3}
            max={60}
            step={1}
            value={cfg?.sample_sec ?? 10}
            onChange={(e) => void update({ sample_sec: Number(e.target.value) })}
          />
          <span className="range-val">{cfg?.sample_sec ?? 10}s</span>
        </div>
      </SettingsRow>

      <SettingsRow label="绑定操作">
        <button type="button" className="console-btn" onClick={() => void bind()}>
          选择游戏并绑定
        </button>
      </SettingsRow>

      <SettingsRow label="状态">
        <SettingsStatusBadge tone={cfg?.active_game ? 'ok' : 'neutral'}>
          {notice || (cfg?.active_game ? `已绑定：${cfg.active_game}` : '未绑定')}
        </SettingsStatusBadge>
      </SettingsRow>
    </SettingsGroup>
  );
}

export function PlaymateVisionSettings(): ReactElement {
  const [cfg, setCfg] = useState<PlaymateConfig | null>(null);
  const [events, setEvents] = useState<PlaymateEvent[]>([]);

  useEffect(() => {
    const timer = window.setInterval(() => {
      void playmateApi.status().then((s) => {
        setCfg(s.config);
        setEvents(s.events);
      }).catch(() => undefined);
    }, 5000);
    return () => window.clearInterval(timer);
  }, []);

  const toggleVision = async (v: boolean): Promise<void> => {
    try {
      setCfg(await playmateApi.saveConfig({ vision_enabled: v }));
    } catch {
      /* noop */
    }
  };

  return (
    <SettingsGroup title="画面识别" description="游戏画面 → 视觉模型描述 → LLM 事件分类（击杀/胜利/掉落等）">
      <SettingsRow label="视觉模型" description="复用屏幕感知视觉 provider（Qwen3-VL）">
        <SettingsStatusBadge tone="ok">SiliconFlow · Qwen3-VL-8B（继承屏幕感知配置）</SettingsStatusBadge>
      </SettingsRow>

      <SettingsRow label="事件识别">
        <label className="toggle-row">
          <input type="checkbox" checked={cfg?.vision_enabled ?? true} onChange={(e) => void toggleVision(e.target.checked)} />
          <span>{cfg?.vision_enabled ? '开' : '关'}</span>
        </label>
      </SettingsRow>

      <SettingsRow label="最近识别" description="最近事件流（5s 轮询）">
        <div className="console-stack" style={{ width: '100%' }}>
          {events.length === 0 ? (
            <SettingsStatusBadge tone="neutral">暂无事件（打开游戏后画面识别自动上报）</SettingsStatusBadge>
          ) : (
            events.slice(-6).map((e, i) => (
              <div className="motion-frame" key={`${e.ts}-${i}`} style={{ gridTemplateColumns: '72px 1fr' }}>
                <span className="motion-frame-sec">{e.event_type}</span>
                <span className="motion-frame-action">
                  {e.summary}
                  {e.cheer && <em style={{ color: '#6ee7a8', marginLeft: 6 }}>🎉 {e.cheer}</em>}
                </span>
              </div>
            ))
          )}
        </div>
      </SettingsRow>

      <SettingsRow label="识别成本">
        <SettingsStatusBadge tone="neutral">计入屏幕感知 metrics（/api/screen/metrics 同源）</SettingsStatusBadge>
      </SettingsRow>

      <SettingsRow label="状态">
        <SettingsStatusBadge tone="ok">画面事件链路已就绪</SettingsStatusBadge>
      </SettingsRow>
    </SettingsGroup>
  );
}

export function PlaymateKbSettings(): ReactElement {
  const [kbStats, setKbStats] = useState<Record<string, { chunks: number; indexed: number }>>({});
  const [gameId, setGameId] = useState('minecraft');
  const [text, setText] = useState('');
  const [question, setQuestion] = useState('');
  const [hits, setHits] = useState<{ text: string; score: number }[]>([]);
  const [notice, setNotice] = useState('');

  useEffect(() => {
    void playmateApi.status().then((s) => {
      setKbStats(s.kb);
      if (s.config.active_game) setGameId(s.config.active_game);
    }).catch(() => undefined);
  }, []);

  const doImport = async (): Promise<void> => {
    if (!text.trim()) return;
    const res = await playmateApi.kbImport({ game_id: gameId, text });
    setNotice(res.ok ? `已导入 ${res.imported} 个分块` : `导入失败：${res.error ?? ''}`);
    void playmateApi.status().then((s) => setKbStats(s.kb)).catch(() => undefined);
  };

  const doQuery = async (): Promise<void> => {
    if (!question.trim()) return;
    const res = await playmateApi.kbQuery({ game_id: gameId, question });
    setHits(res.ok ? res.hits : []);
    if (!res.ok) setNotice(`检索失败：${res.error ?? ''}`);
  };

  return (
    <SettingsGroup title="攻略知识库" description="每游戏独立攻略向量库（RAG），回答角色/任务/宝箱问题">
      <SettingsRow label="目标游戏">
        <select
          className="console-input"
          style={{ maxWidth: 200 }}
          value={gameId}
          onChange={(e) => setGameId(e.target.value)}
        >
          {Object.keys(kbStats).map((id) => (
            <option value={id} key={id}>
              {id}（{kbStats[id]?.chunks ?? 0} 块）
            </option>
          ))}
        </select>
      </SettingsRow>

      <SettingsRow label="导入攻略" description="粘贴攻略文本 → 分块向量化（复用记忆 embedding 配置）">
        <div className="console-stack">
          <textarea
            className="console-textarea"
            rows={4}
            value={text}
            onChange={(e) => setText(e.target.value)}
            placeholder="粘贴攻略正文…（自动按段落切块）"
          />
          <button type="button" className="console-btn" onClick={() => void doImport()}>
            导入
          </button>
        </div>
      </SettingsRow>

      <SettingsRow label="测试问答">
        <div className="console-stack">
          <input
            type="text"
            className="console-input"
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            placeholder="如：风龙废墟怎么开？"
          />
          <button type="button" className="console-btn" onClick={() => void doQuery()}>
            检索
          </button>
          {hits.length > 0 && (
            <div className="console-stack" style={{ width: '100%' }}>
              {hits.map((h, i) => (
                <div className="motion-frame" key={i} style={{ gridTemplateColumns: '48px 1fr' }}>
                  <span className="motion-frame-sec">{(h.score * 100).toFixed(0)}%</span>
                  <span className="motion-frame-action">{h.text.slice(0, 120)}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      </SettingsRow>

      <SettingsRow label="状态">
        <SettingsStatusBadge tone="ok">
          {notice || `已导入：${Object.values(kbStats).reduce((a, b) => a + (b?.chunks ?? 0), 0)} 块（向量检索 · RRF 融合 P4.1）`}
        </SettingsStatusBadge>
      </SettingsRow>
    </SettingsGroup>
  );
}

export function PlaymateCheerSettings(): ReactElement {
  const [cfg, setCfg] = useState<PlaymateConfig | null>(null);
  const [notice, setNotice] = useState('');

  useEffect(() => {
    void playmateApi.config().then(setCfg).catch(() => undefined);
  }, []);

  const update = async (patch: Partial<PlaymateConfig>): Promise<void> => {
    try {
      setCfg(await playmateApi.saveConfig(patch));
    } catch {
      /* noop */
    }
  };

  const testCheer = async (): Promise<void> => {
    const ev = await playmateApi.cheer();
    setNotice(ev.cheer ? `🎉 ${ev.cheer}` : '冷却中（喝彩有 60s 冷却）');
  };

  return (
    <SettingsGroup title="高光喝彩" description="击杀 / 胜利 / 升级时主动搭话喝彩（事件触发 + 冷却去重）">
      <SettingsRow label="高光喝彩">
        <label className="toggle-row">
          <input type="checkbox" checked={cfg?.cheer_enabled ?? true} onChange={(e) => void update({ cheer_enabled: e.target.checked })} />
          <span>{cfg?.cheer_enabled ? '开' : '关'}</span>
        </label>
      </SettingsRow>

      <SettingsRow label="喝彩冷却">
        <div className="range-row">
          <input
            type="range"
            min={10}
            max={600}
            step={10}
            value={cfg?.cheer_cooldown ?? 60}
            onChange={(e) => void update({ cheer_cooldown: Number(e.target.value) })}
          />
          <span className="range-val">{cfg?.cheer_cooldown ?? 60}s</span>
        </div>
      </SettingsRow>

      <SettingsRow label="话术模板" description="按事件类型随机（kill/victory/boss/capture/levelup/research）">
        <SettingsStatusBadge tone="neutral">后端内置 · 可后续开放编辑</SettingsStatusBadge>
      </SettingsRow>

      <SettingsRow label="测试">
        <button type="button" className="console-btn" onClick={() => void testCheer()}>
          触发一次喝彩
        </button>
      </SettingsRow>

      <SettingsRow label="状态">
        <SettingsStatusBadge tone="ok">{notice || '喝彩链路就绪（进对话 TTS 播报 P4.1）'}</SettingsStatusBadge>
      </SettingsRow>
    </SettingsGroup>
  );
}

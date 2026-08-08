import { useCallback, useEffect, useState, type ReactElement } from 'react';
import {
  perfApi,
  engineApi,
  ApiError,
  type PerfResult,
  type EnginesResult,
  type EngineInfo,
  type VoiceVoxStatus,
} from '@/api/rest';
import { SettingStatus } from './SettingStatus';

/**
 * ASR / TTS 引擎设置（UI 重设计 v2）
 *
 * 结构：
 *  - 分段控件：语音识别（ASR）｜语音合成（TTS）—— 按域切换，降低信息密度
 *  - 当前引擎面板：当前生效引擎高亮展示 + 内嵌切换（中文名 + 配置状态标注）
 *  - 引擎库网格：状态点 + 中文名 + 分类标签 + 描述；点击展开字段表单
 *  - VOICEVOX（仅 TTS）：本地引擎下载 / 启动 / 停止 + 进度轮询
 *  - 更多设置（折叠）：keep_alive / 记忆整理 / 性能预设 —— 杂项收纳，不干扰主流程
 */

type TabKey = 'asr' | 'tts';

const KIND_LABELS: Record<string, string> = {
  cloud: '云端 API',
  local: '本地模型',
  local_service: '本地服务',
  builtin: '内置',
};

function errorMessage(err: unknown): string {
  if (err instanceof ApiError) return err.message;
  return err instanceof Error ? err.message : '发生未知错误';
}

export function PerfSettings(): ReactElement {
  const [tab, setTab] = useState<TabKey>('tts');
  const [perf, setPerf] = useState<PerfResult | null>(null);
  const [engines, setEngines] = useState<EnginesResult | null>(null);
  const [expanded, setExpanded] = useState('');
  const [form, setForm] = useState<Record<string, string>>({});
  const [saving, setSaving] = useState(false);
  const [vv, setVv] = useState<VoiceVoxStatus | null>(null);
  const [vvBusy, setVvBusy] = useState(false);
  const [showStartGuide, setShowStartGuide] = useState(false);
  const [status, setStatus] = useState('');
  /** 引擎库只显示已配置可用的引擎（默认开；关闭可看到全部引擎用于配置）。 */
  const [onlyReady, setOnlyReady] = useState(true);

  /** 终端启动后端的命令（PowerShell / CMD 均可）。 */
  const BACKEND_START_CMD =
    'cd C:\\Users\\Elysia\\Desktop\\Project\\Pycharm\\Moonlight\\backend && uv run run_server.py';

  const copyStartCmd = async (): Promise<void> => {
    try {
      await navigator.clipboard.writeText(BACKEND_START_CMD);
      setStatus('已复制启动命令，粘贴到 PowerShell/CMD 运行即可（先停掉当前后端）');
    } catch {
      setStatus(`复制失败，请手动输入：${BACKEND_START_CMD}`);
    }
  };

  const load = useCallback(async (): Promise<void> => {
    try {
      const [perfRes, engRes, vvRes] = await Promise.all([
        perfApi.get(),
        engineApi.list('all'),
        engineApi.voicevoxStatus(),
      ]);
      setPerf(perfRes);
      setEngines(engRes);
      setVv(vvRes.voicevox);
    } catch (err) {
      setStatus(errorMessage(err));
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  // VOICEVOX 状态轮询：下载中，或引擎卡片展开时（方便手动启动引擎后自动感知）
  useEffect(() => {
    const active = vv?.state === 'downloading' || expanded === 'voicevox_tts';
    if (!active) return;
    const t = window.setInterval(async () => {
      try {
        const res = await engineApi.voicevoxStatus();
        // 状态没变化时保持原引用，避免无谓 re-render
        setVv((prev) => {
          const next = res.voicevox;
          if (
            prev &&
            prev.state === next.state &&
            prev.running === next.running &&
            prev.progress === next.progress
          ) {
            return prev;
          }
          return next;
        });
      } catch {
        // 轮询失败静默，下轮重试
      }
    }, 2500);
    return () => window.clearInterval(t);
  }, [vv?.state, expanded]);

  const apply = (fn: () => Promise<unknown>): void => {
    void fn()
      .then(() => setStatus('已保存（重启或重新选择角色后生效）'))
      .catch((err: unknown) => setStatus(errorMessage(err)));
  };

  const expandEngine = (engine: EngineInfo): void => {
    if (expanded === engine.key) {
      setExpanded('');
      return;
    }
    setExpanded(engine.key);
    const init: Record<string, string> = {};
    engine.fields.forEach((f) => (init[f.key] = ''));
    setForm(init);
  };

  const saveEngine = async (scope: TabKey, engine: string): Promise<void> => {
    const fields = Object.fromEntries(
      Object.entries(form).filter(([, v]) => v.trim() !== ''),
    );
    if (Object.keys(fields).length === 0) {
      setStatus('没有可保存的字段值');
      return;
    }
    setSaving(true);
    try {
      await engineApi.save(engine, scope, fields);
      setExpanded('');
      setForm({});
      const engRes = await engineApi.list('all');
      setEngines(engRes);
      setStatus('已保存。重启或重新选择角色后生效；配置好的引擎会出现在角色卡中');
    } catch (err) {
      setStatus(errorMessage(err));
    } finally {
      setSaving(false);
    }
  };

  // ------------------------------------------------------------------ //
  // VOICEVOX 管理
  // ------------------------------------------------------------------ //
  const vvDownload = (useMirror: boolean): void => {
    setVvBusy(true);
    void engineApi
      .voicevoxDownload(useMirror)
      .then((r) => {
        if (!r.ok && r.error) setStatus(r.error);
        else setStatus(r.msg ?? '下载已开始（约几百 MB，请保持后端运行）');
        return engineApi.voicevoxStatus();
      })
      .then((r) => setVv(r.voicevox))
      .catch((err: unknown) => setStatus(errorMessage(err)))
      .finally(() => setVvBusy(false));
  };

  const vvAction = (action: 'start' | 'stop'): void => {
    setVvBusy(true);
    setStatus(action === 'start' ? '正在启动引擎，请稍候（首次约需几秒～几十秒）…' : '正在停止引擎…');
    const fn = action === 'start' ? engineApi.voicevoxStart : engineApi.voicevoxStop;
    void fn()
      .then(async (r) => {
        // 先刷新一次真实状态，再决定提示文案（以状态为准，而不是只看返回）
        const res = await engineApi.voicevoxStatus();
        setVv(res.voicevox);
        await load();
        if (action === 'start' && res.voicevox.running) {
          setStatus('✅ 引擎已启动：运行于 127.0.0.1:50021，现在可以在角色卡选择 VOICEVOX 试听');
        } else if (action === 'stop' && !res.voicevox.running) {
          setStatus('✅ 引擎已停止');
        } else {
          setStatus(r.msg ?? (r.ok ? '操作成功' : '操作失败'));
        }
      })
      .catch((err: unknown) => setStatus(errorMessage(err)))
      .finally(() => setVvBusy(false));
  };

  // ------------------------------------------------------------------ //
  // 派生数据
  // ------------------------------------------------------------------ //
  const allList: EngineInfo[] | undefined = tab === 'tts' ? engines?.tts : engines?.asr;
  /** 引擎库列表：默认只显示已配置可用的引擎，关闭开关后显示全部。 */
  const list: EngineInfo[] | undefined = allList?.filter((e) => !onlyReady || e.configured);
  const currentKey = tab === 'tts' ? perf?.tts_model : perf?.asr_model;
  const current = list?.find((e) => e.key === currentKey);
  const currentVoice = tab === 'tts' ? perf?.tts_voice : undefined;

  const engineLabel = (key: string, scope: TabKey): string => {
    const l = scope === 'tts' ? engines?.tts : engines?.asr;
    const e = l?.find((x) => x.key === key);
    if (!e) return key;
    return e.configured ? e.zh : `${e.zh}（未配置）`;
  };

  const switchCurrent = (key: string): void => {
    if (tab === 'asr') apply(() => perfApi.setAsr({ asr_model: key }));
    else apply(() => perfApi.setTts({ tts_model: key }));
  };

  // ------------------------------------------------------------------ //
  // 渲染
  // ------------------------------------------------------------------ //
  return (
    <div className="settings-section engine-settings">
      <div className="engine-settings-head">
        <div className="engine-tabs" role="tablist">
          <button
            className={`engine-tab ${tab === 'asr' ? 'active' : ''}`}
            onClick={() => setTab('asr')}
            role="tab"
            aria-selected={tab === 'asr'}
          >
            <span className="engine-tab-dot" data-kind="asr" />
            语音识别
          </button>
          <button
            className={`engine-tab ${tab === 'tts' ? 'active' : ''}`}
            onClick={() => setTab('tts')}
            role="tab"
            aria-selected={tab === 'tts'}
          >
            <span className="engine-tab-dot" data-kind="tts" />
            语音合成
          </button>
        </div>
        <span className="engine-settings-sub">
          {tab === 'asr' ? '把你说的话转成文字' : '把回复念给你听'}
        </span>
      </div>

      {/* 当前引擎面板 */}
      <div className={`engine-current ${current?.configured ? 'ready' : ''}`}>
        <div className="engine-current-main">
          <span className={`engine-status-dot ${current?.configured ? 'on' : 'off'}`} />
          <div className="engine-current-info">
            <div className="engine-current-name">
              {current?.zh ?? currentKey ?? '未选择'}
              {current?.configured ? (
                <span className="engine-badge ok">已就绪</span>
              ) : (
                <span className="engine-badge warn">未配置</span>
              )}
            </div>
            <div className="engine-current-desc">
              {current?.desc ?? '从下方引擎库选择一个引擎'}
              {currentVoice ? ` · 音色 ${currentVoice}` : ''}
            </div>
          </div>
        </div>
        <label className="engine-current-switch">
          <span>切换引擎</span>
          <select
            value={currentKey ?? ''}
            onChange={(e) => switchCurrent(e.target.value)}
          >
            {(tab === 'asr' ? perf?.asr_models ?? [] : perf?.tts_models ?? []).map((m) => (
              <option key={m} value={m}>
                {engineLabel(m, tab)}
              </option>
            ))}
          </select>
        </label>
      </div>

      {/* 引擎库 */}
      <div className="engine-section-label">
        <span>引擎库</span>
        <label
          className="engine-only-ready"
          title="关闭后可看到并配置其他引擎（Azure / MiniMax 等）"
        >
          <input
            type="checkbox"
            checked={onlyReady}
            onChange={(e) => setOnlyReady(e.target.checked)}
          />
          仅显示可用引擎
        </label>
        <span className="engine-section-count">{list?.length ?? 0} 个引擎</span>
      </div>
      <div className="engine-grid">
        {(list ?? []).map((e) => (
          <div
            key={e.key}
            className={`engine-card ${e.configured ? 'ready' : ''} ${expanded === e.key ? 'open' : ''}`}
          >
            <div className="engine-card-head" onClick={() => expandEngine(e)}>
              <span className={`engine-status-dot ${e.configured ? 'on' : 'off'}`} />
              <div className="engine-card-info">
                <div className="engine-card-name">
                  <span className="engine-card-title">{e.zh}</span>
                  <span className="engine-card-kind">{KIND_LABELS[e.kind] ?? e.kind}</span>
                </div>
                <div className="engine-card-desc">{e.desc}</div>
              </div>
              <button
                className={`engine-card-btn ${expanded === e.key ? 'active' : ''}`}
                onClick={(ev) => {
                  ev.stopPropagation();
                  expandEngine(e);
                }}
              >
                {expanded === e.key ? '收起' : e.configured ? '详情' : '配置'}
              </button>
            </div>
            {!e.configured && (
              <div className="engine-card-reason">
                <span className="engine-reason-dot" />
                {e.reason}
              </div>
            )}
            {expanded === e.key && (
              e.key === 'voicevox_tts' ? (
                /* VOICEVOX 特殊卡：展开后是本地引擎管理（下载/启动/停止），并入引擎库 */
                <div className="engine-card-config voicevox-inline">
                  <div className="voicevox-state-row">
                    <span className={`engine-badge ${vv?.running ? 'ok' : vv?.state === 'failed' ? 'err' : 'warn'}`}>
                      {vv?.running
                        ? '运行中'
                        : vv?.state === 'downloading'
                          ? `下载中 ${vv.progress}%`
                          : vv?.state === 'downloaded'
                            ? '已下载 · 未启动'
                            : vv?.state === 'failed'
                              ? '下载失败'
                              : '未安装'}
                    </span>
                    <span className="voicevox-state-text">
                      {vv?.running
                        ? '引擎运行于 127.0.0.1:50021（日语语音，含萝莉音）'
                        : vv?.state === 'downloading'
                          ? `${vv.phase === 'downloading' ? `正在下载 ${vv.progress}%` : vv.phase}${vv.size_mb ? `（约 ${vv.size_mb} MB）` : ''}，下载完成后请点击启动`
                          : vv?.state === 'downloaded'
                            ? '引擎已就绪，点击「启动引擎」即可在角色卡中使用'
                            : vv?.state === 'failed'
                              ? `下载失败：${String(vv.phase ?? '').replace(/^failed:\s*/, '')}（可重试或换镜像）`
                              : '下载约 1.7 GB 到 backend/vendor/voicevox_engine/，支持直连或镜像加速'}
                    </span>
                  </div>
                  {vv?.state === 'downloading' && (
                    <div className="voicevox-progress">
                      <div className="voicevox-progress-bar" style={{ width: `${vv.progress}%` }} />
                    </div>
                  )}
                  <div className="voicevox-card-actions">
                    {(!vv || vv.state === 'missing' || vv.state === 'failed') && (
                      <>
                        <button className="btn btn-primary" disabled={vvBusy} onClick={() => vvDownload(false)}>
                          {vvBusy ? '处理中…' : '下载引擎'}
                        </button>
                        <button className="btn" disabled={vvBusy} onClick={() => vvDownload(true)}>
                          镜像加速下载
                        </button>
                      </>
                    )}
                    {vv?.state === 'downloaded' && !vv.running && (
                      <button className="btn btn-primary" disabled={vvBusy} onClick={() => vvAction('start')}>
                        {vvBusy ? '启动中…' : '启动引擎'}
                      </button>
                    )}
                    {vv?.running && (
                      <button className="btn btn-danger" disabled={vvBusy} onClick={() => vvAction('stop')}>
                        {vvBusy ? '停止中…' : '停止引擎'}
                      </button>
                    )}
                    {vv && (vv.state === 'downloaded' || vv.running) && (
                      <button
                        className="btn"
                        disabled={vvBusy}
                        title="手动启动引擎后，点击立即刷新运行状态（卡片展开时也会每 2.5s 自动检测）"
                        onClick={() =>
                          void engineApi.voicevoxStatus().then((r) => {
                            setVv(r.voicevox);
                            setStatus(r.voicevox.running ? '✅ 检测到引擎运行中（127.0.0.1:50021）' : '引擎未在运行');
                          })
                        }
                      >
                        检测状态
                      </button>
                    )}
                    <button
                      className="btn"
                      disabled={vvBusy}
                      title="后端被沙箱限制无法自动拉起引擎时，在终端启动后端的方法"
                      onClick={() => setShowStartGuide((v) => !v)}
                    >
                      {showStartGuide ? '收起启动方法' : '启动方法'}
                    </button>
                  </div>
                  {showStartGuide && (
                    <div className="voicevox-start-guide">
                      <div className="setting-note">
                        若「启动引擎」提示无法写入用户数据目录（运行环境沙箱限制），请先停掉当前后端，在 PowerShell/CMD 中运行以下命令启动后端，再回此页点「启动引擎」：
                      </div>
                      <div className="voicevox-start-cmd">
                        <code>{BACKEND_START_CMD}</code>
                        <button className="btn" onClick={() => void copyStartCmd()}>
                          复制命令
                        </button>
                      </div>
                    </div>
                  )}
                </div>
              ) : (
              <div className="engine-card-config">
                {e.fields.length === 0 ? (
                  <div className="setting-note">该引擎无需额外配置，即开即用。</div>
                ) : (
                  e.fields.map((f) => (
                    <label key={f.key} className="field">
                      <span>{f.label}</span>
                      <input
                        type={f.type === 'password' ? 'password' : f.type === 'number' ? 'number' : 'text'}
                        value={form[f.key] ?? ''}
                        placeholder={f.placeholder}
                        onChange={(ev) => setForm({ ...form, [f.key]: ev.target.value })}
                      />
                    </label>
                  ))
                )}
                {e.fields.length > 0 && (
                  <div className="engine-config-actions">
                    <button
                      className="btn btn-primary"
                      disabled={saving}
                      onClick={() => void saveEngine(tab, e.key)}
                    >
                      {saving ? '保存中…' : '保存配置'}
                    </button>
                  </div>
                )}
              </div>
              )
            )}
          </div>
        ))}
      </div>

      {/* 更多设置已移至「对话大脑 LLM」页（性能与记忆） */}

      <SettingStatus message={status} />
    </div>
  );
}

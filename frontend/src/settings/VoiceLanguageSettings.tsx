import { useEffect, useState, type ReactElement } from 'react';
import { playerApi, translatorApi, deeplxApi, type TranslatorConfig, type DeeplxStatus } from '@/api/rest';
import { useAppState } from '@/state/AppStateContext';

const LANGUAGES = [
  { value: '', label: '自动（跟随角色）' },
  { value: 'zh-CN', label: '简体中文' },
  { value: 'zh-TW', label: '繁体中文' },
  { value: 'en', label: 'English' },
  { value: 'ja', label: '日本語' },
  { value: 'ko', label: '한국어' },
];

/**
 * 语音页 · 语言与翻译（从原「通用」页拆分而来，重设计 v4）：
 * 回复语言 + 字幕翻译（双语气泡）+ 跨语音翻译。语言本质属于语音链路。
 *
 * UX 修复（2026-08-10）：subtitleEnabled 改存全局 settings（后端 ui_prefs 的
 * subtitle_enabled），作为 ChatBubble 的渲染闸门——开关关闭时历史/缓存消息
 * 里已带的 subtitle 也一律不显示，修复「有些有翻译有些没有」的不一致。
 *
 * 翻译引擎切换（2026-08-10 恢复）：跨语音翻译可选「本地 DeepLX」（毫秒级、免费，
 * 需本地部署 deeplx）或「LLM API」（更自然，但每句 5~10s 网络往返）。同时后端
 * 已做整段聚合翻译 + 异步化 + 文本先上屏，LLM 引擎的感知延迟大幅降低。
 */
export function VoiceLanguageSettings(): ReactElement {
  const { state, dispatch } = useAppState();
  const [language, setLanguage] = useState('');
  const [voiceEnabled, setVoiceEnabled] = useState(false);
  const [voiceTarget, setVoiceTarget] = useState('');
  const [engine, setEngine] = useState<'llm' | 'deeplx'>('llm');
  const [deeplxEndpoint, setDeeplxEndpoint] = useState('');
  const [subtitleTarget, setSubtitleTarget] = useState('');
  const [status, setStatus] = useState('');
  // DeepLX 本地服务状态（一键启动/停止，与 VOICEVOX 引擎管理同一模式）
  const [dx, setDx] = useState<DeeplxStatus | null>(null);
  const [dxBusy, setDxBusy] = useState(false);

  const subtitleEnabled = state.settings.subtitleEnabled;

  useEffect(() => {
    void playerApi
      .getLanguage()
      .then((r) => setLanguage(r.language))
      .catch(() => undefined);
    void translatorApi
      .get()
      .then((r: TranslatorConfig) => {
        setVoiceEnabled(r.enabled);
        setVoiceTarget(r.llm_target_lang);
        setEngine(r.engine);
        setDeeplxEndpoint(r.deeplx_endpoint);
        setSubtitleEnabled(r.translate_subtitle);
        setSubtitleTarget(r.subtitle_target_lang);
        if (r.engine === 'deeplx') void refreshDeeplx();
      })
      .catch(() => undefined);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const refreshDeeplx = async (): Promise<void> => {
    try {
      const res = await deeplxApi.status();
      // 状态没变化时保持原引用，避免无谓 re-render
      setDx((prev) =>
        prev &&
        prev.running === res.deeplx.running &&
        prev.exe_exists === res.deeplx.exe_exists &&
        prev.rate_limited === res.deeplx.rate_limited &&
        prev.last_error === res.deeplx.last_error &&
        prev.msg === res.deeplx.msg
          ? prev
          : res.deeplx,
      );
    } catch {
      // 后端不可达时静默，下轮重试
    }
  };

  // DeepLX 状态轮询：选中 deeplx 引擎时自动感知（启动/手动启动后 2.5s 内反映）
  useEffect(() => {
    if (engine !== 'deeplx') return;
    void refreshDeeplx();
    const t = window.setInterval(() => void refreshDeeplx(), 2500);
    return () => window.clearInterval(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [engine]);

  const startDeepLX = async (): Promise<void> => {
    if (dxBusy) return;
    setDxBusy(true);
    setStatus('正在启动 DeepLX…');
    try {
      const res = await deeplxApi.start();
      setStatus(res.msg);
      await refreshDeeplx();
    } catch (err) {
      setStatus(err instanceof Error ? err.message : '启动失败');
    } finally {
      setDxBusy(false);
    }
  };

  const stopDeepLX = async (): Promise<void> => {
    if (dxBusy) return;
    setDxBusy(true);
    try {
      const res = await deeplxApi.stop();
      setStatus(res.msg);
      await refreshDeeplx();
    } catch (err) {
      setStatus(err instanceof Error ? err.message : '停止失败');
    } finally {
      setDxBusy(false);
    }
  };

  const setSubtitleEnabled = (v: boolean): void => {
    // 同步到全局 settings → ChatBubble 渲染闸门即时生效（ui_prefs 自动落盘）
    dispatch({ type: 'UPDATE_SETTINGS', settings: { subtitleEnabled: v } });
  };

  const saveLanguage = async (): Promise<void> => {
    setStatus('保存中…');
    try {
      await playerApi.setLanguage({ language });
      setStatus('回复语言已保存');
    } catch (err) {
      setStatus(err instanceof Error ? err.message : '保存失败');
    }
  };

  const saveTranslator = async (): Promise<void> => {
    setStatus('保存中…');
    try {
      const body: Record<string, unknown> = {
        enabled: voiceEnabled,
        engine,
        translate_subtitle: subtitleEnabled,
        subtitle_target_lang: subtitleTarget,
      };
      if (engine === 'llm') {
        body.llm_target_lang = voiceTarget;
      } else {
        body.deeplx_target_lang = voiceTarget;
        body.deeplx_endpoint = deeplxEndpoint;
      }
      await translatorApi.save(body);
      setStatus('翻译设置已保存（重新选择角色后生效）');
    } catch (err) {
      setStatus(err instanceof Error ? err.message : '保存失败');
    }
  };

  return (
    <div className="settings-section">
      <section className="settings-card">
        <div className="settings-card-head">
          <span className="settings-card-icon">
            <IconLanguages />
          </span>
          <div className="settings-card-title">
            <h3>回复语言</h3>
          </div>
        </div>
        <div className="settings-card-body">
          <label className="field">
            <span>回复语言</span>
            <select value={language} onChange={(e) => setLanguage(e.target.value)}>
              {LANGUAGES.map((l) => (
                <option key={l.value} value={l.value}>
                  {l.label}
                </option>
              ))}
            </select>
          </label>
          <div className="btn-row">
            <button className="btn btn-primary" onClick={() => void saveLanguage()}>
              保存
            </button>
          </div>
        </div>
      </section>

      <section className="settings-card">
        <div className="settings-card-head">
          <span className="settings-card-icon">
            <IconSubtitles />
          </span>
          <div className="settings-card-title">
            <h3>字幕翻译 · 双语气泡</h3>
          </div>
        </div>
        <div className="settings-card-body">
          <label className="toggle-row">
            <span>翻译字幕</span>
            <input
              type="checkbox"
              checked={subtitleEnabled}
              onChange={(e) => setSubtitleEnabled(e.target.checked)}
            />
          </label>
          {subtitleEnabled && (
            <label className="field">
              <span>字幕目标语言</span>
              <input
                value={subtitleTarget}
                onChange={(e) => setSubtitleTarget(e.target.value)}
                placeholder="如 英语 / 日本語"
              />
            </label>
          )}
        </div>
      </section>

      <section className="settings-card">
        <div className="settings-card-head">
          <span className="settings-card-icon">
            <IconVolume />
          </span>
          <div className="settings-card-title">
            <h3>跨语音翻译</h3>
          </div>
        </div>
        <div className="settings-card-body">
          <label className="toggle-row">
            <span>启用语音翻译</span>
            <input
              type="checkbox"
              checked={voiceEnabled}
              onChange={(e) => setVoiceEnabled(e.target.checked)}
            />
          </label>
          {voiceEnabled && (
            <>
              <label className="field">
                <span>翻译引擎</span>
                <select
                  value={engine}
                  onChange={(e) => setEngine(e.target.value as 'llm' | 'deeplx')}
                >
                  <option value="llm">LLM API（DeepSeek 等，翻译更自然）</option>
                  <option value="deeplx">本地 DeepLX（毫秒级、免费，需本地部署）</option>
                </select>
              </label>
              {engine === 'deeplx' && (
                <label className="field">
                  <span>DeepLX 端点</span>
                  <input
                    value={deeplxEndpoint}
                    onChange={(e) => setDeeplxEndpoint(e.target.value)}
                    placeholder="http://localhost:1188/v2/translate"
                  />
                </label>
              )}
              <label className="field">
                <span>语音目标语言</span>
                <input
                  value={voiceTarget}
                  onChange={(e) => setVoiceTarget(e.target.value)}
                  placeholder={engine === 'deeplx' ? '如 JA / EN-US' : '如 日文 / 英文'}
                />
              </label>
              {engine === 'deeplx' && (
                <div className="deeplx-service-box">
                  <div className="voicevox-state-row">
                    <span
                      className={`engine-badge ${
                        dx?.rate_limited
                          ? 'err'
                          : dx?.running
                            ? 'ok'
                            : dx?.exe_exists
                              ? 'warn'
                              : 'err'
                      }`}
                    >
                      {dx?.rate_limited
                        ? '限流中'
                        : dx?.running
                          ? '运行中'
                          : dx?.exe_exists
                            ? '已就绪 · 未启动'
                            : '未安装'}
                    </span>
                    <span className="voicevox-state-text">
                      {dx?.rate_limited
                        ? 'DeepL 官方已临时限制本机 IP（429），翻译会失败、语音会静默跳过'
                        : dx?.running
                          ? '本地翻译服务运行于 127.0.0.1:1188（毫秒级、免费）'
                          : dx?.exe_exists
                            ? '已随项目内置，点「一键启动」即可（或手动运行 backend/vendor/deeplx/start_deeplx.bat）'
                            : 'deeplx.exe 未找到，请检查 backend/vendor/deeplx/'}
                    </span>
                  </div>
                  {dx?.rate_limited && (
                    <p className="deeplx-rate-limit-hint">
                      建议切换到「LLM API」引擎继续翻译（当前已自动回退原文朗读），
                      限流通常几小时自动解除，届时可切回 DeepLX。
                    </p>
                  )}
                  <div className="btn-row">
                    <button
                      className="btn btn-primary"
                      disabled={dx?.running === true || dxBusy}
                      onClick={() => void startDeepLX()}
                    >
                      {dxBusy ? '启动中…' : '一键启动 DeepLX'}
                    </button>
                    <button
                      className="btn"
                      disabled={dx?.running !== true || dxBusy}
                      onClick={() => void stopDeepLX()}
                    >
                      停止
                    </button>
                  </div>
                  <p className="field-hint">
                    未启动时自动回退原文朗读。目标语言用 DeepL 代码（JA / EN-US / ZH-HANS…）。
                  </p>
                </div>
              )}
            </>
          )}
          <div className="btn-row">
            <button className="btn" onClick={() => void saveTranslator()}>
              保存翻译设置
            </button>
          </div>
          {status && <div className="setting-status">{status}</div>}
        </div>
      </section>
    </div>
  );
}

function IconLanguages(): ReactElement {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <path d="M5 8l6 6" />
      <path d="M4 14l6-6 2-3" />
      <path d="M2 5h12" />
      <path d="M7 2h1" />
      <path d="M22 22l-5-10-5 10" />
      <path d="M14 18h6" />
    </svg>
  );
}

function IconSubtitles(): ReactElement {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <rect x="2" y="5" width="20" height="14" rx="3" />
      <path d="M7 15h4M15 15h2M7 11h10" />
    </svg>
  );
}

function IconVolume(): ReactElement {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <path d="M11 5 6 9H2v6h4l5 4z" />
      <path d="M15.5 8.5a5 5 0 0 1 0 7" />
      <path d="M18.5 5.5a9 9 0 0 1 0 13" />
    </svg>
  );
}

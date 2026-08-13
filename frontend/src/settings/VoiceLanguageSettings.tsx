import { useEffect, useState, type ReactElement } from 'react';
import { playerApi, translatorApi, deeplxApi, type TranslatorConfig, type DeeplxStatus } from '@/api/rest';
import { useAppState } from '@/state/AppStateContext';
import {
  SettingsActionBar,
  SettingsGroup,
  SettingsRow,
  SettingsStatusBadge,
} from './SettingsConsole';

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
export type VoiceLanguageMode = 'chat' | 'translation';

export interface VoiceLanguageSettingsProps {
  mode?: VoiceLanguageMode;
}

export function VoiceLanguageSettings({ mode = 'chat' }: VoiceLanguageSettingsProps): ReactElement {
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

  const saveLanguage = async (nextLanguage: string): Promise<void> => {
    try {
      await playerApi.setLanguage({ language: nextLanguage });
    } catch (err) {
      setStatus(err instanceof Error ? err.message : '保存失败');
    }
  };

  const saveSubtitleTarget = async (nextTarget: string): Promise<void> => {
    try {
      await translatorApi.save({
        translate_subtitle: subtitleEnabled,
        subtitle_target_lang: nextTarget,
      });
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
    <div className={`settings-section settings-console-root voice-language-settings voice-language-settings-${mode}`}>
      {mode === 'chat' ? (
        <>
          <SettingsGroup title="回复语言">
            <SettingsRow label="回复语言" settingKey="setting-reply-language">
              <select
                value={language}
                onChange={(e) => {
                  const nextLanguage = e.target.value;
                  setLanguage(nextLanguage);
                  void saveLanguage(nextLanguage);
                }}
              >
                {LANGUAGES.map((l) => (
                  <option key={l.value} value={l.value}>
                    {l.label}
                  </option>
                ))}
              </select>
            </SettingsRow>
          </SettingsGroup>

          <SettingsGroup title="双语气泡">
            <SettingsRow label="双语气泡" settingKey="setting-subtitle-enabled">
              <input
                className="switch"
                type="checkbox"
                checked={subtitleEnabled}
                onChange={(e) => setSubtitleEnabled(e.target.checked)}
              />
            </SettingsRow>
            {subtitleEnabled && (
              <SettingsRow label="目标语言">
                <input
                  value={subtitleTarget}
                  onChange={(e) => setSubtitleTarget(e.target.value)}
                  onBlur={(e) => void saveSubtitleTarget(e.currentTarget.value)}
                  placeholder="如 英语 / 日本語"
                />
              </SettingsRow>
            )}
          </SettingsGroup>
        </>
      ) : null}

      {mode === 'translation' ? <SettingsGroup title="语音合成前翻译" className="console-group-primary">
        <div data-setting-key="setting-voice-translation">
          <SettingsRow label="启用语音翻译">
            <input
              className="switch"
              type="checkbox"
              checked={voiceEnabled}
              onChange={(e) => setVoiceEnabled(e.target.checked)}
            />
          </SettingsRow>
          {voiceEnabled && (
            <>
              <SettingsRow label="翻译引擎">
                <select
                  value={engine}
                  onChange={(e) => setEngine(e.target.value as 'llm' | 'deeplx')}
                >
                  <option value="llm">LLM API（DeepSeek 等，翻译更自然）</option>
                  <option value="deeplx">本地 DeepLX（毫秒级、免费，需本地部署）</option>
                </select>
              </SettingsRow>
              {engine === 'deeplx' && (
                <SettingsRow label="DeepLX 端点">
                  <input
                    value={deeplxEndpoint}
                    onChange={(e) => setDeeplxEndpoint(e.target.value)}
                    placeholder="http://localhost:1188/v2/translate"
                  />
                </SettingsRow>
              )}
              <SettingsRow label="语音目标语言">
                <input
                  value={voiceTarget}
                  onChange={(e) => setVoiceTarget(e.target.value)}
                  placeholder={engine === 'deeplx' ? '如 JA / EN-US' : '如 日文 / 英文'}
                />
              </SettingsRow>
              {engine === 'deeplx' && (
                <div className="deeplx-service-box">
                  <div className="voicevox-state-row">
                    <SettingsStatusBadge
                      tone={
                        dx?.rate_limited
                          ? 'danger'
                          : dx?.running
                            ? 'ok'
                            : dx?.exe_exists
                              ? 'warn'
                              : 'danger'
                      }
                    >
                      {dx?.rate_limited
                        ? '限流中'
                        : dx?.running
                          ? '运行中'
                          : dx?.exe_exists
                            ? '已就绪 · 未启动'
                            : '未安装'}
                    </SettingsStatusBadge>
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
                </div>
              )}
            </>
          )}
          <SettingsActionBar>
            <button className="btn" onClick={() => void saveTranslator()}>
              保存翻译设置
            </button>
          </SettingsActionBar>
        </div>
      </SettingsGroup> : null}
      {status && <div className="setting-status">{status}</div>}
    </div>
  );
}

/**
 * 回复方式面板（控制台「语音 → 回复方式」卡片）。
 *
 * 2026-08-13 改造：去掉 4 Tab（聊天回复/语音识别/语音合成前翻译/语音合成），
 * 平铺「聊天回复 + 语音合成前翻译」两组标准设置——与其他真实组件卡片（LLM /
 * 角色 / 屏幕感知等）的 SettingsGroup 列表样式一致。ASR/TTS 细分配置由
 * 「语音引擎」卡片（voice-engine）覆盖，不在此重复。
 */
export function VoiceSettingsPanel(): ReactElement {
  return (
    <div className="settings-section settings-console-root voice-settings-panel">
      <VoiceLanguageSettings mode="chat" />
      <VoiceLanguageSettings mode="translation" />
    </div>
  );
}

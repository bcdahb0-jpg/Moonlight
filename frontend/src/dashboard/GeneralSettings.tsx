import { useEffect, useState, type ReactElement } from 'react';
import {
  defaultBgApi,
  playerApi,
  playerPromptApi,
  translatorApi,
  type TranslatorConfig,
} from '@/api/rest';
import { Icon } from '@/ui/icons';

const LANGUAGES = [
  { value: '', label: '自动（跟随角色）' },
  { value: 'zh-CN', label: '简体中文' },
  { value: 'zh-TW', label: '繁体中文' },
  { value: 'en', label: 'English' },
  { value: 'ja', label: '日本語' },
  { value: 'ko', label: '한국어' },
];

/**
 * 通用设置：回复语言 + 字幕翻译（双语气泡）+ 跨语音翻译 + 玩家提示词。
 * 外观主题切换已收敛到设置页右上角的图标（见 Dashboard header）。
 * 每个区块独立成卡片，保存操作就近反馈。
 */
export function GeneralSettings(): ReactElement {
  const [language, setLanguage] = useState('');
  const [prompt, setPrompt] = useState('');
  const [background, setBackground] = useState('');
  const [status, setStatus] = useState('');

  // 翻译设置（原 TranslatorSettings 合并而来）
  const [voiceEnabled, setVoiceEnabled] = useState(false);
  const [voiceTarget, setVoiceTarget] = useState('');
  const [subtitleEnabled, setSubtitleEnabled] = useState(false);
  const [subtitleTarget, setSubtitleTarget] = useState('');
  const [translatorStatus, setTranslatorStatus] = useState('');

  useEffect(() => {
    void playerApi
      .getLanguage()
      .then((r) => setLanguage(r.language))
      .catch(() => undefined);
    void playerPromptApi
      .get()
      .then((r) => setPrompt(r.prompt))
      .catch(() => undefined);
    void defaultBgApi
      .get()
      .then((r) => setBackground(r.background))
      .catch(() => undefined);
    void translatorApi
      .get()
      .then((r: TranslatorConfig) => {
        setVoiceEnabled(r.enabled);
        setVoiceTarget(r.llm_target_lang);
        setSubtitleEnabled(r.translate_subtitle);
        setSubtitleTarget(r.subtitle_target_lang);
      })
      .catch(() => undefined);
  }, []);

  const saveLanguage = async (): Promise<void> => {
    setStatus('保存中…');
    try {
      await playerApi.setLanguage({ language });
      setStatus('回复语言已保存');
    } catch (err) {
      setStatus(err instanceof Error ? err.message : '保存失败');
    }
  };

  const savePrompt = async (): Promise<void> => {
    setStatus('保存中…');
    try {
      await playerPromptApi.save({ prompt });
      setStatus('玩家提示词已保存');
    } catch (err) {
      setStatus(err instanceof Error ? err.message : '保存失败');
    }
  };

  const saveTranslator = async (): Promise<void> => {
    setTranslatorStatus('保存中…');
    try {
      await translatorApi.save({
        enabled: voiceEnabled,
        engine: 'llm',
        llm_target_lang: voiceTarget,
        translate_subtitle: subtitleEnabled,
        subtitle_target_lang: subtitleTarget,
      });
      setTranslatorStatus('翻译设置已保存（重新选择角色后生效）');
    } catch (err) {
      setTranslatorStatus(err instanceof Error ? err.message : '保存失败');
    }
  };

  return (
    <div className="settings-section general-settings">
      {/* ① 回复语言 */}
      <section className="settings-card">
        <div className="settings-card-head">
          <span className="settings-card-icon">
            <Icon name="languages" size={16} />
          </span>
          <div className="settings-card-title">
            <h3>回复语言</h3>
            <p>注入每个角色的系统提示词，让 AI 用你熟悉的语言回复</p>
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
              <Icon name="check" size={13} />
              保存语言
            </button>
          </div>
        </div>
      </section>

      {/* ② 字幕翻译（双语气泡） */}
      <section className="settings-card">
        <div className="settings-card-head">
          <span className="settings-card-icon">
            <Icon name="message" size={16} />
          </span>
          <div className="settings-card-title">
            <h3>字幕翻译 · 双语气泡</h3>
            <p>气泡保留中文原文，下方小字显示翻译</p>
          </div>
        </div>
        <div className="settings-card-body">
          <label className="toggle-row">
            <span>翻译字幕（气泡下方显示翻译小字）</span>
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

      {/* ③ 跨语音翻译 */}
      <section className="settings-card">
        <div className="settings-card-head">
          <span className="settings-card-icon">
            <Icon name="volume" size={16} />
          </span>
          <div className="settings-card-title">
            <h3>跨语音翻译</h3>
            <p>回复时先翻译再合成语音，让声音说外语</p>
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
            <label className="field">
              <span>语音目标语言</span>
              <input
                value={voiceTarget}
                onChange={(e) => setVoiceTarget(e.target.value)}
                placeholder="如 日文 / 英文"
              />
            </label>
          )}
          <p className="setting-hint">
            <Icon name="info" size={12} />
            翻译引擎：LLM 翻译（走当前对话模型，无需额外服务）
          </p>
          <div className="btn-row">
            <button className="btn btn-primary" onClick={() => void saveTranslator()}>
              <Icon name="check" size={13} />
              保存翻译设置
            </button>
          </div>
          {translatorStatus && <div className="setting-status">{translatorStatus}</div>}
        </div>
      </section>

      {/* ④ 玩家提示词 */}
      <section className="settings-card">
        <div className="settings-card-head">
          <span className="settings-card-icon">
            <Icon name="user" size={16} />
          </span>
          <div className="settings-card-title">
            <h3>玩家提示词</h3>
            <p>全局上下文（描述你自己），AI 会记住并自然运用</p>
          </div>
        </div>
        <div className="settings-card-body">
          <label className="field">
            <span>全局上下文</span>
            <textarea
              rows={4}
              value={prompt}
              onChange={(e) => setPrompt(e.target.value)}
              placeholder="例如：我是大学生，喜欢动漫和游戏……"
            />
          </label>
          <div className="btn-row">
            <button className="btn" onClick={() => void savePrompt()}>
              <Icon name="check" size={13} />
              保存提示词
            </button>
          </div>
        </div>
      </section>

      {background ? (
        <div className="system-card">
          <div className="system-card-title">默认背景</div>
          <div className="system-card-value">{background}</div>
        </div>
      ) : null}

      {status && <div className="setting-status">{status}</div>}
    </div>
  );
}

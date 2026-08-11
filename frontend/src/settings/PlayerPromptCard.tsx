import { useEffect, useState, type ReactElement } from 'react';
import { defaultBgApi, playerPromptApi } from '@/api/rest';

/**
 * 角色页 · 玩家提示词（从原「通用」页拆分而来，重设计 v4）：
 * 全局上下文（描述你自己）+ 默认背景只读展示。
 */
export function PlayerPromptCard(): ReactElement {
  const [prompt, setPrompt] = useState('');
  const [background, setBackground] = useState('');
  const [status, setStatus] = useState('');

  useEffect(() => {
    void playerPromptApi
      .get()
      .then((r) => setPrompt(r.prompt))
      .catch(() => undefined);
    void defaultBgApi
      .get()
      .then((r) => setBackground(r.background))
      .catch(() => undefined);
  }, []);

  const savePrompt = async (): Promise<void> => {
    setStatus('保存中…');
    try {
      await playerPromptApi.save({ prompt });
      setStatus('已保存');
    } catch (err) {
      setStatus(err instanceof Error ? err.message : '保存失败');
    }
  };

  return (
    <div className="settings-section">
      <section className="settings-card">
        <div className="settings-card-head">
          <span className="settings-card-icon">
            <IconUser />
          </span>
          <div className="settings-card-title">
            <h3>玩家提示词</h3>
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
              保存
            </button>
          </div>
          {background ? (
            <div className="system-card">
              <div className="system-card-title">默认背景</div>
              <div className="system-card-value">{background}</div>
            </div>
          ) : null}
          {status && <div className="setting-status">{status}</div>}
        </div>
      </section>
    </div>
  );
}

function IconUser(): ReactElement {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="12" cy="8" r="4" />
      <path d="M4 21c0-4 3.6-6.5 8-6.5s8 2.5 8 6.5" />
    </svg>
  );
}

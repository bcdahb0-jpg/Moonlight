import { useEffect, useState, type ReactElement } from 'react';
import { playerPromptApi } from '@/api/rest';
import { SettingsActionBar, SettingsGroup } from './SettingsConsole';

/**
 * 角色页 · 玩家上下文。
 */
export function PlayerPromptCard(): ReactElement {
  const [prompt, setPrompt] = useState('');

  useEffect(() => {
    void playerPromptApi
      .get()
      .then((r) => setPrompt(r.prompt))
      .catch(() => undefined);
  }, []);

  const savePrompt = async (): Promise<void> => {
    try {
      await playerPromptApi.save({ prompt });
    } catch {
      // 页面保持极简，不额外渲染状态提示。
    }
  };

  return (
    <div className="settings-section settings-console-root settings-player-console">
      <SettingsGroup
        title="玩家上下文"
        className="player-context-group"
      >
        <div className="player-context-control" data-setting-key="setting-player-prompt">
          <textarea
            className="player-context-textarea"
            rows={4}
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
          />
        </div>
        <SettingsActionBar>
            <button className="btn" onClick={() => void savePrompt()}>
              保存
            </button>
        </SettingsActionBar>
      </SettingsGroup>
    </div>
  );
}

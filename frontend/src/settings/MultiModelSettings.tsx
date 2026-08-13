import { useCallback, useEffect, useState, type ReactElement } from 'react';
import { live2dCatalogApi, type CatalogModel } from '@/api/rest';
import { SettingsGroup, SettingsRow, SettingsStatusBadge } from '@/settings/SettingsConsole';

/**
 * 多模型与专属 Prompt（P1）。
 * 扫描 live2d-models/** 下的模型 → 热切换（写 conf + 返回 model_url）→
 * 专属 Prompt 读写模型目录 model_prompt.txt。
 */
export function MultiModelSettings(): ReactElement {
  const [models, setModels] = useState<CatalogModel[]>([]);
  const [current, setCurrent] = useState('');
  const [selected, setSelected] = useState('');
  const [prompt, setPrompt] = useState('');
  const [loading, setLoading] = useState(false);
  const [notice, setNotice] = useState('');

  const refresh = useCallback(async (): Promise<void> => {
    try {
      const res = await live2dCatalogApi.models();
      setModels(res.models);
      setCurrent(res.current);
      setSelected((prev) => prev || res.current);
      const active = res.models.find((m) => m.name === res.current);
      setPrompt(active?.custom_prompt ?? '');
    } catch {
      setNotice('无法连接后端');
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const selectModel = async (name: string): Promise<void> => {
    setSelected(name);
    try {
      const res = await live2dCatalogApi.prompt(name);
      setPrompt(res.custom_prompt);
    } catch {
      setPrompt('');
    }
  };

  const applyModel = async (): Promise<void> => {
    if (!selected) return;
    setLoading(true);
    try {
      const res = await live2dCatalogApi.load(selected);
      setCurrent(res.name);
      setNotice(
        `已切换到「${res.name}」（${res.model_url}）。窗口模式渲染层将在下次模型加载时生效。`,
      );
    } catch {
      setNotice('切换失败：模型不存在或后端不可用');
    } finally {
      setLoading(false);
    }
  };

  const savePrompt = async (): Promise<void> => {
    if (!selected) return;
    try {
      await live2dCatalogApi.savePrompt(selected, prompt);
      setNotice(`「${selected}」专属 Prompt 已保存到 model_prompt.txt`);
      void refresh();
    } catch {
      setNotice('Prompt 保存失败');
    }
  };

  return (
    <SettingsGroup title="多模型与专属 Prompt" description="扫描 live2d-models 目录，热切换模型并绑定专属提示词">
      <SettingsRow label="模型清单" description={`共 ${models.length} 个可用模型`}>
        <div className="console-tag-wrap">
          {models.map((m) => (
            <button
              type="button"
              key={m.name}
              className={`console-tag${selected === m.name ? ' active' : ''}${m.name === current ? ' current' : ''}`}
              onClick={() => void selectModel(m.name)}
              title={m.model_url}
            >
              {m.name}
              {m.name === current ? ' ✓' : ''}
            </button>
          ))}
        </div>
      </SettingsRow>

      <SettingsRow label="模型热加载" description="切换后写 conf.yaml，前端渲染层热替换">
        <button type="button" className="console-btn" disabled={!selected || loading} onClick={() => void applyModel()}>
          {loading ? '切换中…' : '应用所选模型'}
        </button>
      </SettingsRow>

      <SettingsRow label="模型专属 Prompt" description="每个模型独立的动作/表情指令（model_prompt.txt）">
        <div className="console-stack">
          <textarea
            className="console-textarea"
            rows={4}
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
            placeholder="例如：hiyori → 温柔微笑为主，说话时轻微歪头…"
          />
          <button type="button" className="console-btn" onClick={() => void savePrompt()}>
            保存专属 Prompt
          </button>
        </div>
      </SettingsRow>

      <SettingsRow label="状态">
        <SettingsStatusBadge tone="ok">{notice || `当前模型：${current || '—'}`}</SettingsStatusBadge>
      </SettingsRow>
    </SettingsGroup>
  );
}

export default MultiModelSettings;

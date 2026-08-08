import { useEffect, useState, type ReactElement } from 'react';
import { live2dApi, type Live2dModelEntry } from '@/api/rest';

export function Live2DSettings(): ReactElement {
  const [models, setModels] = useState<Live2dModelEntry[]>([]);
  const [status, setStatus] = useState('');

  useEffect(() => {
    void live2dApi
      .info()
      .then((r) => setModels(r.characters))
      .catch((err) => setStatus(err instanceof Error ? err.message : '加载失败'));
  }, []);

  return (
    <div className="settings-section">
      <h3>Live2D 模型（{models.length}）</h3>
      <div className="model-grid">
        {models.map((m) => (
          <div className="model-card" key={m.name}>
            <div className="model-card-avatar">
              {m.avatar ? (
                <img src={m.avatar} alt={m.name} />
              ) : (
                <span>🎭</span>
              )}
            </div>
            <div className="model-card-name">{m.name}</div>
            <div className="model-card-path">{m.model_path}</div>
          </div>
        ))}
        {models.length === 0 && <div className="setting-status">没有可用的 Live2D 模型。</div>}
      </div>
      {status && <div className="setting-status">{status}</div>}
    </div>
  );
}

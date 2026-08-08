import { useCallback, useEffect, useState, type ReactElement } from 'react';
import { emotionApi, type EmotionEvent } from '@/api/rest';
import { Icon } from '@/ui/icons';

const EMOTIONS = ['joy', 'amusement', 'affection', 'surprise', 'confusion', 'sad', 'anger', 'fear', 'gratitude', 'neutral'];

const EMOTION_LABELS: Record<string, string> = {
  joy: '喜悦',
  amusement: '好笑',
  affection: '亲昵',
  surprise: '惊讶',
  confusion: '困惑',
  sad: '难过',
  anger: '生气',
  fear: '害怕',
  gratitude: '感激',
  neutral: '平静',
};

export function EmotionDebug(): ReactElement {
  const [emotion, setEmotion] = useState<string>('neutral');
  const [histogram, setHistogram] = useState<Record<string, number>>({});
  const [history, setHistory] = useState<EmotionEvent[]>([]);
  const [status, setStatus] = useState('');

  const load = useCallback(async (): Promise<void> => {
    try {
      const [cur, hist] = await Promise.all([emotionApi.get(), emotionApi.getHistory(10)]);
      setEmotion(cur.emotion);
      setHistogram(cur.histogram ?? {});
      setHistory(hist.events);
    } catch (err) {
      setStatus(err instanceof Error ? err.message : '加载失败');
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const override = async (emotionName: string): Promise<void> => {
    try {
      await emotionApi.override(emotionName);
      setStatus(`已强制设为 ${EMOTION_LABELS[emotionName] ?? emotionName}`);
      void load();
    } catch (err) {
      setStatus(err instanceof Error ? err.message : '设置失败');
    }
  };

  const reset = async (): Promise<void> => {
    try {
      await emotionApi.reset();
      setStatus('已重置为 neutral（平静）');
      void load();
    } catch (err) {
      setStatus(err instanceof Error ? err.message : '重置失败');
    }
  };

  const max = Math.max(1, ...Object.values(histogram));

  return (
    <div className="settings-section general-settings">
      <section className="settings-card">
        <div className="settings-card-head">
          <span className="settings-card-icon">
            <Icon name="face" size={16} />
          </span>
          <div className="settings-card-title">
            <h3>当前情绪</h3>
            <p>Live2D 表情与情绪直方图</p>
          </div>
        </div>
        <div className="settings-card-body">
          <div className="system-card">
            <div className="system-card-title">当前情绪</div>
            <div className="system-card-value">
              <span className="emotion-badge large">
                {EMOTION_LABELS[emotion] ?? emotion}
              </span>
            </div>
            {Object.entries(histogram).map(([name, count]) => (
              <div key={name} className="range-row" style={{ marginTop: 6 }}>
                <span style={{ minWidth: 64, fontSize: 12 }}>
                  {EMOTION_LABELS[name] ?? name}
                </span>
                <div className="emotion-bar">
                  <div className="emotion-bar-fill" style={{ width: `${(count / max) * 100}%` }} />
                </div>
                <span className="range-val">{count}</span>
              </div>
            ))}
          </div>
        </div>
      </section>

      <section className="settings-card">
        <div className="settings-card-head">
          <span className="settings-card-icon">
            <Icon name="edit" size={16} />
          </span>
          <div className="settings-card-title">
            <h3>手动调试</h3>
            <p>强制切换表情观察 Live2D 表现</p>
          </div>
        </div>
        <div className="settings-card-body">
          <div className="emotion-chips">
            {EMOTIONS.map((e) => (
              <button key={e} className="chip-btn" onClick={() => void override(e)}>
                {EMOTION_LABELS[e] ?? e}
              </button>
            ))}
          </div>
          <div className="btn-row">
            <button className="btn btn-danger" onClick={() => void reset()}>
              重置为平静
            </button>
          </div>
        </div>
      </section>

      <section className="settings-card">
        <div className="settings-card-head">
          <span className="settings-card-icon">
            <Icon name="history" size={16} />
          </span>
          <div className="settings-card-title">
            <h3>情绪历史（最近 10 条）</h3>
            <p>情绪识别事件的时序记录</p>
          </div>
        </div>
        <div className="settings-card-body">
          {history.length > 0 ? (
            <div className="list">
              {history.map((ev, i) => (
                <div className="list-item" key={i}>
                  <div className="list-item-title">
                    {EMOTION_LABELS[ev.emotion] ?? ev.emotion} · {(ev.confidence * 100).toFixed(0)}%
                  </div>
                  <div className="list-item-sub">
                    {ev.source} · {new Date(ev.timestamp).toLocaleString()}
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <div className="empty-state">暂无情绪记录</div>
          )}
        </div>
      </section>

      {status && <div className="setting-status">{status}</div>}
    </div>
  );
}

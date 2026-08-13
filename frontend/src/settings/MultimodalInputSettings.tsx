import { useEffect, useState, type ReactElement } from 'react';
import { get } from '@/api/rest';
import { SettingsGroup, SettingsRow, SettingsStatusBadge } from '@/settings/SettingsConsole';

/**
 * 多模态输入（P5，PetGPT media.js 思路）：能力探测 + 降级链路说明。
 *
 * 能力探测：GET /api/llm/capabilities（按模型名静态映射 text/image/audio/video/pdf）。
 * 降级链路（chat 输入附件，P5.1 接线）：
 * - 图片 → 无视觉能力的模型（DeepSeek）→ 复用 screen_awareness 视觉模型预描述后注入文本；
 * - PDF → 文本抽取进上下文；
 * - 音频 → ASR（funasr/whisper）转文本。
 */

interface Capabilities {
  ok: boolean;
  model: string;
  capabilities: { text: boolean; image: boolean; audio: boolean; video: boolean; pdf: boolean };
}

const CAP_LABELS: { key: keyof Capabilities['capabilities']; zh: string }[] = [
  { key: 'text', zh: '文本' },
  { key: 'image', zh: '图片' },
  { key: 'audio', zh: '音频' },
  { key: 'video', zh: '视频' },
  { key: 'pdf', zh: 'PDF' },
];

export function MultimodalInputSettings(): ReactElement {
  const [caps, setCaps] = useState<Capabilities | null>(null);
  const [notice, setNotice] = useState('');

  useEffect(() => {
    void get<Capabilities>('/api/llm/capabilities')
      .then(setCaps)
      .catch(() => setNotice('能力探测失败（后端未重启？）'));
  }, []);

  const visionAvailable = caps?.capabilities.image ?? false;

  return (
    <SettingsGroup title="多模态输入" description="让桌宠能看、能听、能读文件（按模型能力自动降级）">
      <SettingsRow label="当前模型能力">
        <div className="console-tag-wrap">
          {CAP_LABELS.map((c) => (
            <span
              key={c.key}
              className="console-tag"
              style={{
                color: caps?.capabilities[c.key] ? 'var(--success,#6ee7a8)' : 'var(--text-faint,rgba(236,231,251,0.45))',
              }}
            >
              {c.zh} {caps?.capabilities[c.key] ? '✓' : '✗'}
            </span>
          ))}
        </div>
        {caps?.model && (
          <div style={{ fontSize: 11.5, marginTop: 6, color: 'var(--text-faint,rgba(236,231,251,0.45))' }}>
            模型：{caps.model}
          </div>
        )}
      </SettingsRow>

      <SettingsRow label="优雅降级" description="不支持的媒体转为文字描述（默认开启，P5.1 附件按钮接入后生效）">
        <SettingsStatusBadge tone={visionAvailable ? 'neutral' : 'ok'}>
          {visionAvailable
            ? '当前模型可直接看图'
            : '图片将走视觉模型预描述后注入文本（复用屏幕感知链路）'}
        </SettingsStatusBadge>
      </SettingsRow>

      <SettingsRow label="降级链路">
        <div className="console-stack">
          <span className="console-tag" style={{ color: 'var(--success,#6ee7a8)' }}>图片 → screen 视觉模型预描述 → 文本注入</span>
          <span className="console-tag" style={{ color: 'var(--success,#6ee7a8)' }}>PDF → 文本抽取 → 上下文</span>
          <span className="console-tag" style={{ color: 'var(--success,#6ee7a8)' }}>音频 → ASR（funasr/whisper）→ 文本</span>
        </div>
      </SettingsRow>

      <SettingsRow label="状态">
        <SettingsStatusBadge tone={notice ? 'warn' : 'ok'}>
          {notice || '能力探测就绪 · 聊天附件按钮 P5.1 接入'}
        </SettingsStatusBadge>
      </SettingsRow>
    </SettingsGroup>
  );
}

export default MultimodalInputSettings;

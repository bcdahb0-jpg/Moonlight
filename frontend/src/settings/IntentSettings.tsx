import { useEffect, useState, type ReactElement } from 'react';
import { intentApi, type IntentClassifyResult, type IntentConfig } from '@/api/rest';
import { SettingsGroup, SettingsRow, SettingsStatusBadge } from '@/settings/SettingsConsole';

/**
 * 意图与情绪识别（P5，AI-Desktop-Pet analyze_intent 思路）。
 *
 * 独立轻量副模型：对每条用户消息并行判 {intent: silence|chat|task, emotion}，
 * 供前端状态条展示 + 后续 policy 决策（勿扰/搭话/静默）。LLM 不可用 →
 * 规则兜底（勿扰词 → silence / 工程词 → task / 否则 chat）。
 * 「试一下」直接调 POST /api/intent/classify 看识别结果。
 */

const INTENT_META: Record<string, { zh: string; tone: 'ok' | 'warn' | 'neutral' }> = {
  silence: { zh: '勿扰', tone: 'warn' },
  chat: { zh: '闲聊', tone: 'ok' },
  task: { zh: '任务指令', tone: 'neutral' },
};

export function IntentSettings(): ReactElement {
  const [cfg, setCfg] = useState<IntentConfig | null>(null);
  const [testText, setTestText] = useState('别烦我，忙着呢');
  const [result, setResult] = useState<IntentClassifyResult | null>(null);
  const [notice, setNotice] = useState('');

  useEffect(() => {
    void intentApi
      .config()
      .then(setCfg)
      .catch(() => setNotice('意图配置拉取失败'));
  }, []);

  const toggle = (enabled: boolean): void => {
    if (!cfg) return;
    void intentApi.saveConfig({ enabled }).then((c) => {
      setCfg(c);
      setNotice(enabled ? '意图识别已开启' : '意图识别已关闭（一律走聊天链路）');
    });
  };

  const classify = (): void => {
    void intentApi
      .classify(testText)
      .then((res) => setResult(res))
      .catch(() => setNotice('识别失败（后端未重启？）'));
  };

  const meta = result ? INTENT_META[result.intent] ?? INTENT_META.chat : null;

  return (
    <SettingsGroup title="意图与情绪识别" description="独立副模型：勿扰 / 闲聊 / 任务 + 情绪，并行于主对话">
      <SettingsRow label="意图识别" description="开启后每条消息自动判意图（可被规则先行命中，零成本）">
        <label className="toggle-row">
          <input type="checkbox" checked={cfg?.enabled ?? false} onChange={(e) => toggle(e.target.checked)} />
          <span>{cfg?.enabled ? '开' : '关'}</span>
        </label>
      </SettingsRow>

      <SettingsRow label="模型选择" description="轻量模型：deepseek-chat / qwen2.5:3b / gemini-flash">
        <SettingsStatusBadge tone="neutral">{cfg?.model || '默认（conf system_config.intent.model）'}</SettingsStatusBadge>
      </SettingsRow>

      <SettingsRow label="识别试一下">
        <div className="console-stack">
          <input className="console-input" style={{ maxWidth: 320 }} value={testText} onChange={(e) => setTestText(e.target.value)} />
          <button type="button" className="console-btn" onClick={classify}>
            识别意图
          </button>
          {result && meta && (
            <div className="console-flow" style={{ marginTop: 4 }}>
              <span className="console-flow-node active">{`意图: ${INTENT_META[result.intent].zh}`}</span>
              <span className="console-flow-node active">{`情绪: ${result.emotion}`}</span>
              <span className="console-tag" style={{ color: 'var(--text-faint,rgba(236,231,251,0.45))' }}>
                source: {result.source}
              </span>
            </div>
          )}
        </div>
      </SettingsRow>

      <SettingsRow label="状态">
        <SettingsStatusBadge tone={notice ? 'warn' : 'ok'}>
          {notice || '意图链路就绪（勿扰词 / 工程词可零 LLM 命中）'}
        </SettingsStatusBadge>
      </SettingsRow>
    </SettingsGroup>
  );
}

export default IntentSettings;

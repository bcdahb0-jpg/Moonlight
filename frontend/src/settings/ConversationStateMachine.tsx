import { useEffect, useState, type ReactElement } from 'react';
import type { WSClient } from '@/api/wsClient';
import {
  getConversationState,
  subscribeConversationState,
  type ConversationState,
} from '@/state/conversationStateBus';
import { SettingsGroup, SettingsRow, SettingsStatusBadge } from '@/settings/SettingsConsole';

/**
 * 对话状态机（P1/P1.5）：Idle / Thinking / Speaking 三态可视化 + 打断 + 静默。
 *
 * 状态来源：`conversationStateBus`（useAppShell 把 isThinking / audioPlaying
 * 归约为三态发布，与聊天主界面实时同步）。
 * 「打断」走 ws.sendInterrupt()（与聊天界面打断同一链路）。
 */

const STATE_META: Record<ConversationState, { zh: string; tone: 'neutral' | 'warn' | 'ok' }> = {
  idle: { zh: 'Idle 空闲', tone: 'neutral' },
  thinking: { zh: 'Thinking 思考中', tone: 'warn' },
  speaking: { zh: 'Speaking 说话中', tone: 'ok' },
};

function StateFlow({ state }: { state: ConversationState }): ReactElement {
  const steps: ConversationState[] = ['idle', 'thinking', 'speaking'];
  const activeIdx = steps.indexOf(state);
  return (
    <div className="console-flow" role="img" aria-label={`对话状态：${STATE_META[state].zh}`}>
      {steps.map((s, i) => (
        <div key={s} className={`console-flow-node${i <= activeIdx ? ' active' : ''} ${STATE_META[s].tone}`}>
          {STATE_META[s].zh}
          {i < steps.length - 1 && <span className="console-flow-arrow">→</span>}
        </div>
      ))}
    </div>
  );
}

export function ConversationStateMachine({ ws }: { ws?: () => WSClient | null }): ReactElement {
  const [state, setState] = useState<ConversationState>(getConversationState());
  const [muted, setMuted] = useState(false);
  const [notice, setNotice] = useState('');

  useEffect(() => {
    return subscribeConversationState(setState);
  }, []);

  const interrupt = (): void => {
    const client = ws?.();
    if (client) {
      client.sendInterrupt();
      setNotice('已发送打断指令（聊天界面同步生效）');
    } else {
      setNotice('控制台内无法打断（需 WS 连接），请在聊天界面操作');
    }
  };

  return (
    <SettingsGroup title="对话状态机" description="Idle / Thinking / Speaking 三态，支持打断与静默">
      <SettingsRow label="当前状态">
        <SettingsStatusBadge tone={STATE_META[state].tone}>{STATE_META[state].zh}</SettingsStatusBadge>
      </SettingsRow>

      <SettingsRow label="状态可视化">
        <div style={{ width: '100%', maxWidth: 320 }}>
          <StateFlow state={state} />
        </div>
      </SettingsRow>

      <SettingsRow label="打断机制" description="说话中可打断重新响应（ws.sendInterrupt）">
        <button type="button" className="console-btn" onClick={interrupt}>
          打断当前回复
        </button>
      </SettingsRow>

      <SettingsRow label="静默模式" description="一键静默，AI 不再主动说话（本地开关）">
        <label className="toggle-row">
          <input type="checkbox" checked={muted} onChange={(e) => setMuted(e.target.checked)} />
          <span>{muted ? '开' : '关'}</span>
        </label>
      </SettingsRow>

      <SettingsRow label="语音快捷键" description="长按 F2 语音输入（聊天界面 PTT 链路）">
        <SettingsStatusBadge tone="neutral">F2（已在聊天界面启用）</SettingsStatusBadge>
      </SettingsRow>

      <SettingsRow label="状态">
        <SettingsStatusBadge tone="ok">
          {notice || '已接入实时状态（isThinking / audioPlaying 总线）'}
        </SettingsStatusBadge>
      </SettingsRow>
    </SettingsGroup>
  );
}

export default ConversationStateMachine;

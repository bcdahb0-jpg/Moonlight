import { useEffect, useState, type ReactElement } from 'react';
import { emotionApi, type EmotionStateMachineResult } from '@/api/rest';
import { SettingsGroup, SettingsRow, SettingsStatusBadge } from '@/settings/SettingsConsole';

/** 情感状态机可视化（P1）：状态集 + 当前会话情绪 + 衰减 + 迁移图。 */

/** 迁移图主状态（后端 EMOTIONS 全量 29 个，图上只展示核心 6 个）。 */
const CORE_STATES = ['neutral', 'happy', 'sad', 'angry', 'surprised', 'shy'];

const STATE_ZH: Record<string, string> = {
  neutral: '平静',
  happy: '开心',
  sad: '难过',
  angry: '生气',
  surprised: '惊讶',
  shy: '害羞',
};

/** 简易状态迁移 SVG：核心状态环 + 迁移箭头（静态示意）。 */
function StateDiagram({ current }: { current: string }): ReactElement {
  const size = 240;
  const cx = size / 2;
  const cy = size / 2;
  const radius = 78;
  const positions = CORE_STATES.map((_, i) => {
    const angle = (Math.PI * 2 * i) / CORE_STATES.length - Math.PI / 2;
    return { x: cx + radius * Math.cos(angle), y: cy + radius * Math.sin(angle) };
  });
  return (
    <svg viewBox={`0 0 ${size} ${size}`} width="100%" height="auto" role="img" aria-label="情感状态迁移图">
      {CORE_STATES.map((state, i) => {
        const next = positions[(i + 1) % CORE_STATES.length];
        const cur = positions[i];
        return (
          <line
            key={`edge-${state}`}
            x1={cur.x}
            y1={cur.y}
            x2={next.x}
            y2={next.y}
            stroke="rgba(110,231,168,0.35)"
            strokeWidth="1.5"
            strokeDasharray="4 4"
            markerEnd="url(#cc-arrow)"
          />
        );
      })}
      {CORE_STATES.map((state, i) => {
        const pos = positions[i];
        const active = state === current;
        return (
          <g key={state}>
            <circle cx={pos.x} cy={pos.y} r={22} fill={active ? 'rgba(110,231,168,0.25)' : 'rgba(23,26,43,0.9)'} stroke={active ? '#6ee7a8' : 'rgba(236,231,251,0.4)'} strokeWidth={active ? 2.5 : 1.5} />
            <text x={pos.x} y={pos.y + 4} textAnchor="middle" fontSize="11" fill={active ? '#6ee7a8' : '#ece7fb'} fontWeight={active ? 700 : 400}>
              {STATE_ZH[state] ?? state}
            </text>
          </g>
        );
      })}
      <defs>
        <marker id="cc-arrow" markerWidth="7" markerHeight="7" refX="6" refY="3" orient="auto">
          <path d="M0,0 L6,3 L0,6 Z" fill="rgba(110,231,168,0.5)" />
        </marker>
      </defs>
    </svg>
  );
}

export function EmotionStateMachine(): ReactElement {
  const [sm, setSm] = useState<EmotionStateMachineResult | null>(null);

  useEffect(() => {
    let cancelled = false;
    const refresh = (): void => {
      void emotionApi
        .stateMachine()
        .then((res) => {
          if (!cancelled) setSm(res);
        })
        .catch(() => {
          /* noop */
        });
    };
    refresh();
    const timer = window.setInterval(refresh, 10_000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, []);

  return (
    <SettingsGroup title="情感状态机" description="每个会话维护独立情绪，消息驱动状态迁移，超时衰减回归平静">
      <SettingsRow label="当前情绪">
        <SettingsStatusBadge tone="ok">
          {sm ? `${STATE_ZH[sm.current] ?? sm.current}（置信 ${sm.confidence?.toFixed(2) ?? '—'}）` : '—'}
        </SettingsStatusBadge>
      </SettingsRow>

      <SettingsRow label="状态集合" description="后端 EMOTIONS 全量（图上仅展示核心状态）">
        <div className="console-tag-wrap">
          {CORE_STATES.map((s) => (
            <span className={`console-tag${s === sm?.current ? ' active' : ''}`} key={s}>
              {STATE_ZH[s] ?? s}
            </span>
          ))}
        </div>
      </SettingsRow>

      <SettingsRow label="情绪衰减" description="会话 N 分钟无消息后回归平静（后端配置）">
        <div className="range-row">
          <input type="range" min={5} max={120} step={5} value={sm?.decay_minutes ?? 30} disabled />
          <span className="range-val">{sm?.decay_minutes ?? 30} min</span>
        </div>
      </SettingsRow>

      <SettingsRow label="状态迁移图">
        <div style={{ width: '100%', maxWidth: 260 }}>
          <StateDiagram current={sm?.current ?? 'neutral'} />
        </div>
      </SettingsRow>

      <SettingsRow label="状态">
        <SettingsStatusBadge tone="ok">
          {sm ? `共 ${sm.states.length} 个情绪状态` : '连接中…'}
        </SettingsStatusBadge>
      </SettingsRow>
    </SettingsGroup>
  );
}

export default EmotionStateMachine;

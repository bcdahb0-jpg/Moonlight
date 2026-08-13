/**
 * PetOverlay — 桌宠悬浮控制条。
 * 信息区（好感 / 心情 / 连接状态）常驻显示；操作按钮（切换模式、设置、
 * 对讲/打断）以图标形式存在，鼠标移入悬浮条时才展开显示。
 *
 * Phase 0（PTT 对讲）：原「打断」按钮替换为 PttButton —— 空闲按住说话、
 * AI 生成/播放中短按打断、长按抢先发言，单一状态机判定，无冲突路径。
 */
import { useState, type ReactElement } from 'react';
import type { AffectionSummary, ErrorCode } from '@/types/ws';
import type { ConnStatus, Emotion } from '@/state/types';
import { Icon } from '@/ui/icons';
import { ScreenEyeIndicator } from '@/screen/ScreenEyeIndicator';
import { PttButton } from '@/chat/PttButton';
import { PetSubtitleBar } from '@/chat/PetSubtitleBar';

export interface PetOverlayProps {
  affection: AffectionSummary | null;
  emotion: Emotion;
  /** 情绪强度 0..1（诊断显示）。 */
  emotionIntensity?: number | null;
  /** 情绪来源 'rule' | 'llm'（诊断显示）。 */
  emotionSource?: string | null;
  /** 引擎类型（诊断徽标：soullink / legacy）。 */
  engineType?: 'soullink' | 'legacy' | null;
  connStatus: ConnStatus;
  errorCode: ErrorCode | null;
  onToggleMode: () => void;
  onOpenSettings: () => void;
  onInterrupt: () => void;
  /** Phase 5：屏幕感知状态灯（快捷暂停/恢复）。 */
  screenEnabled?: boolean;
  screenAuthorized?: boolean;
  screenCapturing?: boolean;
  screenLastAnalyzeAt?: number | null;
  screenError?: string | null;
  onToggleScreen?: () => void;
  /** Phase 0：AI 正在生成或播放语音（isThinking || audioPlayer.isPlaying）。 */
  aiSpeaking?: boolean;
  /** Phase 0（修复 2026-08-11）：PTT 边录边发 —— 每个 PCM chunk 实时回调。 */
  onPttChunk?: (chunk: Float32Array) => void;
  /** Phase 0：PTT 录音结束（App 层确保会话后发 mic-audio-end）。 */
  onPttEnd?: () => void;
  /** Phase 0：麦克风权限/启动错误回调。 */
  onMicError?: (error: Error) => void;
  /** Phase 3：桌宠字幕条文本（最近一条流式 AI full-text）。 */
  petSubtitle?: string;
  /** 功能快捷入口：打开控制台对应分区（唱歌/直播/陪玩/QQ 等）。 */
  onOpenSection?: (section: string) => void;
  /** 勿扰（静音）开关：停止当前播放并抑制后续 TTS。 */
  muted?: boolean;
  onToggleMute?: () => void;
}

/** 桌宠模式功能图标集（P0–P6 常用，点击打开控制台分区）。 */
const PET_FUNCS: Array<{ id: string; icon: 'music' | 'broadcast' | 'gamepad' | 'puzzle'; label: string; section: string }> = [
  { id: 'sing', icon: 'music', label: '唱歌', section: 'entertainment' },
  { id: 'live', icon: 'broadcast', label: '直播', section: 'entertainment' },
  { id: 'playmate', icon: 'gamepad', label: '陪玩', section: 'entertainment' },
  { id: 'qq', icon: 'puzzle', label: 'QQ', section: 'task' },
];

export function PetOverlay({
  connStatus,
  onToggleMode,
  onOpenSettings,
  onInterrupt,
  screenEnabled = false,
  screenAuthorized = false,
  screenCapturing = false,
  screenLastAnalyzeAt = null,
  screenError = null,
  onToggleScreen,
  aiSpeaking = false,
  onPttChunk,
  onPttEnd,
  onMicError,
  petSubtitle = '',
  onOpenSection,
  muted = false,
  onToggleMute,
}: PetOverlayProps): ReactElement {
  const connected = connStatus === 'connected';
  const [expanded, setExpanded] = useState(false);
  const connectionLabel = connected ? '已连接' : connStatus === 'connecting' ? '连接中' : '未连接';
  const screenTitle = !screenAuthorized
    ? '屏幕感知：待授权'
    : screenError
      ? `屏幕感知：识别失败 · ${screenError}`
      : screenCapturing
        ? '屏幕感知：识别中'
        : screenEnabled
          ? `屏幕感知：已启用${screenLastAnalyzeAt ? ` · 最近 ${new Date(screenLastAnalyzeAt * 1000).toLocaleTimeString()}` : ''}`
          : '屏幕感知：已暂停';
  return (
    <div className="pet-overlay">
      {/* Phase 3：桌宠字幕条（最近一条流式 AI full-text，可配置延迟后淡出） */}
      <PetSubtitleBar text={petSubtitle} />
      {/* 信息区：常驻显示 */}
      <div className={`pet-dock ${expanded ? 'expanded' : ''}`} data-testid="companion-dock">
      <div className="pet-dock-summary" role="status" aria-live="polite">
      <span className="pet-dock-connection" title={connectionLabel}><span className="conn-dot" data-status={connStatus} aria-label={connectionLabel} /></span>
      <button
        type="button"
        className="pet-dock-toggle"
        onClick={() => setExpanded((value) => !value)}
        aria-expanded={expanded}
        aria-label={expanded ? '收起桌宠控制面板' : '展开桌宠控制面板'}
        title={expanded ? '收起控制面板' : '展开控制面板'}
      >
        <Icon name="chevronDown" size={14} />
      </button>
      </div>
      {expanded && <div className="pet-dock-panel">

      {/* 操作区：hover 显示图标，默认隐藏 */}
      {onToggleScreen && (
        <div className="pet-dock-action pet-dock-screen-action" title={screenTitle}>
          <ScreenEyeIndicator enabled={screenEnabled} capturing={screenCapturing} onToggle={onToggleScreen} />
          <span>{screenEnabled ? '暂停看屏幕' : '看屏幕'}</span>
        </div>
      )}

      {/* P6 UI 重构：功能快捷图标（唱歌/直播/陪玩/QQ → 控制台分区） */}
      {onOpenSection && (
        <div className="pet-dock-funcs">
          {PET_FUNCS.map((f) => (
            <button
              type="button"
              key={f.id}
              className="overlay-btn pet-dock-action pet-dock-func"
              onClick={() => onOpenSection(f.section)}
              title={f.label}
              aria-label={f.label}
            >
              <Icon name={f.icon} size={15} />
              <span>{f.label}</span>
            </button>
          ))}
          {/* 勿扰（静音）：停止当前播放 + 抑制后续 TTS */}
          {onToggleMute && (
            <button
              type="button"
              className={`overlay-btn pet-dock-action pet-dock-func${muted ? ' muted' : ''}`}
              onClick={onToggleMute}
              title={muted ? '解除勿扰（恢复语音）' : '勿扰（静音）'}
              aria-label={muted ? '解除勿扰' : '勿扰'}
              aria-pressed={muted}
            >
              <Icon name={muted ? 'micOff' : 'zap'} size={15} />
              <span>{muted ? '解除' : '勿扰'}</span>
            </button>
          )}
        </div>
      )}

      <button type="button" className="overlay-btn pet-dock-action" onClick={onToggleMode} title="切换窗口模式" aria-label="切换窗口模式">
        <Icon name="switch" size={15} />
        <span>切换窗口模式</span>
      </button>
      <button type="button" className="overlay-btn pet-dock-action" onClick={onOpenSettings} title="设置" aria-label="设置">
        <Icon name="settings" size={15} />
        <span>设置</span>
      </button>
      {/* Phase 0：对讲按钮（替代原打断按钮；短按打断、长按抢先发言、空闲按住说话） */}
      {onPttChunk && onPttEnd && (
        <div className="pet-dock-action pet-dock-ptt"><PttButton
          aiSpeaking={aiSpeaking}
          connected={connected}
          onInterrupt={onInterrupt}
          onChunk={onPttChunk}
          onEnd={onPttEnd}
          onMicError={onMicError}
        /><span>按住说话</span></div>
      )}
      </div>}
      </div>
    </div>
  );
}

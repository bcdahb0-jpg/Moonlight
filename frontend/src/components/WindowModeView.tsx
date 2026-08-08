/**
 * WindowModeView — 窗口模式布局（Phase 2 拆分自 App.tsx）：
 * 标题栏（操作入口 + 窗口控制） + 会话侧边栏（可折叠） + 聊天面板 + 角色框
 * （Live2D 画布 + 角色状态条：好感 / 心情 / 连接状态），交互经回调上抛给 shell 装配。
 */
import type { ReactElement } from 'react';
import { Live2DCanvas } from '@/live2d/Live2DCanvas';
import type { Live2DAdapter } from '@/live2d/Live2DAdapter';
import { ChatPanel } from '@/chat/ChatPanel';
import { ConversationSidebar } from '@/chat/ConversationSidebar';
import { TitleBar } from '@/components/TitleBar';
import { AffectionBadge } from '@/emotion/AffectionBadge';
import { EmotionBadge } from '@/emotion/EmotionBadge';
import type { WSClient } from '@/api/wsClient';
import type { AudioPlayer } from '@/api/audioPlayer';
import type { AffectionSummary } from '@/types/ws';
import type { ConnStatus, Emotion, ChatMessage, HistoryEntry } from '@/state/types';
import type { ErrorCode } from '@/types/ws';

export interface WindowModeViewProps {
  modelUrl: string;
  emotionMap?: Record<string, number>;
  tapMotions?: Record<string, Record<string, number>>;
  adapterRef: React.MutableRefObject<Live2DAdapter | null>;
  messages: ChatMessage[];
  isThinking: boolean;
  subtitle: string;
  connected: boolean;
  historyList: HistoryEntry[];
  currentHistoryUid: string | null;
  lastError: string | null;
  errorCode: ErrorCode | null;
  affection: AffectionSummary | null;
  emotion: Emotion;
  connStatus: ConnStatus;
  onDismissError: () => void;
  onOpenSettingsSection?: (section: string) => void;
  onToggleMode: () => void;
  /** 录音结束回调（默认只发 audio-end；App 层会额外回显「正在识别…」占位）。 */
  onMicAudioEnd?: () => void;
  /** 文字发送回调（App 层负责本地立即回显 + 发送）；缺省时直接发 WS。 */
  onSendText?: (text: string) => void;
  /** 打断 AI 回复（App 层负责本地复位转圈 + 发 WS 中断信号）。 */
  onInterrupt?: () => void;
  ws: () => WSClient | null;
  audioPlayer: () => AudioPlayer | null;
  onError: (error: Error) => void;
}

export function WindowModeView({
  modelUrl,
  emotionMap,
  tapMotions,
  adapterRef,
  messages,
  isThinking,
  subtitle,
  connected,
  historyList,
  currentHistoryUid,
  lastError,
  errorCode,
  affection,
  emotion,
  connStatus,
  onDismissError,
  onOpenSettingsSection,
  onToggleMode,
  onMicAudioEnd,
  onSendText,
  onInterrupt,
  ws,
  audioPlayer,
  onError,
}: WindowModeViewProps): ReactElement {
  return (
    <div className="window-layout">
      <TitleBar
        onToggleMode={onToggleMode}
        onOpenSettings={() => onOpenSettingsSection?.('general')}
      />
      <div className="window-body">
        <ConversationSidebar
          historyList={historyList}
          currentHistoryUid={currentHistoryUid}
          onFetchHistory={() => ws()?.sendFetchHistoryList()}
          onLoadHistory={(uid) => ws()?.sendFetchAndSetHistory(uid)}
          onNewHistory={() => ws()?.sendCreateNewHistory()}
          onDeleteHistory={(uid) => ws()?.sendDeleteHistory(uid)}
          onRename={(uid, title) => ws()?.sendSetHistoryTitle(uid, title)}
        />
        <ChatPanel
          messages={messages}
          isThinking={isThinking}
          subtitle={subtitle}
          connected={connected}
          lastError={lastError}
          errorCode={errorCode}
          onDismissError={onDismissError}
          onOpenSettingsSection={onOpenSettingsSection}
          onSend={(text) => {
            if (onSendText) {
              onSendText(text); // App 层：本地立即回显 + 发送
            } else {
              ws()?.sendTextInput(text);
            }
          }}
          onAudioChunk={(chunk) => ws()?.sendMicAudioChunk(chunk)}
          onAudioEnd={() => {
            if (onMicAudioEnd) {
              onMicAudioEnd();
            } else {
              ws()?.sendMicAudioEnd();
            }
          }}
          onInterrupt={() => {
            audioPlayer()?.stop();
            if (onInterrupt) {
              onInterrupt();
            } else {
              ws()?.sendInterrupt();
            }
          }}
        />
        <div className="char-col">
          <Live2DCanvas
            modelUrl={modelUrl}
            emotionMap={emotionMap}
            tapMotions={tapMotions}
            onAdapterReady={(a) => {
              adapterRef.current = a;
            }}
            onError={onError}
          />
          <div className="char-status-bar">
            <AffectionBadge affection={affection} />
            <EmotionBadge emotion={emotion} />
            <span className="conn-dot" data-status={connStatus} />
          </div>
        </div>
      </div>
    </div>
  );
}

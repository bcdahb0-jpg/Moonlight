import { useState, type ReactElement } from 'react';
import { AppStateProvider, useAppState } from '@/state/AppStateContext';
import { useAppShell } from '@/hooks/useAppShell';
import { PetView } from '@/components/PetView';
import { PetOverlay } from '@/components/PetOverlay';
import { WindowModeView } from '@/components/WindowModeView';
import { Dashboard, type DashboardSection } from '@/dashboard/Dashboard';
import { Onboarding, ONBOARDED_KEY } from '@/onboarding/Onboarding';

function setGlobalError(error: Error): void {
  // eslint-disable-next-line no-console
  console.error('[live2d]', error);
}

function AppInner(): ReactElement {
  const { state, dispatch } = useAppState();
  const shell = useAppShell();

  const [onboarded, setOnboarded] = useState<boolean>(() => {
    try {
      return localStorage.getItem(ONBOARDED_KEY) === '1';
    } catch {
      return false;
    }
  });
  // 错误修复卡跳转的设置分区（Dashboard initialSection）。
  const [settingsSection, setSettingsSection] = useState<DashboardSection | null>(null);

  const openSettingsSection = (section: string): void => {
    setSettingsSection(section as DashboardSection);
    shell.setSettingsOpen(true);
  };

  /** 打断 AI 回复：本地立即复位转圈状态（不等后端 control 回包，防 WebSocket 延迟/断线卡死）。 */
  const handleInterrupt = (): void => {
    shell.audioPlayerRef.current?.stop();
    dispatch({ type: 'SET_THINKING', thinking: false });
    dispatch({ type: 'SET_SUBTITLE', text: '' });
    shell.wsRef.current?.sendInterrupt();
  };

  /** 语音录音结束：先回显占位消息（识别中），后端识别完成后原地替换为文本。 */
  const handleMicAudioEnd = (): void => {
    shell.wsRef.current?.sendMicAudioEnd();
    dispatch({
      type: 'ADD_MESSAGE',
      message: {
        id: `mic_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 8)}`,
        role: 'user',
        text: '🎙️ 正在识别…',
        timestamp: Date.now(),
        streaming: true,
      },
    });
  };

  /** 文字输入：本地立即回显（后端 text-input 不回传用户消息，不本地加
   *  这条聊天区会一直空到自己说完），随后经 WS 发给后端。 */
  const handleTextSend = (text: string): void => {
    const trimmed = text.trim();
    if (!trimmed) return;
    dispatch({
      type: 'ADD_MESSAGE',
      message: {
        id: `text_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 8)}`,
        role: 'user',
        text: trimmed,
        timestamp: Date.now(),
      },
    });
    shell.wsRef.current?.sendTextInput(trimmed);
  };

  if (!onboarded) {
    return <Onboarding onComplete={() => setOnboarded(true)} />;
  }

  const petView = shell.petVisible ? (
    state.modelUrl ? (
      <PetView
        modelUrl={state.modelUrl}
        emotionMap={state.modelInfo?.emotionMap}
        tapMotions={state.modelInfo?.tapMotions}
        adapterRef={shell.adapterRef}
        onError={setGlobalError}
        onInteract={(zone) => shell.wsRef.current?.sendInteract(zone)}
      />
    ) : (
      <div className="pet-loading">
        <div className="spinner" />
        <span>正在连接后端…</span>
      </div>
    )
  ) : (
    <WindowModeView
      modelUrl={state.modelUrl ?? ''}
      emotionMap={state.modelInfo?.emotionMap}
      tapMotions={state.modelInfo?.tapMotions}
      adapterRef={shell.adapterRef}
      messages={state.messages}
      isThinking={state.isThinking}
      subtitle={state.subtitle}
      connected={state.connStatus === 'connected'}
      historyList={state.historyList}
      currentHistoryUid={state.currentHistoryUid}
      lastError={state.lastError}
      errorCode={state.errorCode}
      affection={state.affection}
      emotion={state.emotion}
      connStatus={state.connStatus}
      onDismissError={() => dispatch({ type: 'SET_ERROR', message: null })}
      onOpenSettingsSection={openSettingsSection}
      onToggleMode={shell.toggleMode}
      onMicAudioEnd={handleMicAudioEnd}
      onSendText={handleTextSend}
      onInterrupt={handleInterrupt}
      ws={() => shell.wsRef.current}
      audioPlayer={() => shell.audioPlayerRef.current}
      onError={setGlobalError}
    />
  );

  return (
    <div
      className={`app ${shell.petVisible ? 'pet-mode' : 'window-mode'} ${shell.maximized ? 'app-maximized' : ''}`}
      onPointerDown={shell.handlePointerDown}
      onPointerMove={shell.handlePointerMove}
      onPointerUp={shell.handlePointerUp}
    >
      {petView}

      {/* 桌宠模式才显示悬浮控制条；窗口模式的操作已移入标题栏，
          心情/状态移入角色框（char-status-bar） */}
      {shell.petVisible ? (
        <PetOverlay
          affection={state.affection}
          emotion={state.emotion}
          connStatus={state.connStatus}
          errorCode={state.errorCode}
          onToggleMode={shell.toggleMode}
          onOpenSettings={() => shell.setSettingsOpen(true)}
          onInterrupt={handleInterrupt}
        />
      ) : null}

      {shell.settingsOpen ? (
        <Dashboard
          key={settingsSection ?? 'default'}
          initialSection={settingsSection ?? undefined}
          onClose={() => shell.setSettingsOpen(false)}
          ws={() => shell.wsRef.current}
        />
      ) : null}
    </div>
  );
}

export function App(): ReactElement {
  return (
    <AppStateProvider>
      <AppInner />
    </AppStateProvider>
  );
}

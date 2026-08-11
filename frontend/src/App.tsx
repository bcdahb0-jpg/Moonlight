import { useState, type ReactElement } from 'react';
import { AppStateProvider, useAppState } from '@/state/AppStateContext';
import { useAppShell } from '@/hooks/useAppShell';
import { PetView } from '@/components/PetView';
import { PetOverlay } from '@/components/PetOverlay';
import { WindowModeView } from '@/components/WindowModeView';
import { Dashboard, type DashboardSection } from '@/dashboard/Dashboard';
import { Onboarding, ONBOARDED_KEY } from '@/onboarding/Onboarding';
import type { TaskEvent } from '@/task/types';

function setGlobalError(error: Error): void {
  // eslint-disable-next-line no-console
  console.error('[live2d]', error);
}

function AppInner(): ReactElement {
  const { state, dispatch } = useAppState();
  const shell = useAppShell();

  // 引擎类型诊断（soullink / legacy）：Live2DCanvas 选择完成后回调
  const [engineType, setEngineType] = useState<'soullink' | 'legacy' | null>(null);

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

  /** 文字输入：本地立即回显（0ms 反馈，不阻塞于意图路由 / WS / LLM 分类）。
   *  发送由 WindowModeView 在后台意图路由完成后按 chat/task 分流——回显先行，
   *  否则意图分类的 HTTP/LLM 延迟（可达数秒）会让聊天区「空窗」。
   *  UX 修复（2026-08-10）：返回生成的用户消息 id——任务分支用它把执行卡
   *  锚定到这条用户消息下方（不再沉在消息流末尾）。 */
  const handleEchoText = (text: string): string => {
    const trimmed = text.trim();
    if (!trimmed) return '';
    const id = `text_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 8)}`;
    dispatch({
      type: 'ADD_MESSAGE',
      message: {
        id,
        role: 'user',
        text: trimmed,
        timestamp: Date.now(),
      },
    });
    return id;
  };

  /** 文字输入发送（聊天链路）：回显已由 handleEchoText 先行，这里只发 WS。 */
  const handleTextSend = (text: string): void => {
    const trimmed = text.trim();
    if (!trimmed) return;
    shell.wsRef.current?.sendTextInput(trimmed);
  };

  /** 任务模式：用户消息本地回显（不发 WS，任务走 REST/SSE）。 */
  const handleTaskUserEcho = (text: string): void => {
    const trimmed = text.trim();
    if (!trimmed) return;
    dispatch({
      type: 'ADD_MESSAGE',
      message: {
        id: `task_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 8)}`,
        role: 'user',
        text: trimmed,
        timestamp: Date.now(),
      },
    });
  };

  /** 任务模式：角色外壳汇报气泡（G7 分流 shell 侧；core 事件进执行记录卡片）。
   *
   * P0 外壳转述落地后：正常路径后端经 WS `audio` 消息广播角色转述
   * （气泡 + 语音 + Live2D 表情，见 messageHandlers.handleAudio），本函数
   * 不再重复加硬编码气泡（否则每条 run 事件出现双气泡）。
   *
   * 兜底保障：后端 shell 播报全程 fail-soft——LLM 转述失败回退模板文案、
   * TTS 失败发静默 payload（气泡仍显示）；即使 WS 广播异常，任务执行卡
   * （TaskRunCard）仍提供状态反馈。此处仅保留函数签名兼容。 */
  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  const handleTaskShellEvent = (_ev: TaskEvent): void => {
    // no-op：外壳汇报由后端 WS audio 消息驱动（P0）
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
        onEngineChange={setEngineType}
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
      toolStatus={state.toolStatus}
      connected={state.connStatus === 'connected'}
      historyList={state.historyList}
      currentHistoryUid={state.currentHistoryUid}
      lastError={state.lastError}
      errorCode={state.errorCode}
      affection={state.affection}
      emotion={state.emotion}
      emotionIntensity={state.emotionIntensity}
      emotionSource={state.emotionSource}
      engineType={engineType}
      connStatus={state.connStatus}
      onEngineChange={setEngineType}
      onDismissError={() => dispatch({ type: 'SET_ERROR', message: null })}
      onOpenSettingsSection={openSettingsSection}
      onToggleMode={shell.toggleMode}
      onMicAudioEnd={handleMicAudioEnd}
      onEchoText={handleEchoText}
      onSendText={handleTextSend}
      onInterrupt={handleInterrupt}
      onTaskUserEcho={handleTaskUserEcho}
      onTaskShellEvent={handleTaskShellEvent}
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
          emotionIntensity={state.emotionIntensity}
          emotionSource={state.emotionSource}
          engineType={engineType}
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

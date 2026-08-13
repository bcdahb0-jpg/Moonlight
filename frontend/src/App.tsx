import { useEffect, useMemo, useRef, useState, type ReactElement } from 'react';
import { AppStateProvider, useAppState } from '@/state/AppStateContext';
import { useAppShell } from '@/hooks/useAppShell';
import { PetView } from '@/components/PetView';
import { PetOverlay } from '@/components/PetOverlay';
import { WindowModeView } from '@/components/WindowModeView';
import { ControlCenter } from '@/control/ControlCenter';
import type { ControlSectionId } from '@/control/controlData';
import { Onboarding, ONBOARDED_KEY } from '@/onboarding/Onboarding';
import { ScreenAuthModal, SCREEN_AUTH_KEY } from '@/screen/ScreenAuthModal';
import type { TaskEvent } from '@/task/types';
import { usePttSession } from '@/chat/usePttSession';

function setGlobalError(error: Error): void {
  // eslint-disable-next-line no-console
  console.error('[live2d]', error);
}

function AppInner(): ReactElement {
  const { state, dispatch } = useAppState();
  const shell = useAppShell();

  // Phase 1（pet-ptt-workflow）：当前会话绑定的工作目录（会话共用 UID 的来源）。
  const currentWorkspace = useMemo(() => {
    const cur = state.historyList.find(
      (h) => String(h.uid ?? h.history_uid ?? '') === state.currentHistoryUid,
    );
    return String(cur?.workspace ?? '').trim() || undefined;
  }, [state.historyList, state.currentHistoryUid]);

  // Phase 1：桌宠对讲会话确保（无会话 → 自动创建，PCM 暂存后发送）。
  const pttSession = usePttSession({
    ws: () => shell.wsRef.current,
    getHistoryUid: () => state.currentHistoryUid,
    getHistoryList: () => state.historyList,
  });

  // 引擎类型诊断（soullink / legacy）：Live2DCanvas 选择完成后回调
  const [engineType, setEngineType] = useState<'soullink' | 'legacy' | null>(null);

  const [onboarded, setOnboarded] = useState<boolean>(() => {
    try {
      return localStorage.getItem(ONBOARDED_KEY) === '1';
    } catch {
      return false;
    }
  });
  // 错误修复卡跳转的控制台分区（同名映射：role/brain/voice/sense/…）。
  const [settingsSection, setSettingsSection] = useState<ControlSectionId | null>(null);
  const taskReportKeysRef = useRef(new Set<string>());

  // Phase 5：首次开启屏幕感知 → 授权说明（确认后 localStorage 标记，不再打扰）。
  const [screenAuthOpen, setScreenAuthOpen] = useState(false);
  const [screenAuthGranted, setScreenAuthGranted] = useState<boolean>(() => {
    try {
      return localStorage.getItem(SCREEN_AUTH_KEY) === '1';
    } catch {
      return false;
    }
  });
  const screenAuthCheckedRef = useRef(false);

  useEffect(() => {
    if (!state.settings.screenAwareEnabled || screenAuthGranted || screenAuthCheckedRef.current) {
      return;
    }
    screenAuthCheckedRef.current = true;
    // 授权前立即回滚开关，保证设置同步和采集 hook 都处于关闭态。
    dispatch({ type: 'UPDATE_SETTINGS', settings: { screenAwareEnabled: false } });
    setScreenAuthOpen(true);
  }, [dispatch, screenAuthGranted, state.settings.screenAwareEnabled]);

  const confirmScreenAuth = (): void => {
    try {
      localStorage.setItem(SCREEN_AUTH_KEY, '1');
    } catch {
      /* storage unavailable */
    }
    setScreenAuthGranted(true);
    dispatch({ type: 'UPDATE_SETTINGS', settings: { screenAwareEnabled: true } });
    setScreenAuthOpen(false);
  };

  /** Phase 5：眼睛状态灯快捷暂停/恢复。 */
  const toggleScreenAwareness = (): void => {
    dispatch({
      type: 'UPDATE_SETTINGS',
      settings: { screenAwareEnabled: !state.settings.screenAwareEnabled },
    });
    if (state.settings.screenAwareEnabled) {
      // 关闭：立即停采并清空后端上下文。
      void import('@/screen/screenActions').then(({ screenActions: sa }) => sa.pause('user_paused'));
    }
  };

  const openSettingsSection = (section: string): void => {
    // 旧版传 'general' 等值，控制台侧统一回退到概览分区
    setSettingsSection(section as ControlSectionId);
    shell.setSettingsOpen(true);
  };

  /** 打断 AI 回复：本地立即复位转圈状态（不等后端 control 回包，防 WebSocket 延迟/断线卡死）。 */
  const handleInterrupt = (): void => {
    shell.audioPlayerRef.current?.stop();
    dispatch({ type: 'SET_THINKING', thinking: false });
    dispatch({ type: 'SET_SUBTITLE', text: '' });
    shell.wsRef.current?.sendInterrupt();
  };

  // P6 UI 重构：桌宠「勿扰」静音开关（本地状态；开启即打断当前语音）。
  const [petMuted, setPetMuted] = useState(false);
  const togglePetMute = (): void => {
    setPetMuted((prev) => {
      const next = !prev;
      if (next) handleInterrupt();
      return next;
    });
  };

  /** 语音录音结束：先回显占位消息（识别中），后端识别完成后原地替换为文本。
   *  Phase 1：透传当前会话 workspace（无目录会话输入时后端引导，不静默伪造）。 */
  const handleMicAudioEnd = (): void => {
    shell.wsRef.current?.sendMicAudioEnd(currentWorkspace);
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

  /** Phase 0（PTT）：桌宠对讲 —— 边录边发（与窗口模式 ChatInput 一致）。
   *  每个 PCM chunk 实时发送；无会话时先自动创建（workspace 解析），
   *  创建完成前 chunk 经 ensurePttHistory 暂存，完成后按序 flush。
   *  修复（2026-08-11）：改为实时发送 + promise 排队，取代「录完一次性
   *  发送缓冲」的链路（该链路曾因缓冲交付时序发空音频）。 */
  const ensurePttHistoryRef = useRef<Promise<string | null> | null>(null);
  const ensurePttHistory = (): Promise<string | null> => {
    if (!ensurePttHistoryRef.current) {
      ensurePttHistoryRef.current = pttSession
        .ensureHistory()
        .finally(() => {
          ensurePttHistoryRef.current = null;
        });
    }
    return ensurePttHistoryRef.current;
  };

  const handlePttChunk = (chunk: Float32Array): void => {
    const ws = shell.wsRef.current;
    if (!ws) return;
    void ensurePttHistory().then((uid) => {
      if (!uid) return; // 会话创建失败：丢弃（handlePttEnd 统一报错）
      shell.wsRef.current?.sendMicAudioChunk(chunk);
    });
  };

  const handlePttEnd = (): void => {
    void ensurePttHistory().then((uid) => {
      if (!uid) {
        dispatch({
          type: 'SET_ERROR',
          message: '无法自动创建会话（工作目录不可用），请切到窗口模式选择工作目录。',
        });
        return;
      }
      // 语音回合刷新屏幕快照（fire-and-forget：供后端 _attach_screen_context 注入）。
      if (shell.state.settings.screenAwareEnabled) {
        void import('@/screen/screenActions').then(({ screenActions: sa }) => {
          void sa.captureOnce();
        });
      }
      handleMicAudioEnd();
    });
  };

  const handlePttMicError = (error: Error): void => {
    dispatch({
      type: 'SET_ERROR',
      message: `麦克风不可用：${error.message}`,
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

  /** 文字输入发送（聊天链路）：回显已由 handleEchoText 先行，这里只发 WS。
   *  修复（2026-08-11）：用户消息命中屏幕关键词时，先按需采集一帧并等待
   *  后端分析出新快照再发送 —— 摘要 TTL 45s 过期/静态画面去重后，问屏幕
   *  依然能拿到新鲜上下文（此前仅 captureOnDemand 模式触发，且不等待）。 */
  const handleTextSend = async (text: string): Promise<void> => {
    const trimmed = text.trim();
    if (!trimmed) return;
    if (shell.state.settings.screenAwareEnabled) {
      await import('@/screen/onDemandCapture').then(({ maybeCaptureOnDemand }) =>
        maybeCaptureOnDemand(trimmed),
      );
    }
    shell.wsRef.current?.sendTextInput(trimmed, currentWorkspace);
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
    if (_ev.event_type !== 'run_end') return;
    const payload = _ev.payload as { status?: unknown; report?: unknown };
    const report = typeof payload.report === 'string' ? payload.report.trim() : '';
    if (!report || payload.status !== 'completed') return;
    // SSE 可能在断线重连后回放同一事件；每个 run 只投影一条气泡。
    const key = `task-report:${_ev.task_id}:${_ev.run_id}`;
    if (taskReportKeysRef.current.has(key)) return;
    if (state.messages.some((message) => message.role === 'ai' && message.text.trim() === report)) {
      taskReportKeysRef.current.add(key);
      return;
    }
    taskReportKeysRef.current.add(key);
    dispatch({
      type: 'ADD_MESSAGE',
      message: {
        id: key,
        role: 'ai',
        text: report,
        name: state.characterName || state.confName || 'hiyori',
        timestamp: Date.now(),
      },
    });
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
      screenStatus={state.screenStatus}
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
          // Phase 5：屏幕感知眼睛状态灯（快捷暂停/恢复）
          screenEnabled={state.settings.screenAwareEnabled}
          screenAuthorized={screenAuthGranted}
          screenCapturing={state.screenStatus?.capturing ?? false}
          screenLastAnalyzeAt={state.screenStatus?.last_analyze_at ?? null}
          screenError={state.screenStatus?.last_error ?? null}
          onToggleScreen={toggleScreenAwareness}
          // Phase 0：对讲（PTT）——AI 状态 = 思考中 || 语音播放中；边录边发
          aiSpeaking={state.isThinking || shell.audioPlaying}
          onPttChunk={handlePttChunk}
          onPttEnd={handlePttEnd}
          onMicError={handlePttMicError}
          // Phase 3：桌宠字幕条（最近一条流式 AI full-text）
          petSubtitle={state.petSubtitle}
          // P6 UI 重构：功能快捷入口 + 勿扰静音
          onOpenSection={openSettingsSection}
          muted={petMuted}
          onToggleMute={togglePetMute}
        />
      ) : null}

      {/* Phase 5：首次开启屏幕感知的授权说明 */}
      <ScreenAuthModal
        open={screenAuthOpen}
        onConfirm={confirmScreenAuth}
          onCancel={() => {
            screenAuthCheckedRef.current = false;
            setScreenAuthOpen(false);
            dispatch({ type: 'UPDATE_SETTINGS', settings: { screenAwareEnabled: false } });
          }}
      />

      {shell.settingsOpen ? (
        <ControlCenter
          key={settingsSection ?? 'default'}
          initialSection={settingsSection ?? undefined}
          onClose={() => shell.setSettingsOpen(false)}
          ws={() => shell.wsRef.current}
          settingsSync={shell.settingsSync}
          confUid={state.confUid}
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

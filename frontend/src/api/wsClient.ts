import type { ClientMessage, DisplayText, ServerMessage } from '@/types/ws';
import { isServerMessage } from '@/types/ws';

export const WS_URL = 'ws://127.0.0.1:12393/client-ws';
export const WS_HEARTBEAT_INTERVAL_MS = 25_000;
export const WS_RECONNECT_BASE_MS = 1_000;
export const WS_RECONNECT_MAX_MS = 15_000;

function randomUid(): string {
  if (typeof crypto !== 'undefined' && 'randomUUID' in crypto) {
    return crypto.randomUUID();
  }
  return `uid_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 10)}`;
}

export interface WSClientOptions {
  onMessage: (msg: ServerMessage) => void;
  onStatusChange: (status: 'connecting' | 'connected' | 'disconnected') => void;
}

/**
 * WebSocket client for the Open-LLM-VTuber backend. Owns connection lifecycle,
 * automatic reconnection with backoff, and a periodic heartbeat. All outgoing
 * message builders from the spec (text-input / mic-audio / interrupt /
 * ai-speak-signal / history / config) are exposed as methods.
 */
export class WSClient {
  private ws: WebSocket | null = null;
  private readonly uid: string;
  private readonly options: WSClientOptions;
  private reconnectTimer: number | null = null;
  private heartbeatTimer: number | null = null;
  private reconnectAttempt = 0;
  private manuallyClosed = false;
  private alive = false;

  constructor(options: WSClientOptions) {
    this.options = options;
    this.uid = randomUid();
  }

  get clientUid(): string {
    return this.uid;
  }

  connect(): void {
    this.manuallyClosed = false;
    this.open();
  }

  disconnect(): void {
    this.manuallyClosed = true;
    this.clearTimers();
    this.ws?.close();
    this.ws = null;
  }

  // ------------------------------------------------------------------ //
  // Outgoing messages (spec: websocket_handler.MessageType)
  // ------------------------------------------------------------------ //

  sendTextInput(text: string): void {
    this.send({ type: 'text-input', text });
  }

  sendMicAudioChunk(chunk: Float32Array): void {
    if (!this.ws || this.ws.readyState !== WebSocket.OPEN) return;
    // Binary PCM avoids allocating a JS number array and serializing thousands
    // of floats to JSON for every microphone callback.
    const frame = chunk.buffer.slice(
      chunk.byteOffset,
      chunk.byteOffset + chunk.byteLength,
    );
    this.ws.send(frame);
  }

  sendMicAudioEnd(): void {
    this.send({ type: 'mic-audio-end' });
  }

  sendInterrupt(heardResponse = ''): void {
    this.send({ type: 'interrupt-signal', text: heardResponse });
  }

  sendAiSpeakSignal(): void {
    this.send({ type: 'ai-speak-signal' });
  }

  sendHeartbeat(): void {
    this.send({ type: 'heartbeat' });
  }

  sendFetchConfigs(): void {
    this.send({ type: 'fetch-configs' });
  }

  sendSwitchConfig(file: string): void {
    this.send({ type: 'switch-config', file });
  }

  sendFetchHistoryList(): void {
    this.send({ type: 'fetch-history-list' });
  }

  sendFetchAndSetHistory(historyUid: string): void {
    this.send({ type: 'fetch-and-set-history', history_uid: historyUid });
  }

  /** v5：新建会话必须绑定工作目录（绝对路径）。 */
  sendCreateNewHistory(workspace: string): void {
    this.send({ type: 'create-new-history', workspace });
  }

  sendDeleteHistory(historyUid: string): void {
    this.send({ type: 'delete-history', history_uid: historyUid });
  }

  /** 重命名会话（自定义会话标题）。 */
  sendSetHistoryTitle(historyUid: string, title: string): void {
    this.send({ type: 'set-history-title', history_uid: historyUid, title });
  }

  /** v5：把会话移动到另一个工作目录。 */
  sendSetHistoryWorkspace(historyUid: string, workspace: string): void {
    this.send({ type: 'set-history-workspace', history_uid: historyUid, workspace });
  }

  /** v5：清空该角色全部会话（存量无目录会话一次性清理，调用方需先确认）。 */
  sendClearAllHistories(): void {
    this.send({ type: 'clear-all-histories' });
  }

  sendAudioPlayStart(displayText?: DisplayText | null): void {
    this.send({ type: 'audio-play-start', display_text: displayText ?? undefined });
  }

  /** 养成交互：点击/摸头等互动区域。 */
  sendInteract(zone = 'click'): void {
    this.send({ type: 'interact', zone });
  }

  /** Sent after the frontend finishes playing an audio turn (unblocks backend). */
  sendFrontendPlaybackComplete(): void {
    this.send({ type: 'frontend-playback-complete' });
  }

  private send(message: ClientMessage): void {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify(message));
    }
  }

  // ------------------------------------------------------------------ //
  // Connection internals
  // ------------------------------------------------------------------ //

  private open(): void {
    this.options.onStatusChange('connecting');
    const url = `${WS_URL}?uid=${encodeURIComponent(this.uid)}`;
    const ws = new WebSocket(url);
    this.ws = ws;

    ws.onopen = () => {
      this.alive = true;
      this.reconnectAttempt = 0;
      this.options.onStatusChange('connected');
      this.startHeartbeat();
    };

    ws.onmessage = (event: MessageEvent<string>) => {
      try {
        const raw: unknown = JSON.parse(event.data);
        if (isServerMessage(raw)) this.options.onMessage(raw);
      } catch {
        // Ignore malformed frames; the backend guards its own JSON errors.
      }
    };

    ws.onclose = () => {
      this.alive = false;
      this.stopHeartbeat();
      if (!this.manuallyClosed) {
        this.options.onStatusChange('disconnected');
        this.scheduleReconnect();
      }
    };

    ws.onerror = () => {
      // onclose follows; no-op here to avoid duplicate reconnect scheduling.
    };
  }

  private scheduleReconnect(): void {
    if (this.manuallyClosed || this.reconnectTimer !== null) return;
    const delay = Math.min(
      WS_RECONNECT_BASE_MS * 2 ** this.reconnectAttempt,
      WS_RECONNECT_MAX_MS,
    );
    this.reconnectAttempt += 1;
    this.reconnectTimer = window.setTimeout(() => {
      this.reconnectTimer = null;
      this.open();
    }, delay);
  }

  private startHeartbeat(): void {
    this.stopHeartbeat();
    this.heartbeatTimer = window.setInterval(() => {
      if (this.alive) this.sendHeartbeat();
    }, WS_HEARTBEAT_INTERVAL_MS);
  }

  private stopHeartbeat(): void {
    if (this.heartbeatTimer !== null) {
      window.clearInterval(this.heartbeatTimer);
      this.heartbeatTimer = null;
    }
  }

  private clearTimers(): void {
    this.stopHeartbeat();
    if (this.reconnectTimer !== null) {
      window.clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
  }
}

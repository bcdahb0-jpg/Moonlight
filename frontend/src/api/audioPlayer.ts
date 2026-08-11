/**
 * Queued audio player built on the Web Audio API. Each audio item is decoded
 * from base64 WAV and played sequentially. During playback the caller's
 * `onVolume` callback is driven from the backend's per-slice RMS volumes array
 * (used for Live2D lip-sync), and `onItemEnd` fires when an item finishes.
 */
export interface AudioItem {
  id: string;
  base64: string | null;
  /** One normalized RMS value per `sliceLengthMs` slice (backend `volumes`). */
  volumes: number[];
  /**
   * 音素级口型（可选）：每 slice 一个 [a,i,u,e,o] 概率向量，与 volumes
   * 同粒度。缺失时前端回退 RMS 驱动口型。
   */
  visemes?: number[][] | null;
  /** 情绪强度/时长元数据（Phase 1 面部表情）。 */
  emotionMeta?: { intensity?: number; duration_ms?: number; source?: string } | null;
  sliceLengthMs: number;
  expression?: Array<string | number> | null;
  displayText?: string | null;
}

export interface AudioPlayerOptions {
  onVolume: (value: number, viseme?: number[] | null) => void;
  /** Fired right before an item starts producing sound (apply expression here). */
  onItemStart: (item: AudioItem) => void;
  onItemEnd: (item: AudioItem) => void;
  onQueueEmpty: () => void;
  /**
   * Fired when `stop()` is called (interrupt). `stop()` does NOT trigger
   * `onItemEnd` — it clears the queue and closes the AudioContext, so the
   * caller must reset lip-sync/motion state here immediately.
   */
  onStop?: () => void;
}

const VOLUME_TICK_MS = 20;

function base64ToArrayBuffer(base64: string): ArrayBuffer {
  const binary = atob(base64);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
  return bytes.buffer;
}

export class AudioPlayer {
  private ctx: AudioContext | null = null;
  private queue: AudioItem[] = [];
  private playing = false;
  private volumeTimer: number | null = null;
  private currentItem: AudioItem | null = null;
  private currentSource: AudioBufferSourceNode | null = null;
  private currentStartAt = 0;
  private readonly options: AudioPlayerOptions;

  constructor(options: AudioPlayerOptions) {
    this.options = options;
  }

  enqueue(item: AudioItem): void {
    this.queue.push(item);
    void this.pump();
  }

  stop(): void {
    this.queue = [];
    this.clearVolumeTimer();
    if (this.ctx && this.ctx.state !== 'closed') {
      void this.ctx.close().catch(() => undefined);
    }
    try {
      this.currentSource?.stop();
    } catch {
      // source may already have ended
    }
    this.currentSource = null;
    this.ctx = null;
    this.playing = false;
    this.currentItem = null;
    this.options.onStop?.();
  }

  get isPlaying(): boolean {
    return this.playing || this.queue.length > 0;
  }

  private async pump(): Promise<void> {
    if (this.playing) return;
    const item = this.queue.shift();
    if (!item) {
      this.options.onQueueEmpty();
      return;
    }
    this.playing = true;
    this.currentItem = item;
    this.options.onItemStart(item);
    try {
      await this.playItem(item);
    } catch {
      // A malformed/undecodable item is skipped; continue with the queue.
    } finally {
      this.playing = false;
      this.currentItem = null;
      this.clearVolumeTimer();
      this.options.onItemEnd(item);
      void this.pump();
    }
  }

  private async playItem(item: AudioItem): Promise<void> {
    if (!item.base64) {
      // Silent display item: still fire a quick fade so the queue advances.
      await new Promise((resolve) => setTimeout(resolve, 80));
      return;
    }

    const ctx = this.getContext();
    const arrayBuffer = base64ToArrayBuffer(item.base64);
    let audioBuffer: AudioBuffer;
    try {
      audioBuffer = await ctx.decodeAudioData(arrayBuffer);
    } catch (err) {
      throw err;
    }

    const source = ctx.createBufferSource();
    this.currentSource = source;
    source.buffer = audioBuffer;
    const gain = ctx.createGain();
    gain.gain.value = 1;
    source.connect(gain);
    gain.connect(ctx.destination);
    source.start();

    this.currentStartAt = ctx.currentTime;
    this.startVolumeTimer(item);

    await new Promise<void>((resolve) => {
      source.onended = () => {
        if (this.currentSource === source) this.currentSource = null;
        resolve();
      };
    });
  }

  private getContext(): AudioContext {
    if (!this.ctx) {
      this.ctx = new AudioContext();
    }
    if (this.ctx.state === 'suspended') {
      void this.ctx.resume().catch(() => undefined);
    }
    return this.ctx;
  }

  private startVolumeTimer(item: AudioItem): void {
    this.clearVolumeTimer();
    const step = Math.max(1, Math.round(item.sliceLengthMs || VOLUME_TICK_MS));
    const tick = (): void => {
      if (!this.ctx || !this.currentItem) return;
      const elapsedMs = (this.ctx.currentTime - this.currentStartAt) * 1000;
      // 浮点切片位置 + 前后 slice 线性插值：消除 20ms 粒度查表带来的量化跳变
      const pos = elapsedMs / step;
      const i = Math.floor(pos);
      const frac = pos - i;
      const v0 = item.volumes[i] ?? 0;
      const v1 = item.volumes[i + 1] ?? v0;
      // 元音 viseme 随播放时间推进取对应 slice（与 volumes 同粒度）
      const viseme = item.visemes ? (item.visemes[i] ?? null) : null;
      this.options.onVolume(v0 + (v1 - v0) * frac, viseme);
    };
    this.volumeTimer = window.setInterval(tick, VOLUME_TICK_MS);
  }

  private clearVolumeTimer(): void {
    if (this.volumeTimer !== null) {
      window.clearInterval(this.volumeTimer);
      this.volumeTimer = null;
    }
    this.options.onVolume(0);
  }
}

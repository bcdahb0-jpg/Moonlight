/**
 * Microphone capture that streams mono Float32 PCM at ~16 kHz. Uses a
 * ScriptProcessorNode (universally supported) with linear down-sampling to
 * 16 kHz, matching what the backend ASR expects (`mic-audio-data` chunks).
 */
export class VoiceRecorder {
  private ctx: AudioContext | null = null;
  private stream: MediaStream | null = null;
  private source: MediaStreamAudioSourceNode | null = null;
  private processor: ScriptProcessorNode | null = null;
  private readonly onChunk: (chunk: Float32Array) => void;
  private readonly onEnd: () => void;
  private readonly onError: (error: Error) => void;

  constructor(
    onChunk: (chunk: Float32Array) => void,
    onEnd: () => void,
    onError: (error: Error) => void,
  ) {
    this.onChunk = onChunk;
    this.onEnd = onEnd;
    this.onError = onError;
  }

  async start(): Promise<void> {
    try {
      this.stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
        },
      });

      // Prefer a 16 kHz context; fall back to the device rate and resample.
      let rate = 16000;
      try {
        this.ctx = new AudioContext({ sampleRate: 16000 });
        rate = this.ctx.sampleRate;
      } catch {
        this.ctx = new AudioContext();
        rate = this.ctx.sampleRate;
      }

      this.source = this.ctx.createMediaStreamSource(this.stream);
      this.processor = this.ctx.createScriptProcessor(4096, 1, 1);
      this.processor.onaudioprocess = (event) => {
        const input = event.inputBuffer.getChannelData(0);
        this.onChunk(resampleToRate(input, rate, 16000));
      };
      this.source.connect(this.processor);
      this.processor.connect(this.ctx.destination);
    } catch (err) {
      this.onError(err instanceof Error ? err : new Error('麦克风启动失败'));
    }
  }

  stop(): void {
    if (this.processor) {
      this.processor.disconnect();
      this.processor.onaudioprocess = null;
      this.processor = null;
    }
    if (this.source) {
      this.source.disconnect();
      this.source = null;
    }
    if (this.stream) {
      this.stream.getTracks().forEach((track) => track.stop());
      this.stream = null;
    }
    if (this.ctx) {
      void this.ctx.close().catch(() => undefined);
      this.ctx = null;
    }
    this.onEnd();
  }

  dispose(): void {
    this.stop();
  }
}

/** Linear-interpolation down-sampler. */
function resampleToRate(input: Float32Array, inputRate: number, outputRate: number): Float32Array {
  if (inputRate <= 0 || outputRate <= 0 || inputRate === outputRate) return input;
  const ratio = inputRate / outputRate;
  const outLen = Math.max(1, Math.floor(input.length / ratio));
  const out = new Float32Array(outLen);
  for (let i = 0; i < outLen; i++) {
    const pos = i * ratio;
    const i0 = Math.floor(pos);
    const i1 = Math.min(i0 + 1, input.length - 1);
    const frac = pos - i0;
    out[i] = input[i0] * (1 - frac) + input[i1] * frac;
  }
  return out;
}

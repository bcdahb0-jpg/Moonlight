// 本文件由 scripts/generate-config-types.mjs 自动生成，请勿手改。
// 数据源：contracts/config-schema.json（后端 pydantic Config.model_json_schema()）。
// 重新生成：node scripts/generate-config-types.mjs

export interface ASRConfig {
  asr_model: "faster_whisper" | "whisper_cpp" | "whisper" | "azure_asr" | "fun_asr" | "groq_whisper_asr" | "sherpa_onnx_asr";
  azure_asr?: AzureASRConfig | null;
  faster_whisper?: FasterWhisperConfig | null;
  whisper_cpp?: WhisperCPPConfig | null;
  whisper?: WhisperConfig | null;
  fun_asr?: FunASRConfig | null;
  groq_whisper_asr?: GroqWhisperASRConfig | null;
  sherpa_onnx_asr?: SherpaOnnxASRConfig | null;
}

export interface AgentConfig {
  conversation_agent_choice: "basic_memory_agent" | "mem0_agent" | "hume_ai_agent" | "letta_agent";
  agent_settings: AgentSettings;
  llm_configs: StatelessLLMConfigs;
}

export interface AgentSettings {
  basic_memory_agent?: BasicMemoryAgentConfig | null;
  mem0_agent?: Mem0Config | null;
  hume_ai_agent?: HumeAIConfig | null;
  letta_agent?: LettaConfig | null;
}

export interface AzureASRConfig {
  api_key: string;
  region: string;
  languages?: Array<string>;
}

export interface AzureTTSConfig {
  api_key: string;
  region: string;
  voice: string;
  pitch: string;
  rate: string;
}

export interface BarkTTSConfig {
  voice: string;
}

export interface BasicMemoryAgentConfig {
  llm_provider: "stateless_llm_with_template" | "openai_compatible_llm" | "claude_llm" | "llama_cpp_llm" | "ollama_llm" | "lmstudio_llm" | "openai_llm" | "gemini_llm" | "zhipu_llm" | "deepseek_llm" | "groq_llm" | "mistral_llm";
  faster_first_response?: boolean | null;
  segment_method?: "regex" | "pysbd";
  use_mcpp?: boolean | null;
  mcp_enabled_servers?: Array<string> | null;
}

export interface BiliBiliLiveConfig {
  room_ids?: Array<number>;
  sessdata?: string;
}

export interface CartesiaTTSConfig {
  model_id?: "sonic-3" | "sonic-2" | "sonic-turbo" | "sonic-multilingual" | "sonic";
  api_key: string;
  voice_id: string;
  output_format?: "wav" | "mp3";
  language?: "en" | "fr" | "de" | "es" | "pt" | "zh" | "ja" | "hi" | "it" | "ko" | "nl" | "pl" | "ru" | "sv" | "tr" | "tl" | "bg" | "ro" | "ar" | "cs" | "el" | "fi" | "hr" | "ms" | "sk" | "da" | "ta" | "uk" | "hu" | "no" | "vi" | "bn" | "th" | "he" | "ka" | "id" | "te" | "gu" | "kn" | "ml" | "mr" | "pa";
  emotion?: "neutral" | "angry" | "excited" | "content" | "sad" | "scared" | "happy" | "enthusiastic" | "elated" | "euphoric" | "triumphant" | "amazed" | "surprised" | "flirtatious" | "joking/comedic" | "curious" | "peaceful" | "serene" | "calm" | "grateful" | "affectionate" | "trust" | "sympathetic" | "anticipation" | "mysterious" | "mad" | "outraged" | "frustrated" | "agitated" | "threatened" | "disgusted" | "contempt" | "envious" | "sarcastic" | "ironic" | "dejected" | "melancholic" | "disappointed" | "hurt" | "guilty" | "bored" | "tired" | "rejected" | "nostalgic" | "wistful" | "apologetic" | "hesitant" | "insecure" | "confused" | "resigned" | "anxious" | "panicked" | "alarmed" | "proud" | "confident" | "distant" | "skeptical" | "contemplative" | "determined";
  volume?: number;
  speed?: number;
}

export interface CharacterConfig {
  conf_name: string;
  conf_uid: string;
  live2d_model_name: string;
  character_name?: string;
  human_name?: string;
  avatar?: string;
  persona_prompt: string;
  agent_config: AgentConfig;
  asr_config: ASRConfig;
  tts_config: TTSConfig;
  vad_config: VADConfig;
  tts_preprocessor_config: TTSPreprocessorConfig;
  long_term_memory_enabled?: boolean;
  core_memory_max_chars?: number;
  fts_memory_enabled?: boolean;
  fts_memory_top_k?: number;
  memory_consolidation_interval?: number;
}

export interface ClaudeConfig {
  interrupt_method?: "system" | "user";
  base_url?: string;
  llm_api_key: string;
  model: string;
}

export interface CoquiTTSConfig {
  model_name: string;
  speaker_wav?: string;
  language: string;
  device?: string;
}

export interface Cosyvoice2TTSConfig {
  client_url: string;
  mode_checkbox_group: string;
  sft_dropdown: string;
  prompt_text: string;
  prompt_wav_upload_url: string;
  prompt_wav_record_url: string;
  instruct_text: string;
  stream: boolean;
  seed: number;
  speed: number;
  api_name: string;
}

export interface CosyvoiceTTSConfig {
  client_url: string;
  mode_checkbox_group: string;
  sft_dropdown: string;
  prompt_text: string;
  prompt_wav_upload_url: string;
  prompt_wav_record_url: string;
  instruct_text: string;
  seed: number;
  api_name: string;
}

export interface DeepLXConfig {
  deeplx_target_lang: string;
  deeplx_api_endpoint: string;
}

export interface DeepseekConfig {
  interrupt_method?: "system" | "user";
  base_url?: string;
  llm_api_key: string;
  model: string;
  organization_id?: string | null;
  project_id?: string | null;
  temperature?: number;
}

export interface EdgeTTSConfig {
  voice: string;
}

export interface ElevenLabsTTSConfig {
  api_key: string;
  voice_id: string;
  model_id?: string;
  output_format?: string;
  stability?: number;
  similarity_boost?: number;
  style?: number;
  use_speaker_boost?: boolean;
}

export interface FasterWhisperConfig {
  model_path: string;
  download_root: string;
  language?: string | null;
  device?: string;
  compute_type?: "int8" | "float16" | "float32";
  prompt?: string | null;
}

export interface FishAPITTSConfig {
  api_key: string;
  reference_id: string;
  latency: "normal" | "balanced";
  base_url: string;
}

export interface FunASRConfig {
  model_name?: string;
  vad_model?: string;
  punc_model?: string;
  device?: "cpu" | "cuda";
  disable_update?: boolean;
  ncpu?: number;
  hub?: "ms" | "hf";
  use_itn?: boolean;
  language?: string;
}

export interface GPTSoVITSConfig {
  api_url: string;
  text_lang: string;
  ref_audio_path: string;
  prompt_lang: string;
  prompt_text: string;
  text_split_method: string;
  batch_size: string;
  media_type: string;
  streaming_mode: string;
}

export interface GeminiConfig {
  interrupt_method?: "system" | "user";
  base_url?: string;
  llm_api_key: string;
  model: string;
  organization_id?: string | null;
  project_id?: string | null;
  temperature?: number;
}

export interface GroqConfig {
  interrupt_method?: "system" | "user";
  base_url?: string;
  llm_api_key: string;
  model: string;
  organization_id?: string | null;
  project_id?: string | null;
  temperature?: number;
}

export interface GroqWhisperASRConfig {
  api_key: string;
  model?: string;
  lang?: string | null;
}

export interface HumeAIConfig {
  api_key: string;
  host?: string;
  config_id?: string | null;
  idle_timeout?: number;
}

export interface LLMTranslateConfig {
  api_endpoint: string;
  model: string;
  target_lang: string;
}

export interface LettaConfig {
  host?: string;
  port?: number;
  id: string;
  faster_first_response?: boolean | null;
  segment_method?: "regex" | "pysbd";
}

export interface LiveConfig {
  bilibili_live?: BiliBiliLiveConfig;
}

export interface LlamaCppConfig {
  interrupt_method?: "system" | "user";
  model_path: string;
}

export interface LmStudioConfig {
  interrupt_method?: "system" | "user";
  base_url?: string;
  llm_api_key?: string;
  model: string;
  organization_id?: string | null;
  project_id?: string | null;
  temperature?: number;
}

export interface MeloTTSConfig {
  speaker: string;
  language: string;
  device?: string;
  speed?: number;
}

export interface Mem0Config {
  vector_store: Mem0VectorStoreConfig;
  llm: Mem0LLMConfig;
  embedder: Mem0EmbedderConfig;
}

export interface Mem0EmbedderConfig {
  provider: string;
  config: {
    [key: string]: unknown;
  };
}

export interface Mem0LLMConfig {
  provider: string;
  config: {
    [key: string]: unknown;
  };
}

export interface Mem0VectorStoreConfig {
  provider: string;
  config: {
    [key: string]: unknown;
  };
}

export interface MinimaxTTSConfig {
  group_id: string;
  api_key: string;
  model?: string;
  voice_id?: string;
  pronunciation_dict?: string;
}

export interface MistralConfig {
  interrupt_method?: "system" | "user";
  base_url?: string;
  llm_api_key: string;
  model: string;
  organization_id?: string | null;
  project_id?: string | null;
  temperature?: number;
}

export interface OllamaConfig {
  interrupt_method?: "system" | "user";
  base_url: string;
  llm_api_key?: string;
  model: string;
  organization_id?: string | null;
  project_id?: string | null;
  temperature?: number;
  keep_alive?: number;
  unload_at_exit?: boolean;
}

export interface OpenAICompatibleConfig {
  interrupt_method?: "system" | "user";
  base_url: string;
  llm_api_key: string;
  model: string;
  organization_id?: string | null;
  project_id?: string | null;
  temperature?: number;
}

export interface OpenAIConfig {
  interrupt_method?: "system" | "user";
  base_url?: string;
  llm_api_key: string;
  model: string;
  organization_id?: string | null;
  project_id?: string | null;
  temperature?: number;
}

export interface OpenAITTSConfig {
  model?: string | null;
  voice?: string | null;
  api_key?: string | null;
  base_url?: string | null;
  file_extension?: "mp3" | "wav";
}

export interface PiperTTSConfig {
  model_path?: string;
  speaker_id?: number;
  length_scale?: number;
  noise_scale?: number;
  noise_w?: number;
  volume?: number;
  normalize_audio?: boolean;
  use_cuda?: boolean;
}

export interface SherpaOnnxASRConfig {
  model_type: "transducer" | "paraformer" | "nemo_ctc" | "wenet_ctc" | "whisper" | "tdnn_ctc" | "sense_voice" | "fire_red_asr";
  encoder?: string | null;
  decoder?: string | null;
  joiner?: string | null;
  paraformer?: string | null;
  nemo_ctc?: string | null;
  wenet_ctc?: string | null;
  tdnn_model?: string | null;
  whisper_encoder?: string | null;
  whisper_decoder?: string | null;
  sense_voice?: string | null;
  fire_red_asr_encoder?: string | null;
  fire_red_asr_decoder?: string | null;
  tokens: string;
  num_threads?: number;
  use_itn?: boolean;
  language?: "auto" | "zh" | "en" | "ja" | "ko" | "yue";
  provider?: "cpu" | "cuda" | "rocm";
}

export interface SherpaOnnxTTSConfig {
  vits_model: string;
  vits_lexicon?: string | null;
  vits_tokens: string;
  vits_data_dir?: string | null;
  vits_dict_dir?: string | null;
  tts_rule_fsts?: string | null;
  max_num_sentences?: number;
  sid?: number;
  provider?: "cpu" | "cuda" | "coreml";
  num_threads?: number;
  speed?: number;
  debug?: boolean;
}

export interface SileroVADConfig {
  orig_sr: number;
  target_sr: number;
  prob_threshold: number;
  db_threshold: number;
  required_hits: number;
  required_misses: number;
  smoothing_window: number;
}

export interface SiliconFlowTTSConfig {
  api_url?: string;
  api_key: string;
  default_model?: string;
  default_voice?: string;
  sample_rate?: number;
  response_format?: string;
  stream?: boolean;
  speed?: number;
  gain?: number;
}

export interface SparkTTSConfig {
  api_url: string;
  prompt_wav_upload: string;
  api_name: string;
  gender: string;
  pitch: number;
  speed: number;
}

export interface StatelessLLMConfigs {
  stateless_llm_with_template?: StatelessLLMWithTemplate | null;
  openai_compatible_llm?: OpenAICompatibleConfig | null;
  ollama_llm?: OllamaConfig | null;
  lmstudio_llm?: LmStudioConfig | null;
  openai_llm?: OpenAIConfig | null;
  gemini_llm?: GeminiConfig | null;
  zhipu_llm?: ZhipuConfig | null;
  deepseek_llm?: DeepseekConfig | null;
  groq_llm?: GroqConfig | null;
  claude_llm?: ClaudeConfig | null;
  llama_cpp_llm?: LlamaCppConfig | null;
  mistral_llm?: MistralConfig | null;
}

export interface StatelessLLMWithTemplate {
  interrupt_method?: "system" | "user";
  base_url: string;
  llm_api_key: string;
  model: string;
  organization_id?: string | null;
  project_id?: string | null;
  template?: string | null;
  temperature?: number;
}

export interface SystemConfig {
  conf_version: string;
  host: string;
  port: number;
  config_alts_dir: string;
  tool_prompts: {
    [key: string]: string;
  };
  enable_proxy?: boolean;
  player_language?: string;
  player_prompt?: string;
  default_background?: string;
  mcp_server_enabled?: boolean;
  ui_prefs?: UiPrefs;
}

export interface TTSConfig {
  tts_model: "azure_tts" | "bark_tts" | "edge_tts" | "cosyvoice_tts" | "cosyvoice2_tts" | "melo_tts" | "coqui_tts" | "x_tts" | "gpt_sovits_tts" | "fish_api_tts" | "sherpa_onnx_tts" | "siliconflow_tts" | "openai_tts" | "spark_tts" | "minimax_tts" | "elevenlabs_tts" | "cartesia_tts" | "piper_tts" | "voicevox_tts";
  azure_tts?: AzureTTSConfig | null;
  bark_tts?: BarkTTSConfig | null;
  edge_tts?: EdgeTTSConfig | null;
  cosyvoice_tts?: CosyvoiceTTSConfig | null;
  cosyvoice2_tts?: Cosyvoice2TTSConfig | null;
  melo_tts?: MeloTTSConfig | null;
  coqui_tts?: CoquiTTSConfig | null;
  x_tts?: XTTSConfig | null;
  gpt_sovits?: GPTSoVITSConfig | null;
  fish_api_tts?: FishAPITTSConfig | null;
  sherpa_onnx_tts?: SherpaOnnxTTSConfig | null;
  siliconflow_tts?: SiliconFlowTTSConfig | null;
  openai_tts?: OpenAITTSConfig | null;
  spark_tts?: SparkTTSConfig | null;
  minimax_tts?: MinimaxTTSConfig | null;
  elevenlabs_tts?: ElevenLabsTTSConfig | null;
  cartesia_tts?: CartesiaTTSConfig | null;
  piper_tts?: PiperTTSConfig | null;
  voicevox_tts?: VoiceVoxTTSConfig | null;
}

export interface TTSPreprocessorConfig {
  remove_special_char: boolean;
  ignore_brackets?: boolean;
  ignore_parentheses?: boolean;
  ignore_asterisks?: boolean;
  ignore_angle_brackets?: boolean;
  translator_config: TranslatorConfig;
}

export interface TencentConfig {
  secret_id: string;
  secret_key: string;
  region: string;
  source_lang: string;
  target_lang: string;
}

export interface TranslatorConfig {
  translate_audio: boolean;
  translate_provider: "deeplx" | "tencent" | "llm";
  translate_subtitle?: boolean;
  subtitle_target_lang?: string | null;
  deeplx?: DeepLXConfig | null;
  tencent?: TencentConfig | null;
  llm?: LLMTranslateConfig | null;
}

export interface UiPrefs {
  screen_aware_enabled?: boolean;
  screen_poll_interval_sec?: number;
  proactive_enabled?: boolean;
  proactive_idle_sec?: number;
  auto_speak_on_idle?: boolean;
}

export interface VADConfig {
  vad_model?: string | null;
  silero_vad?: SileroVADConfig | null;
}

export interface VoiceVoxTTSConfig {
  base_url?: string;
  speaker?: number;
}

export interface WhisperCPPConfig {
  model_name: string;
  model_dir: string;
  print_realtime?: boolean;
  print_progress?: boolean;
  language?: string;
  prompt?: string | null;
}

export interface WhisperConfig {
  name: string;
  download_root: string;
  device?: "cpu" | "cuda";
  prompt?: string | null;
}

export interface XTTSConfig {
  api_url: string;
  speaker_wav: string;
  language: string;
}

export interface ZhipuConfig {
  interrupt_method?: "system" | "user";
  base_url?: string;
  llm_api_key: string;
  model: string;
  organization_id?: string | null;
  project_id?: string | null;
  temperature?: number;
}

export interface Config {
  system_config?: SystemConfig;
  character_config: CharacterConfig;
  live_config?: LiveConfig;
}

/**
 * 设置控制台 —— 数据模型与配置项（v2 极简版）
 *
 * 设计说明：
 * - 7 个主导航分区（首页/角色/语音/感知/娱乐/任务/系统），图标 + 二字名。
 * - 卡片带 component → 渲染真实功能组件；无 component → 渲染字段（状态/设置）。
 * - 规划中（planned）功能统一收进首页「实验室」，不与真实功能混排。
 * - 卡片/字段不再携带说明小字（标题即说明），status/source 仅内部保留。
 */

/* ============================== 类型定义 ============================== */

export type ControlSectionId =
  | 'home'
  | 'role'
  | 'voice'
  | 'sense'
  | 'entertainment'
  | 'task'
  | 'system';

export type FeatureStatus = 'live' | 'planned';
export type FeatureSource =
  | 'moonlight'
  | 'open-llm-vtuber'
  | 'neko'
  | 'ai-desktop-pet'
  | 'soullink'
  | 'petgpt'
  | 'mea-pet'
  | 'ai-yinmei'
  | 'zerolan-live-robot'
  | 'so-vits-svc'
  | 'super-agent-party'
  | 'my-neuro';

export type ControlType =
  | 'toggle'
  | 'select'
  | 'text'
  | 'slider'
  | 'button'
  | 'stat'
  | 'progress'
  | 'tags';

/**
 * 真实功能组件挂载键。
 * 卡片带 component 时，渲染层直接挂载对应 React 组件（真实可用）。
 */
export type ControlComponentKey =
  | 'character' // CharacterSettings（角色卡/人设/Live2D/TTS 引擎）
  | 'playerPrompt' // PlayerPromptCard（玩家提示词）
  | 'llm' // LLMSettings（服务商/模型/性能）
  | 'voicePanel' // VoiceSettingsPanel（回复语言/双语气泡/翻译）
  | 'screen' // ScreenAwareSettings（屏幕感知）
  | 'memory' // MemorySettings（核心画像/事实/反思）
  | 'proactive' // ProactiveSettings（主动对话/话题）
  | 'task' // TaskPlatformSettings（任务平台/MCP/沙箱）
  | 'systemInfo' // SystemInfo（连接/版本/诊断）
  | 'emotionDebug' // EmotionDebug（情感调试面板）
  | 'live2dAppearance' // Live2DAppearanceSettings（缩放/位置/透明度，P0）
  | 'expression' // ExpressionSettings（AI 表情引擎，P1）
  | 'emotionMachine' // EmotionStateMachine（情感状态机可视化，P1）
  | 'conversationStateMachine' // ConversationStateMachine（对话三态，P1）
  | 'occlusionEditor' // OcclusionEditor（遮罩与光照，P1.5）
  | 'motionPreview' // MotionPreviewSettings（口型与连续动作预览，P1.5）
  | 'singingRequest' // SingingRequestSettings（点歌学唱，P2）
  | 'singingQueue' // SingingQueueSettings（歌单与队列，P2）
  | 'singingEngine' // SingingEngineSettings（翻唱引擎，P2）
  | 'liveBili' // LiveBiliSettings（B站直播接入，P3）
  | 'liveDanmaku' // LiveDanmakuSettings（弹幕玩法，P3）
  | 'liveObs' // LiveObsSettings（OBS 与表情，P3）
  | 'playmateGame' // PlaymateGameSettings（目标游戏，P4）
  | 'playmateVision' // PlaymateVisionSettings（画面识别，P4）
  | 'playmateKb' // PlaymateKbSettings（攻略知识库，P4）
  | 'playmateCheer' // PlaymateCheerSettings（高光喝彩，P4）
  | 'pluginManager' // PluginManagerSettings（插件管理器，P5）
  | 'skillMarketplace' // SkillMarketplaceSettings（技能市场，P5）
  | 'exportImport' // ExportImportSettings（导出分享，P5）
  | 'intentSettings' // IntentSettings（意图识别，P5）
  | 'multimodalInput' // MultimodalInputSettings（多模态输入，P5）
  | 'skillLibrary' // SkillLibrarySettings（技能系统，P5）
  | 'qqConnector'; // QqConnectorSettings（QQ 连接器，P6）

export interface ControlField {
  id: string;
  label: string;
  description?: string;
  type: ControlType;
  status?: FeatureStatus; // 默认继承卡片
  source?: FeatureSource; // 默认继承卡片
  /** toggle */
  value?: boolean;
  /** select */
  options?: string[];
  /** text / select 当前值 */
  textValue?: string;
  /** slider / progress */
  min?: number;
  max?: number;
  step?: number;
  valueNum?: number;
  /** progress 后缀（如 %） */
  suffix?: string;
  /** button 组 */
  actions?: string[];
  /** tags */
  tags?: string[];
  /** stat 色调 */
  tone?: 'ok' | 'warn' | 'danger' | 'neutral';
  /** 附加小徽标 */
  badge?: string;
}

export interface ControlCard {
  id: string;
  title: string;
  icon?: string;
  description?: string;
  status?: FeatureStatus;
  source?: FeatureSource;
  /** 真实功能组件挂载键（有值 → 渲染真实组件，忽略 fields 展位渲染）。 */
  component?: ControlComponentKey;
  fields?: ControlField[];
}

export interface ControlSection {
  id: ControlSectionId;
  label: string;
  icon: string;
  group: string;
  description: string;
  cards: ControlCard[];
}

export interface TopStat {
  id: string;
  label: string;
  value: string;
  tone: 'ok' | 'warn' | 'danger' | 'neutral';
  hint?: string;
}

/* ============================== 来源项目名映射（内部保留） ============================== */

export const SOURCE_NAMES: Record<FeatureSource, string> = {
  moonlight: 'Moonlight',
  'open-llm-vtuber': 'Open-LLM-VTuber',
  neko: 'N.E.K.O',
  'ai-desktop-pet': 'AI-Desktop-Pet',
  soullink: 'SoulLink_Live2D',
  petgpt: 'PetGPT',
  'mea-pet': 'Mea-Pet',
  'ai-yinmei': 'AI-YinMei',
  'zerolan-live-robot': 'ZerolanLiveRobot',
  'so-vits-svc': 'So-VITS-SVC',
  'super-agent-party': 'Super-Agent-Party',
  'my-neuro': 'My-Neuro',
};

/* ============================== 顶部状态条（fallback，实时值由 ControlCenter 注入） ============================== */

export const TOP_STATS: TopStat[] = [
  { id: 'stat-role', label: '角色', value: '小月', tone: 'ok', hint: 'Live2D: hiyori' },
  { id: 'stat-llm', label: 'LLM', value: 'deepseek-v4-flash', tone: 'ok', hint: '在线' },
  { id: 'stat-voicevox', label: 'VOICEVOX', value: '未连接', tone: 'neutral', hint: '等待后端状态' },
  { id: 'stat-deeplx', label: 'DeepLX', value: '未连接', tone: 'neutral', hint: '等待后端状态' },
  { id: 'stat-memory', label: '记忆', value: '128 条', tone: 'ok', hint: '画像 3 · 事实 96 · 反思 29' },
  { id: 'stat-emotion', label: '情感', value: '开心', tone: 'neutral', hint: '好感度 Lv.7' },
];

/* ============================== 全部分区（v2 极简：7 个） ============================== */

export const CONTROL_SECTIONS: ControlSection[] = [
  /* ------------------------------ 首页 ------------------------------ */
  {
    id: 'home',
    label: '首页',
    icon: 'monitor',
    group: '',
    description: '',
    cards: [
      {
        id: 'home-quick',
        title: '你想做什么',
        fields: [
          { id: 'hq-voice', label: '换声音', type: 'button', actions: ['换声音'] },
          { id: 'hq-role', label: '换形象', type: 'button', actions: ['换形象'] },
          { id: 'hq-memory', label: '管理记忆', type: 'button', actions: ['管理记忆'] },
          { id: 'hq-live', label: '打开直播', type: 'button', actions: ['打开直播'] },
          { id: 'hq-sing', label: '点歌唱曲', type: 'button', actions: ['点歌唱曲'] },
          { id: 'hq-task', label: '任务工作区', type: 'button', actions: ['任务工作区'] },
        ],
      },
      {
        id: 'home-lab',
        title: '实验室',
        status: 'planned',
        fields: [
          { id: 'lab-voicebank', label: '音色克隆', type: 'text', textValue: '规划中' },
          { id: 'lab-vault', label: '记忆浏览器', type: 'text', textValue: '规划中' },
          { id: 'lab-dance', label: '伴舞', type: 'text', textValue: '规划中' },
          { id: 'lab-overlay', label: '叠加层', type: 'text', textValue: '规划中' },
          { id: 'lab-automation', label: '自动化', type: 'text', textValue: '规划中' },
        ],
      },
    ],
  },

  /* ------------------------------ 角色 ------------------------------ */
  {
    id: 'role',
    label: '角色',
    icon: 'sparkles',
    group: '',
    description: '',
    cards: [
      { id: 'role-character', title: '角色卡', icon: '🪪', component: 'character' },
      { id: 'role-player', title: '玩家提示词', icon: '🧑', component: 'playerPrompt' },
      { id: 'role-live2d', title: 'Live2D 外观', icon: '🖼️', component: 'live2dAppearance' },
      { id: 'role-mask', title: '遮罩与光照', icon: '💡', component: 'occlusionEditor' },
      { id: 'brain-llm', title: '连接与模型', icon: '🔌', component: 'llm' },
      {
        id: 'brain-perf',
        title: '性能预设',
        icon: '⚡',
        fields: [
          { id: 'bp-keepalive', label: 'keep_alive', type: 'toggle', value: false },
          { id: 'bp-segment', label: '分段方法', type: 'select', options: ['pysbd（当前）', 'sentence', 'none'], textValue: 'pysbd（当前）' },
          { id: 'bp-stream', label: '流式输出', type: 'toggle', value: true },
          { id: 'bp-batch', label: '批量优化', type: 'toggle', value: true },
        ],
      },
      { id: 'brain-multimodal', title: '多模态输入', icon: '🖼️', component: 'multimodalInput' },
    ],
  },

  /* ------------------------------ 语音 ------------------------------ */
  {
    id: 'voice',
    label: '语音',
    icon: 'volume',
    group: '',
    description: '',
    cards: [
      { id: 'voice-reply', title: '回复方式', icon: '💬', component: 'voicePanel' },
      {
        id: 'voice-engine',
        title: '语音引擎',
        icon: '🔊',
        fields: [
          { id: 've-tts', label: 'TTS 引擎', type: 'select', options: ['VOICEVOX（日语 · 当前）', 'Edge TTS', 'CosyVoice（SiliconFlow）'], textValue: 'VOICEVOX（日语 · 当前）' },
          { id: 've-asr', label: 'ASR 引擎', type: 'select', options: ['sherpa-onnx（SenseVoice · 当前）', 'faster-whisper', 'whisper.cpp', 'FunASR'], textValue: 'sherpa-onnx（SenseVoice · 当前）' },
          { id: 've-speed', label: '语速', type: 'slider', valueNum: 1.0, min: 0.5, max: 2, step: 0.1, suffix: '×' },
          { id: 've-pitch', label: '音高', type: 'slider', valueNum: 0, min: -12, max: 12, step: 1, suffix: '半音' },
          { id: 've-vol', label: '音量', type: 'slider', valueNum: 90, min: 0, max: 100, step: 1, suffix: '%' },
          { id: 've-voice', label: '音色选择', type: 'select', options: ['小月（默认）', '四国めたん', 'ずんだもん', '春日部つむぎ'], textValue: '小月（默认）' },
        ],
      },
      { id: 'voice-lipsync', title: '口型与动作', icon: '👄', component: 'motionPreview' },
    ],
  },

  /* ------------------------------ 感知 ------------------------------ */
  {
    id: 'sense',
    label: '感知',
    icon: 'eye',
    group: '',
    description: '',
    cards: [
      { id: 'sense-screen', title: '屏幕感知', icon: '🖥️', component: 'screen' },
      {
        id: 'sense-privacy',
        title: '隐私排除',
        icon: '🛡️',
        fields: [
          { id: 'sp-blacklist', label: '应用黑名单', type: 'tags', tags: ['密码管理器', '银行', '网银', '付款'] },
          { id: 'sp-keywords', label: '标题关键词', type: 'tags', tags: ['password', '支付', '密码', '验证码', 'token'] },
          { id: 'sp-skip', label: '敏感窗口自动跳过', type: 'toggle', value: true },
          { id: 'sp-local', label: '仅本机处理', type: 'toggle', value: false },
        ],
      },
      { id: 'sense-memory', title: '记忆', icon: '🧩', component: 'memory' },
      {
        id: 'sense-history',
        title: '屏幕回看',
        icon: '📸',
        fields: [
          { id: 'sh-recent', label: '最近识别', type: 'stat', tone: 'neutral', textValue: '—' },
          { id: 'sh-cost', label: '估算成本', type: 'stat', tone: 'neutral', textValue: '—' },
          { id: 'sh-feedback', label: '识别反馈', type: 'button', actions: ['查看反馈'] },
          { id: 'sh-clear', label: '记录管理', type: 'button', actions: ['清空识别上下文'] },
        ],
      },
      { id: 'emotion-state', title: '当前情感', icon: '🌡️', component: 'emotionDebug' },
      { id: 'emotion-ai', title: 'AI 表情', icon: '🎨', component: 'expression' },
      { id: 'emotion-machine', title: '情感状态机', icon: '🤖', component: 'emotionMachine' },
      { id: 'proactive-main', title: '主动对话', icon: '🔔', component: 'proactive' },
      {
        id: 'proactive-scan',
        title: '屏幕巡检',
        icon: '🔄',
        fields: [
          { id: 'ps-enabled', label: '屏幕巡检', type: 'toggle', value: true },
          { id: 'ps-interval', label: '巡检周期', type: 'slider', valueNum: 60, min: 10, max: 600, step: 10, suffix: 's' },
          { id: 'ps-sensitivity', label: '搭话敏感度', type: 'slider', valueNum: 50, min: 0, max: 100, step: 1, suffix: '%' },
          { id: 'ps-strategy', label: '决策策略', type: 'select', options: ['light_chat（轻聊 · 当前）', 'interrupt（插话）', 'silence（只看不说）'], textValue: 'light_chat（轻聊 · 当前）' },
        ],
      },
      {
        id: 'proactive-topics',
        title: '话题来源',
        icon: '🗞️',
        fields: [
          { id: 'pt-list', label: '话题列表', type: 'tags', tags: ['屏幕变化', '时间问候', 'AI 资讯', '天气', '随机'] },
          { id: 'pt-news', label: '自动更新', type: 'toggle', value: false },
          { id: 'pt-manage', label: '话题管理', type: 'button', actions: ['管理话题源'] },
        ],
      },
      { id: 'proactive-sm', title: '对话状态机', icon: '🔄', component: 'conversationStateMachine' },
      { id: 'brain-intent', title: '意图识别', icon: '🎯', component: 'intentSettings' },
    ],
  },

  /* ------------------------------ 娱乐 ------------------------------ */
  {
    id: 'entertainment',
    label: '娱乐',
    icon: 'music',
    group: '',
    description: '',
    cards: [
      { id: 'singing-request', title: '点歌学唱', icon: '🎵', component: 'singingRequest' },
      { id: 'singing-engine', title: '翻唱引擎', icon: '🎼', component: 'singingEngine' },
      { id: 'singing-queue', title: '歌单队列', icon: '📋', component: 'singingQueue' },
      { id: 'live-bili', title: 'B站直播', icon: '📺', component: 'liveBili' },
      { id: 'live-danmaku', title: '弹幕玩法', icon: '🎮', component: 'liveDanmaku' },
      { id: 'live-obs', title: 'OBS 与表情', icon: '🎬', component: 'liveObs' },
      { id: 'playmate-game', title: '目标游戏', icon: '🕹️', component: 'playmateGame' },
      { id: 'playmate-vision', title: '画面识别', icon: '👀', component: 'playmateVision' },
      { id: 'playmate-kb', title: '攻略知识', icon: '📚', component: 'playmateKb' },
      { id: 'playmate-action', title: '高光喝彩', icon: '🎉', component: 'playmateCheer' },
    ],
  },

  /* ------------------------------ 任务 ------------------------------ */
  {
    id: 'task',
    label: '任务',
    icon: 'tool',
    group: '',
    description: '',
    cards: [
      { id: 'task-platform', title: '任务平台', icon: '🗂️', component: 'task' },
      {
        id: 'task-tools',
        title: '联网搜索',
        icon: '🌐',
        fields: [
          { id: 'tt-search', label: '网页搜索', type: 'toggle', value: true },
          { id: 'tt-provider', label: '搜索服务', type: 'select', options: ['DuckDuckGo', 'Tavily', 'Brave'], textValue: 'DuckDuckGo' },
          { id: 'tt-web', label: '网页抓取', type: 'toggle', value: true },
        ],
      },
      {
        id: 'task-sandbox',
        title: '沙箱',
        icon: '🧱',
        fields: [
          { id: 'ts-timeout', label: '命令超时', type: 'slider', valueNum: 120, min: 10, max: 600, step: 10, suffix: 's' },
          { id: 'ts-output', label: '输出上限', type: 'slider', valueNum: 8000, min: 1000, max: 50000, step: 1000, suffix: '字符' },
          { id: 'ts-fs', label: '文件读写限制', type: 'toggle', value: true },
          { id: 'ts-destruct', label: '危险命令拦截', type: 'toggle', value: true },
        ],
      },
      {
        id: 'task-mcp',
        title: 'MCP 服务',
        icon: '🔌',
        fields: [
          { id: 'tm-list', label: '已配置服务', type: 'stat', tone: 'warn', textValue: 'time · ddg-search', badge: '0 个可用' },
          { id: 'tm-add', label: '添加服务器', type: 'button', actions: ['添加 stdio', '添加 SSE'] },
          { id: 'tm-test', label: '连接测试', type: 'button', actions: ['测试全部连接'] },
        ],
      },
      { id: 'task-skills', title: '技能', icon: '🧩', component: 'skillLibrary' },
      { id: 'plugin-manager', title: '插件', icon: '📦', component: 'pluginManager' },
      { id: 'plugin-skills', title: '技能市场', icon: '🛍️', component: 'skillMarketplace' },
      { id: 'plugin-qq', title: 'QQ 连接', icon: '💬', component: 'qqConnector' },
      { id: 'plugin-export', title: '导出分享', icon: '📤', component: 'exportImport' },
    ],
  },

  /* ------------------------------ 系统 ------------------------------ */
  {
    id: 'system',
    label: '系统',
    icon: 'cpu',
    group: '',
    description: '',
    cards: [
      { id: 'system-conn', title: '连接状态', icon: '🔗', component: 'systemInfo' },
      {
        id: 'system-perf',
        title: '性能',
        icon: '📊',
        fields: [
          { id: 'sp-cpu', label: 'CPU 占用', type: 'progress', valueNum: 18, max: 100, suffix: '%', tone: 'ok' },
          { id: 'sp-mem', label: '内存占用', type: 'progress', valueNum: 42, max: 100, suffix: '%', tone: 'ok' },
          { id: 'sp-gpu', label: 'GPU 使用', type: 'progress', valueNum: 0, max: 100, suffix: '%', tone: 'neutral' },
          { id: 'sp-latency', label: '对话延迟', type: 'stat', tone: 'ok', textValue: '≈ 1.8s' },
          { id: 'sp-mode', label: '性能档位', type: 'select', options: ['均衡（当前）', '高性能', '省电'], textValue: '均衡（当前）' },
        ],
      },
      {
        id: 'system-errors',
        title: '最近错误',
        icon: '🚨',
        fields: [
          { id: 'se-count', label: '未处理错误', type: 'stat', tone: 'warn', textValue: '2 条' },
          { id: 'se-mcp', label: 'MCP 连接失败', type: 'text', textValue: '不影响主链路', badge: '已知' },
          { id: 'se-actions', label: '错误处理', type: 'button', actions: ['查看详情', '清除已处理'] },
        ],
      },
      {
        id: 'system-privacy',
        title: '隐私数据',
        icon: '🔐',
        fields: [
          { id: 'sy-data-dir', label: '数据目录', type: 'text', textValue: '~/AppData/Roaming/moonlight' },
          { id: 'sy-log', label: '日志等级', type: 'select', options: ['INFO（当前）', 'DEBUG', 'WARNING', 'ERROR'], textValue: 'INFO（当前）' },
          { id: 'sy-telemetry', label: '匿名统计', type: 'toggle', value: false },
          { id: 'sy-backup', label: '备份与恢复', type: 'button', actions: ['立即备份', '从备份恢复'] },
        ],
      },
      {
        id: 'system-dev',
        title: '开发者',
        icon: '🧑‍💻',
        fields: [
          { id: 'sd-version', label: '版本', type: 'stat', tone: 'neutral', textValue: 'v0.12.0 · 2026-08' },
          { id: 'sd-stack', label: '技术栈', type: 'tags', tags: ['FastAPI', 'React', 'Electron', 'Live2D', 'LangGraph', 'MCP'] },
          { id: 'sd-debug', label: '开发者模式', type: 'toggle', value: false },
          { id: 'sd-tools', label: '调试工具', type: 'button', actions: ['情感调试', 'WS 事件流', '健康检查'] },
        ],
      },
    ],
  },
];

/* ============================== 辅助 ============================== */

export const SECTION_GROUPS = [...new Set(CONTROL_SECTIONS.map((s) => s.group))];

export function findSection(id: string): ControlSection | undefined {
  return CONTROL_SECTIONS.find((s) => s.id === id);
}

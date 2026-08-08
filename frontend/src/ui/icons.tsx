/**
 * icons.tsx — Moonlight 内联线性 SVG 图标库。
 *
 * 设计约定：
 * - 24x24 viewBox，1.75 描边（stroke-width），round 端点/连接，与暗夜月光玻璃风格匹配；
 * - 颜色继承 currentColor，跟随父级文字颜色；
 * - 不新增任何 npm 依赖。
 *
 * 用法：<Icon name="sparkles" /> 或 <Sparkles />，可传 className / size。
 */
import type { ReactElement, SVGProps } from 'react';

export interface IconProps extends SVGProps<SVGSVGElement> {
  /** 图标名称（见 ICON_PATHS）。 */
  name: IconName;
  /** 像素尺寸，默认 20。 */
  size?: number;
}

type IconName =
  | 'sparkles' // 角色
  | 'message' // 对话
  | 'volume' // 声音
  | 'eye' // 感知
  | 'palette' // 外观
  | 'cpu' // 系统
  | 'brain' // LLM
  | 'database' // 记忆
  | 'send' // 发送
  | 'mic' // 录音
  | 'micOff' // 停止录音
  | 'stop' // 打断
  | 'settings' // 设置
  | 'switch' // 切换模式
  | 'plus' // 新建
  | 'trash' // 删除
  | 'history' // 历史
  | 'x' // 关闭
  | 'chevronDown' // 更多
  | 'panelLeftOpen' // 展开会话侧边栏
  | 'panelLeftClose' // 收起会话侧边栏
  | 'check' // 完成
  | 'moon' // 深色
  | 'sun' // 浅色
  | 'wifi' // 连接
  | 'heart' // 好感
  | 'face' // 情绪
  | 'languages' // 翻译
  | 'tool' // 工具 MCP
  | 'info' // 系统信息
  | 'zap' // 主动
  | 'monitor' // 屏幕感知
  | 'user' // 玩家
  | 'edit' // 编辑
  | 'search' // 搜索
  | 'refresh' // 刷新
  | 'play' // 试听
  | 'pause'
  | 'alert' // 错误
  ;

export type { IconName };

/** 统一的描边参数，保证整套图标视觉一致。 */
const STROKE = {
  fill: 'none',
  stroke: 'currentColor',
  strokeWidth: 1.75,
  strokeLinecap: 'round' as const,
  strokeLinejoin: 'round' as const,
};

const ICON_PATHS: Record<IconName, ReactElement> = {
  // —— 设置分区 ——
  sparkles: (
    <>
      <path d="M12 3l1.9 5.1L19 10l-5.1 1.9L12 17l-1.9-5.1L5 10l5.1-1.9L12 3z" />
      <path d="M19 16l.8 2.2L22 19l-2.2.8L19 22l-.8-2.2L16 19l2.2-.8L19 16z" />
      <path d="M5 16l.7 1.8L7.5 18.5l-1.8.7L5 21l-.7-1.8L2.5 18.5l1.8-.7L5 16z" />
    </>
  ),
  message: (
    <>
      <path d="M21 12a8 8 0 0 1-8 8H5.5L3 22l.6-4A8 8 0 1 1 21 12z" />
      <path d="M8.5 10.5h7M8.5 14h4.5" />
    </>
  ),
  volume: (
    <>
      <path d="M11 5L6.5 8.5H3v7h3.5L11 19V5z" />
      <path d="M15 9a4.5 4.5 0 0 1 0 6M17.5 6.5a8 8 0 0 1 0 11" />
    </>
  ),
  eye: (
    <>
      <path d="M2 12s3.5-6.5 10-6.5S22 12 22 12s-3.5 6.5-10 6.5S2 12 2 12z" />
      <circle cx="12" cy="12" r="2.8" />
    </>
  ),
  palette: (
    <>
      <path d="M12 3a9 9 0 0 0 0 18c1.2 0 2-.8 2-1.8 0-.5-.2-.9-.5-1.2-.3-.3-.5-.7-.5-1.2 0-1 .8-1.8 1.8-1.8H17a4 4 0 0 0 4-4c0-4.4-4-8-9-8z" />
      <circle cx="7.5" cy="10.5" r="1" fill="currentColor" stroke="none" />
      <circle cx="12" cy="7.5" r="1" fill="currentColor" stroke="none" />
      <circle cx="16.5" cy="10.5" r="1" fill="currentColor" stroke="none" />
    </>
  ),
  cpu: (
    <>
      <rect x="6" y="6" width="12" height="12" rx="2" />
      <rect x="9.5" y="9.5" width="5" height="5" rx="1" />
      <path d="M9 3v3M15 3v3M9 18v3M15 18v3M3 9h3M3 15h3M18 9h3M18 15h3" />
    </>
  ),
  brain: (
    <>
      <path d="M9.5 4a2.5 2.5 0 0 0-2.5 2.5A2.5 2.5 0 0 0 5 11.5 2.5 2.5 0 0 0 6.5 16v3.5" />
      <path d="M9.5 4a2.5 2.5 0 0 1 2.5 2.5V21" />
      <path d="M12 6.5V4a2.5 2.5 0 0 1 2.5-2.5c1.5 0 2.5 1.3 2.5 2.8V11" />
      <path d="M17 11a2.5 2.5 0 0 0 0 5 2 2 0 0 1 0 4h-4.5" />
    </>
  ),
  database: (
    <>
      <ellipse cx="12" cy="5.5" rx="7.5" ry="3" />
      <path d="M4.5 5.5v6c0 1.7 3.4 3 7.5 3s7.5-1.3 7.5-3v-6" />
      <path d="M4.5 11.5v6c0 1.7 3.4 3 7.5 3s7.5-1.3 7.5-3v-6" />
    </>
  ),
  // —— 聊天 / 交互 ——
  send: (
    <>
      <path d="M21 3L10.5 13.5" />
      <path d="M21 3l-6.5 18-4-7.5L3 9.5 21 3z" />
    </>
  ),
  mic: (
    <>
      <rect x="9" y="3" width="6" height="11" rx="3" />
      <path d="M5.5 11a6.5 6.5 0 0 0 13 0M12 17.5V21M8.5 21h7" />
    </>
  ),
  micOff: (
    <>
      <path d="M9 9v2a3 3 0 0 0 5.1 2.1M15 10.5V6a3 3 0 0 0-5.5-1.8" />
      <path d="M5.5 11a6.5 6.5 0 0 0 9.6 5.6M12 17.5V21M8.5 21h7" />
      <path d="M3 3l18 18" />
    </>
  ),
  stop: (
    <>
      <circle cx="12" cy="12" r="9" />
      <rect x="9" y="9" width="6" height="6" rx="1" fill="currentColor" stroke="none" />
    </>
  ),
  settings: (
    <>
      <circle cx="12" cy="12" r="3.2" />
      <path d="M19.4 15a1.7 1.7 0 0 0 .34 1.87l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.7 1.7 0 0 0-1.87-.34 1.7 1.7 0 0 0-1.03 1.56V21a2 2 0 1 1-4 0v-.09a1.7 1.7 0 0 0-1.11-1.56 1.7 1.7 0 0 0-1.87.34l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.7 1.7 0 0 0 .34-1.87 1.7 1.7 0 0 0-1.56-1.03H3a2 2 0 1 1 0-4h.09a1.7 1.7 0 0 0 1.56-1.11 1.7 1.7 0 0 0-.34-1.87l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.7 1.7 0 0 0 1.87.34h.08a1.7 1.7 0 0 0 1.03-1.56V3a2 2 0 1 1 4 0v.09c0 .68.4 1.29 1.03 1.56a1.7 1.7 0 0 0 1.87-.34l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.7 1.7 0 0 0-.34 1.87v.08c.27.63.88 1.03 1.56 1.03H21a2 2 0 1 1 0 4h-.09a1.7 1.7 0 0 0-1.51 1.03z" />
    </>
  ),
  switch: (
    <>
      <path d="M8 3L4 7l4 4" />
      <path d="M4 7h13a3 3 0 0 1 3 3v1" />
      <path d="M16 21l4-4-4-4" />
      <path d="M20 17H7a3 3 0 0 1-3-3v-1" />
    </>
  ),
  plus: (
    <>
      <path d="M12 5v14M5 12h14" />
    </>
  ),
  trash: (
    <>
      <path d="M4 7h16M9.5 7V5.5A1.5 1.5 0 0 1 11 4h2a1.5 1.5 0 0 1 1.5 1.5V7" />
      <path d="M6.5 7l.8 12.2A1.8 1.8 0 0 0 9.1 21h5.8a1.8 1.8 0 0 0 1.8-1.8L17.5 7" />
      <path d="M10 11v6M14 11v6" />
    </>
  ),
  history: (
    <>
      <path d="M3 12a9 9 0 1 0 3-6.7L3 8" />
      <path d="M3 4v4h4" />
      <path d="M12 8v4.5l3 1.8" />
    </>
  ),
  x: (
    <>
      <path d="M6 6l12 12M18 6L6 18" />
    </>
  ),
  chevronDown: (
    <>
      <path d="M6 9l6 6 6-6" />
    </>
  ),
  panelLeftOpen: (
    <>
      <rect x="3" y="4" width="18" height="16" rx="2" />
      <path d="M9.5 4v16" />
      <path d="M14 9l3 3-3 3" />
    </>
  ),
  panelLeftClose: (
    <>
      <rect x="3" y="4" width="18" height="16" rx="2" />
      <path d="M9.5 4v16" />
      <path d="M14 9l-3 3 3 3" />
    </>
  ),
  check: (
    <>
      <path d="M5 12.5l4.5 4.5L19 7" />
    </>
  ),
  // —— 主题 ——
  moon: (
    <>
      <path d="M20.5 14.5A8.5 8.5 0 1 1 9.5 3.5a7 7 0 0 0 11 11z" />
    </>
  ),
  sun: (
    <>
      <circle cx="12" cy="12" r="4" />
      <path d="M12 2.5v2M12 19.5v2M4.5 4.5l1.4 1.4M18.1 18.1l1.4 1.4M2.5 12h2M19.5 12h2M4.5 19.5l1.4-1.4M18.1 5.9l1.4-1.4" />
    </>
  ),
  // —— 状态 / 杂项 ——
  wifi: (
    <>
      <path d="M2.5 9a15.5 15.5 0 0 1 19 0M5.5 12.5a10.5 10.5 0 0 1 13 0M8.5 16a5.5 5.5 0 0 1 7 0" />
      <circle cx="12" cy="19" r="1" fill="currentColor" stroke="none" />
    </>
  ),
  heart: (
    <>
      <path d="M12 20.5s-7.5-4.6-9.3-9.3C1.5 8 3.7 5 6.8 5c2 0 3.6 1.1 4.4 2.7h1.6C13.6 6.1 15.2 5 17.2 5c3.1 0 5.3 3 4.1 6.2-1.8 4.7-9.3 9.3-9.3 9.3z" />
    </>
  ),
  face: (
    <>
      <circle cx="12" cy="12" r="9" />
      <path d="M8.5 10h.01M15.5 10h.01" />
      <path d="M9 14.5c1 .9 2 1.3 3 1.3s2-.4 3-1.3" />
    </>
  ),
  languages: (
    <>
      <circle cx="12" cy="12" r="9" />
      <path d="M3.5 12h17M12 3a14 14 0 0 1 0 18M12 3a14 14 0 0 0 0 18" />
    </>
  ),
  tool: (
    <>
      <path d="M14.7 6.3a4.5 4.5 0 0 0-6 5.6L3 17.6V21h3.4l5.7-5.7a4.5 4.5 0 0 0 5.6-6L14 13l-3-3 3.7-3.7z" />
    </>
  ),
  info: (
    <>
      <circle cx="12" cy="12" r="9" />
      <path d="M12 11v5M12 7.5h.01" />
    </>
  ),
  zap: (
    <>
      <path d="M13 2L4.5 13.5H11L9.5 22 19 10.5h-6.5L13 2z" />
    </>
  ),
  monitor: (
    <>
      <rect x="2.5" y="4" width="19" height="13" rx="2" />
      <path d="M8 21h8M12 17v4" />
    </>
  ),
  user: (
    <>
      <circle cx="12" cy="8" r="4" />
      <path d="M4 21c.8-4 4.2-6 8-6s7.2 2 8 6" />
    </>
  ),
  edit: (
    <>
      <path d="M4 20h4L20 8l-4-4L4 16v4z" />
      <path d="M13.5 6.5l4 4" />
    </>
  ),
  search: (
    <>
      <circle cx="11" cy="11" r="7" />
      <path d="M20.5 20.5L16 16" />
    </>
  ),
  refresh: (
    <>
      <path d="M20.5 12a8.5 8.5 0 1 1-2.5-6" />
      <path d="M20.5 3.5V9H15" />
    </>
  ),
  play: (
    <>
      <circle cx="12" cy="12" r="9" />
      <path d="M10 8.5l6 3.5-6 3.5v-7z" fill="currentColor" stroke="none" />
    </>
  ),
  pause: (
    <>
      <circle cx="12" cy="12" r="9" />
      <path d="M10 9.5v5M14 9.5v5" />
    </>
  ),
  alert: (
    <>
      <path d="M10.3 3.9L1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z" />
      <path d="M12 9v4.5M12 17h.01" />
    </>
  ),
};

/**

/**
 * 通用图标组件：<Icon name="settings" size={18} />
 */
export function Icon({ name, size = 20, ...rest }: IconProps): ReactElement {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      aria-hidden="true"
      focusable="false"
      {...STROKE}
      {...rest}
    >
      {ICON_PATHS[name]}
    </svg>
  );
}

/** 具名导出，可直接用 <SettingsIcon />（省一层 name 参数）。 */
export const SparklesIcon = (p: Omit<IconProps, 'name'>): ReactElement => <Icon name="sparkles" {...p} />;
export const MessageIcon = (p: Omit<IconProps, 'name'>): ReactElement => <Icon name="message" {...p} />;
export const VolumeIcon = (p: Omit<IconProps, 'name'>): ReactElement => <Icon name="volume" {...p} />;
export const EyeIcon = (p: Omit<IconProps, 'name'>): ReactElement => <Icon name="eye" {...p} />;
export const PaletteIcon = (p: Omit<IconProps, 'name'>): ReactElement => <Icon name="palette" {...p} />;
export const CpuIcon = (p: Omit<IconProps, 'name'>): ReactElement => <Icon name="cpu" {...p} />;
export const BrainIcon = (p: Omit<IconProps, 'name'>): ReactElement => <Icon name="brain" {...p} />;
export const DatabaseIcon = (p: Omit<IconProps, 'name'>): ReactElement => <Icon name="database" {...p} />;
export const SendIcon = (p: Omit<IconProps, 'name'>): ReactElement => <Icon name="send" {...p} />;
export const MicIcon = (p: Omit<IconProps, 'name'>): ReactElement => <Icon name="mic" {...p} />;
export const MicOffIcon = (p: Omit<IconProps, 'name'>): ReactElement => <Icon name="micOff" {...p} />;
export const StopIcon = (p: Omit<IconProps, 'name'>): ReactElement => <Icon name="stop" {...p} />;
export const SettingsIcon = (p: Omit<IconProps, 'name'>): ReactElement => <Icon name="settings" {...p} />;
export const SwitchIcon = (p: Omit<IconProps, 'name'>): ReactElement => <Icon name="switch" {...p} />;
export const PlusIcon = (p: Omit<IconProps, 'name'>): ReactElement => <Icon name="plus" {...p} />;
export const TrashIcon = (p: Omit<IconProps, 'name'>): ReactElement => <Icon name="trash" {...p} />;
export const HistoryIcon = (p: Omit<IconProps, 'name'>): ReactElement => <Icon name="history" {...p} />;
export const XIcon = (p: Omit<IconProps, 'name'>): ReactElement => <Icon name="x" {...p} />;
export const ChevronDownIcon = (p: Omit<IconProps, 'name'>): ReactElement => <Icon name="chevronDown" {...p} />;
export const CheckIcon = (p: Omit<IconProps, 'name'>): ReactElement => <Icon name="check" {...p} />;
export const MoonIcon = (p: Omit<IconProps, 'name'>): ReactElement => <Icon name="moon" {...p} />;
export const SunIcon = (p: Omit<IconProps, 'name'>): ReactElement => <Icon name="sun" {...p} />;
export const WifiIcon = (p: Omit<IconProps, 'name'>): ReactElement => <Icon name="wifi" {...p} />;
export const HeartIcon = (p: Omit<IconProps, 'name'>): ReactElement => <Icon name="heart" {...p} />;
export const FaceIcon = (p: Omit<IconProps, 'name'>): ReactElement => <Icon name="face" {...p} />;
export const LanguagesIcon = (p: Omit<IconProps, 'name'>): ReactElement => <Icon name="languages" {...p} />;
export const ToolIcon = (p: Omit<IconProps, 'name'>): ReactElement => <Icon name="tool" {...p} />;
export const InfoIcon = (p: Omit<IconProps, 'name'>): ReactElement => <Icon name="info" {...p} />;
export const ZapIcon = (p: Omit<IconProps, 'name'>): ReactElement => <Icon name="zap" {...p} />;
export const MonitorIcon = (p: Omit<IconProps, 'name'>): ReactElement => <Icon name="monitor" {...p} />;
export const UserIcon = (p: Omit<IconProps, 'name'>): ReactElement => <Icon name="user" {...p} />;
export const EditIcon = (p: Omit<IconProps, 'name'>): ReactElement => <Icon name="edit" {...p} />;
export const SearchIcon = (p: Omit<IconProps, 'name'>): ReactElement => <Icon name="search" {...p} />;
export const RefreshIcon = (p: Omit<IconProps, 'name'>): ReactElement => <Icon name="refresh" {...p} />;
export const PlayIcon = (p: Omit<IconProps, 'name'>): ReactElement => <Icon name="play" {...p} />;
export const PauseIcon = (p: Omit<IconProps, 'name'>): ReactElement => <Icon name="pause" {...p} />;
export const AlertIcon = (p: Omit<IconProps, 'name'>): ReactElement => <Icon name="alert" {...p} />;

/**
 * icons.tsx — Moonlight 内联线性 SVG 图标库（React 渲染壳）。
 *
 * 设计约定：
 * - 24x24 viewBox，1.75 描边（stroke-width），round 端点/连接，与暗夜月光玻璃风格匹配；
 * - 颜色继承 currentColor，跟随父级文字颜色；
 * - 不新增任何 npm 依赖。
 *
 * 图标数据（SVG path 字符串）在 ./iconPaths.ts —— 单一事实源，
 * 同时被 frontend/scripts/build-control-preview.mjs 编译进独立预览页。
 *
 * 用法：<Icon name="sparkles" /> 或 <Sparkles />，可传 className / size。
 */
import type { ReactElement, SVGProps } from 'react';
import { ICON_PATHS } from './iconPaths';

export interface IconProps extends SVGProps<SVGSVGElement> {
  /** 图标名称（见 ICON_PATHS / iconPaths.ts）。 */
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
  | 'folder' // 工作目录
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
  // —— 控制台新增 ——
  | 'music' // 唱歌
  | 'broadcast' // 直播
  | 'gamepad' // 陪玩
  | 'puzzle' // 插件
  | 'attach' // P5.1 聊天附件
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
      dangerouslySetInnerHTML={{ __html: ICON_PATHS[name] ?? '' }}
    />
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
export const MusicIcon = (p: Omit<IconProps, 'name'>): ReactElement => <Icon name="music" {...p} />;
export const BroadcastIcon = (p: Omit<IconProps, 'name'>): ReactElement => <Icon name="broadcast" {...p} />;
export const GamepadIcon = (p: Omit<IconProps, 'name'>): ReactElement => <Icon name="gamepad" {...p} />;
export const PuzzleIcon = (p: Omit<IconProps, 'name'>): ReactElement => <Icon name="puzzle" {...p} />;

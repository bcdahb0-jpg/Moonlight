/**
 * FeatureDock — 窗口模式左侧栏顶部的「功能组件区」（P0–P6 快捷入口）。
 *
 * 位置：会话侧栏顶部、工作区上方（用户要求：功能组件上移，工作区下移）。
 * 交互：点击图标 → onOpenSection(sectionId) 打开控制台对应分区（复用
 * ControlCenter 全屏弹层，不复制设置面板，单一事实源）。
 *
 * 数据驱动：FEATURES 数组 = 常用功能分区 + 图标 + 标签；
 * 折叠侧栏时随 aside 一起收起（由 ConversationSidebar 以 dock prop 渲染）。
 */
import type { ReactElement } from 'react';
import { Icon, type IconName } from '@/ui/icons';

interface FeatureEntry {
  id: string;
  icon: IconName;
  label: string;
  section: string;
  /** 徽标（可选，如「7 模型」「48 功能」）。 */
  badge?: string;
}

const FEATURES: FeatureEntry[] = [
  { id: 'singing', icon: 'music', label: '唱歌', section: 'entertainment', badge: '点歌学唱' },
  { id: 'live', icon: 'broadcast', label: '直播', section: 'entertainment', badge: '弹幕互动' },
  { id: 'playmate', icon: 'gamepad', label: '陪玩', section: 'entertainment', badge: '游戏搭子' },
  { id: 'plugin', icon: 'puzzle', label: '插件', section: 'task', badge: '技能市场' },
  { id: 'task', icon: 'tool', label: '任务', section: 'task', badge: '智能体' },
  { id: 'overview', icon: 'monitor', label: '全部', section: 'home', badge: '控制台' },
];

export interface FeatureDockProps {
  /** 打开控制台对应分区（section id，见 controlData ControlSectionId）。 */
  onOpenSection?: (section: string) => void;
}

export function FeatureDock({ onOpenSection }: FeatureDockProps): ReactElement {
  return (
    <div className="feature-dock">
      <div className="feature-dock-head">
        <span className="feature-dock-title">
          <Icon name="sparkles" size={13} />
          功能组件
        </span>
      </div>
      <div className="feature-dock-grid">
        {FEATURES.map((f) => (
          <button
            type="button"
            key={f.id}
            className="feature-dock-item"
            onClick={() => onOpenSection?.(f.section)}
            title={f.badge ?? f.label}
          >
            <span className="feature-dock-ic">
              <Icon name={f.icon} size={17} />
            </span>
            <span className="feature-dock-label">{f.label}</span>
            {f.badge && <span className="feature-dock-hint">{f.badge}</span>}
          </button>
        ))}
      </div>
    </div>
  );
}

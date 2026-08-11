import type { Live2DModel } from 'pixi-live2d-display/cubism4';

/**
 * 随机动作与头部微动共享工具（Phase B）。
 *
 * 背景：模型动作组命名差异很大（Talk/talk/TapBody/tap...），且多数模型只有
 * Idle 组。`pickRandomMotion` 仿 daidai-live2d-pet（app.js:211-215）的
 * 「候选组名列表 + 多命名兼容 + 随机抽」思路：先探测模型实际存在哪些动作组，
 * 在候选列表里过滤，抽不到再退化为任意非空组，保证任何模型都不会空手而归。
 */

export type MotionMode = 'idle' | 'speaking';

/** daidai 的候选组名兼容列表（首项为 Cubism 惯例命名）。 */
const GROUP_CANDIDATES: Record<MotionMode, string[]> = {
  speaking: ['Talk', 'talk', 'tap_body', 'Tap', 'tap', 'flick_head'],
  idle: ['Idle', 'idle', 'rest', 'sleepy', 'tap_body', 'Tap', 'tap'],
};

interface MotionGroupLike {
  group: string;
  count: number;
}

interface MotionSettingsLike {
  motions?: unknown;
  getMotionGroups?: () => string[];
}

/**
 * 探测模型实际存在的动作组（兼容 cubism2 settings.motions 数组 /
 * cubism4 settings.motions Record / getMotionGroups()）。
 */
export function getMotionGroups(model: Live2DModel): MotionGroupLike[] {
  const settings = (model.internalModel?.settings ?? undefined) as
    | MotionSettingsLike
    | undefined;
  if (!settings) return [];
  try {
    const motions = settings.motions;
    if (Array.isArray(motions)) {
      // cubism2 风格：[{ group, motions: [...] }]
      return motions
        .filter((m: unknown) => m && typeof m === 'object')
        .map((m: { group?: string; motions?: unknown[] }) => ({
          group: m.group ?? '',
          count: Array.isArray(m.motions) ? m.motions.length : 0,
        }));
    }
    if (motions && typeof motions === 'object') {
      // cubism4 风格：Record<group, MotionSetting[]>
      return Object.entries(motions as Record<string, unknown>).map(
        ([group, list]) => ({
          group,
          count: Array.isArray(list) ? list.length : 0,
        }),
      );
    }
    if (typeof settings.getMotionGroups === 'function') {
      return (settings.getMotionGroups() ?? [])
        .filter((g) => typeof g === 'string' && g.length > 0)
        .map((g) => ({ group: g, count: 1 }));
    }
  } catch {
    // 探测失败返回空，调用方会退化
  }
  return [];
}

/**
 * 从模型现有动作组里随机挑一个动作（多组名兼容）。
 * @returns { group, index }；模型无动作组时返回 null。
 */
export function pickRandomMotion(
  model: Live2DModel | null,
  mode: MotionMode,
): { group: string; index: number } | null {
  if (!model) return null;
  const groups = getMotionGroups(model);
  if (groups.length === 0) return null;

  const wanted = GROUP_CANDIDATES[mode];
  const matched = groups.filter((g) => wanted.includes(g.group) && g.count > 0);
  const pool = matched.length > 0 ? matched : groups.filter((g) => g.count > 0);
  if (pool.length === 0) return null;

  const g = pool[Math.floor(Math.random() * pool.length)];
  const index = g.count > 0 ? Math.floor(Math.random() * g.count) : 0;
  return { group: g.group, index };
}

// --------------------------------------------------------------------- //
// 头部微动（Phase B2）
// --------------------------------------------------------------------- //

/**
 * 说话/待机的头部微动状态：低频呼吸摆动 + 说话时高频小幅摆动/点头。
 * 值单位 = Cubism 角度参数（ParamAngleX/Y/Z）的实际度数。
 */
export class HeadMotion {
  /** 说话微动幅度上限（度）。小幅度叠加在动作上；过大/过频会产生抽动鬼畜感。 */
  private static readonly SPEAK_AMP_MAX = 2.5;

  /** 当前各轴输出（度）。 */
  angleX = 0;
  angleY = 0;
  angleZ = 0;

  private active = false;
  private lastLevel = 0;

  /**
   * 每帧更新。仅在 level > 0（说话）时输出非零微动；
   * idle 微动交给模型动作/引擎（避免双写冲突）。
   */
  update(timeSeconds: number, level: number): void {
    const speaking = level > 0.02;
    this.active = speaking;
    if (!speaking) {
      // 快速衰减到 0，避免闭口瞬间头部突跳
      const decay = 0.25;
      this.angleX += (0 - this.angleX) * decay;
      this.angleY += (0 - this.angleY) * decay;
      this.angleZ += (0 - this.angleZ) * decay;
      this.lastLevel = 0;
      return;
    }
    // 音量低通：避免口型抖动直接传导到头部
    this.lastLevel += (Math.min(1, level) - this.lastLevel) * 0.3;
    const amp = Math.min(HeadMotion.SPEAK_AMP_MAX, this.lastLevel * 6);
    const t = timeSeconds;
    // 三个轴频率错开 + 固定相位差 → 自然无规律感（低频柔和，避免抽动）
    this.angleX = Math.sin(t * 4.1) * amp;
    this.angleZ = Math.sin(t * 3.1 + 1.3) * amp * 0.5;
    this.angleY = Math.sin(t * 1.9 + 0.6) * amp * 0.3; // 轻点头
  }

  /** 是否有正在生效的微动（说话中）。 */
  isActive(): boolean {
    return this.active;
  }
}

/** 引擎输出中的头部参数 id 列表（Soullink idle 时输出 0 会覆盖动作动画）。 */
export const HEAD_PARAM_IDS = ['ParamAngleX', 'ParamAngleY', 'ParamAngleZ'];

/** 判断是否是头部角度参数。 */
export function isHeadParam(id: string): boolean {
  return id === 'ParamAngleX' || id === 'ParamAngleY' || id === 'ParamAngleZ';
}

/** 判断是否是身体角度参数（与头部同类问题：引擎 idle 输出 0 覆盖动作身体动画）。 */
export function isBodyParam(id: string): boolean {
  return id === 'ParamBodyAngleX' || id === 'ParamBodyAngleY' || id === 'ParamBodyAngleZ';
}

import {
  CURRENT_SCHEMA_VERSION,
  deriveNeutralParams,
  deriveParameterSmoothing,
  detectCapabilities,
  type FACSKey,
  type ModelProfile,
  type ParameterMap,
} from '@soullink-emotion/engine';

/**
 * 启发式默认 profile 生成器（Phase 0）。
 *
 * 解决「模型没有 soullink.profile.json 就没有表情」的硬门槛：从 model3.json
 * 的 Parameters/ParameterGroups 按标准 Cubism 参数 ID 匹配，生成一份可用的
 * 默认 ModelProfile（parameterMap + neutralParams + parameterSmoothing），
 * 让任意标准模型都能直接走 Soullink 逐帧引擎。
 *
 * 参考：reference/soullink-emotion-sdk/packages/profile-generator/
 *   - createHeuristicProfile（Live2DProfileAutoGenerator.ts）的规则结构
 *   - STANDARD_PARAM_TABLE（standardParamTable.ts）的标准参数表
 * 本实现只做「标准 ID 精确匹配」（Cubism 官方模型均用标准 ID），不做
 * name-match 模糊匹配——自定义命名参数无法可靠映射 FACS，放弃并让引擎
 * 用默认兜底。
 */

// ------------------------------------------------------------------ //
// 标准 Cubism 参数表（FACS key → 参数规则），抄自 profile-generator
// 的 STANDARD_PARAM_TABLE。mode: set=直接赋值 / subtract=取反
// ------------------------------------------------------------------ //

interface ParamSpec {
  ids: readonly string[];
  mode: 'set' | 'subtract';
  scale: number;
  min: number;
  max: number;
}

const STANDARD_TABLE: ReadonlyArray<readonly [FACSKey, ParamSpec]> = [
  ['eyeOpen', { ids: ['ParamEyeLOpen', 'ParamEyeROpen'], mode: 'set', scale: 1, min: 0, max: 1.2 }],
  ['eyeSmile', { ids: ['ParamEyeLSmile', 'ParamEyeRSmile'], mode: 'set', scale: 1, min: 0, max: 1 }],
  ['gazeX', { ids: ['ParamEyeBallX'], mode: 'set', scale: 1, min: -1, max: 1 }],
  ['gazeY', { ids: ['ParamEyeBallY'], mode: 'set', scale: 1, min: -1, max: 1 }],
  ['headX', { ids: ['ParamAngleX'], mode: 'set', scale: 30, min: -30, max: 30 }],
  ['headY', { ids: ['ParamAngleY'], mode: 'set', scale: 30, min: -30, max: 30 }],
  ['headZ', { ids: ['ParamAngleZ'], mode: 'set', scale: 30, min: -30, max: 30 }],
  ['bodyX', { ids: ['ParamBodyAngleX'], mode: 'set', scale: 12, min: -12, max: 12 }],
  ['bodyY', { ids: ['ParamBodyAngleY'], mode: 'set', scale: 12, min: -12, max: 12 }],
  ['bodyZ', { ids: ['ParamBodyAngleZ'], mode: 'set', scale: 12, min: -12, max: 12 }],
  ['mouthSmile', { ids: ['ParamMouthForm'], mode: 'set', scale: 1, min: -1, max: 1 }],
  ['mouthOpen', { ids: ['ParamMouthOpenY'], mode: 'set', scale: 1, min: 0, max: 1 }],
  ['mouthPucker', { ids: ['ParamMouthPucker'], mode: 'set', scale: 1, min: 0, max: 1 }],
  ['browInnerUp', { ids: ['ParamBrowLY', 'ParamBrowRY'], mode: 'set', scale: 1, min: -1, max: 1 }],
  ['browOuterUp', { ids: ['ParamBrowLAngle', 'ParamBrowRAngle'], mode: 'set', scale: 0.9, min: -1, max: 1 }],
  ['browDown', { ids: ['ParamBrowLForm', 'ParamBrowRForm'], mode: 'set', scale: -0.85, min: -1, max: 1 }],
  ['blush', { ids: ['ParamCheek'], mode: 'set', scale: 1, min: 0, max: 1 }],
  ['breath', { ids: ['ParamBreath'], mode: 'set', scale: 1, min: 0, max: 1 }],
];

/** eyeOpen 派生的减法规则（眨眼 = 1 - eyeOpen）。 */
const DERIVED_SUBTRACT: ReadonlyArray<readonly [FACSKey, FACSKey, number]> = [
  ['eyeBlinkL', 'eyeOpen', 1],
  ['eyeBlinkR', 'eyeOpen', 1],
  ['eyeSquint', 'eyeOpen', 0.22],
];

interface CubismModel3 {
  Parameters?: Array<{ Id: string }>;
  Groups?: Array<{ Target: string; Name: string; Ids: string[] }>;
}

export interface GeneratedProfile {
  profile: ModelProfile;
  /** 是否匹配到眨眼参数（否则引擎无法眨眼，提示降级切换式）。 */
  canBlink: boolean;
}

/**
 * 从 model3.json 的 URL 拉取并生成默认 profile。
 * 失败（fetch 失败 / 解析失败 / 无任何标准参数）返回 null。
 */
export async function generateDefaultProfile(modelUrl: string): Promise<GeneratedProfile | null> {
  let json: CubismModel3;
  try {
    const res = await fetch(modelUrl, { cache: 'no-cache' });
    if (!res.ok) return null;
    json = (await res.json()) as CubismModel3;
  } catch {
    return null;
  }

  const paramIds = new Set(
    (json.Parameters ?? []).map((p) => p.Id).filter((id): id is string => Boolean(id)),
  );
  const blinkGroupIds = new Set(
    (json.Groups ?? [])
      .filter((g) => g.Target === 'Parameter' && g.Name === 'EyeBlink')
      .flatMap((g) => g.Ids)
      .filter((id) => paramIds.has(id)),
  );

  // 标准表匹配：仅收录模型实际存在的参数
  const parameterMap: ParameterMap = {};
  for (const [key, spec] of STANDARD_TABLE) {
    const targets = spec.ids.filter((id) => paramIds.has(id));
    if (!targets.length) continue;
    const rule = spec.mode === 'subtract' || targets.length === 1
      ? { target: targets[0], mode: spec.mode, scale: spec.scale, min: spec.min, max: spec.max }
      : { targets, mode: spec.mode, scale: spec.scale, min: spec.min, max: spec.max };
    parameterMap[key] = rule;
  }

  // 眨眼：优先用模型 EyeBlink 组，否则用标准 ParamEyeLOpen/ROpen
  const eyeOpenSpec = STANDARD_TABLE.find(([k]) => k === 'eyeOpen')![1];
  const eyeTargets = blinkGroupIds.size >= 2
    ? [...blinkGroupIds]
    : eyeOpenSpec.ids.filter((id) => paramIds.has(id));
  if (eyeTargets.length >= 2) {
    parameterMap.eyeOpen = { targets: eyeTargets, mode: 'set', scale: 1, min: 0, max: 1.2 };
    // eyeBlinkL/R、eyeSquint 从 eyeOpen 减法派生
    for (const [derivedKey, sourceKey, factor] of DERIVED_SUBTRACT) {
      const source = parameterMap[sourceKey];
      if (source && 'targets' in source && Array.isArray(source.targets) && source.targets.length) {
        parameterMap[derivedKey] = {
          targets: source.targets as string[],
          mode: 'subtract',
          scale: factor,
          min: 0,
          max: 1.2,
        };
      }
    }
  }

  // mouthFrown 从 mouthForm 减法派生
  const mouthSmile = parameterMap.mouthSmile;
  if (mouthSmile && 'target' in mouthSmile) {
    parameterMap.mouthFrown = {
      target: mouthSmile.target,
      mode: 'subtract',
      scale: 1,
      min: -1,
      max: 1,
    };
  }

  // 一个标准参数都没匹配到：模型参数完全非标，生成无意义
  if (Object.keys(parameterMap).length === 0) return null;

  const profile: ModelProfile = {
    modelId: `auto_${modelUrl.replace(/\W+/g, '_').slice(-32)}`,
    displayName: modelUrl.split('/').pop()?.replace(/\.model3\.json$/i, '') ?? 'auto',
    version: '1.0.0',
    modelPath: modelUrl,
    autoProfile: {
      provider: 'heuristic',
      promptVersion: 'moonlight-auto-v1',
      generatedAt: new Date().toISOString(),
      notes: [
        'Auto-generated from model3.json standard Cubism parameter names.',
        'Prefer a calibrated soullink.profile.json for full emotional fidelity.',
      ],
    },
    schemaVersion: CURRENT_SCHEMA_VERSION,
    capabilities: undefined as never, // 由 detectCapabilities 补全
    parameterMap,
    idleConfig: {},
    neutralParams: deriveNeutralParams({ parameterMap }),
    parameterSmoothing: deriveParameterSmoothing({ parameterMap }),
  };
  // 结构补全（capabilities 由引擎侧派生规则计算）
  const patched = { ...profile, capabilities: detectCapabilities(profile) };
  return { profile: patched, canBlink: Boolean(parameterMap.eyeOpen) };
}

/**
 * 为 Moonlight 的 Live2D 模型生成 Soullink Emotion SDK 的 ModelProfile。
 *
 * 用法（在 frontend/ 下执行）:
 *   node scripts/gen-soullink-profiles.mjs            # 生成所有已登记模型
 *   node scripts/gen-soullink-profiles.mjs --force    # 忽略签名哈希强制重新生成
 *
 * 输出: <模型目录>/soullink.profile.json，由后端静态服务（/live2d-models/...）提供。
 */
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { Live2DProfileAutoGenerator } from '@soullink-emotion/profile-generator';

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..', '..');
const MODELS_BASE_URL = '/live2d-models';

/**
 * 每个目标模型：modelsRoot + modelDir 组合出含 .model3.json 的目录
 * （profile-generator 要求 modelDir 为单层目录名）。
 */
const TARGETS = [
  { modelsRoot: 'backend/live2d-models/mao_pro', modelDir: 'runtime', displayName: 'mao_pro' },
  { modelsRoot: 'backend/live2d-models', modelDir: 'hiyori', displayName: 'hiyori' },
];

const force = process.argv.includes('--force');

for (const target of TARGETS) {
  const generator = new Live2DProfileAutoGenerator({
    modelsRoot: resolve(ROOT, target.modelsRoot),
    modelsBaseUrl: MODELS_BASE_URL,
    useConfiguredOpenAI: false,
  });

  try {
    const result = await generator.ensure({
      modelDir: target.modelDir,
      displayName: target.displayName,
      force,
    });

    const profile = result.profile;
    console.log(
      JSON.stringify(
        {
          model: target.displayName,
          generated: result.generated,
          reason: result.reason,
          provider: result.provider,
          profileUrl: result.profileUrl,
          modelUrl: result.modelUrl,
          mappedFACS: Object.keys(profile.parameterMap ?? {}).length,
          privateEmotions: Object.keys(profile.privateEmotionMap ?? {}).length,
          expressions: profile.nativeAnimations?.expressions?.length ?? 0,
          motions: profile.nativeAnimations?.motions?.length ?? 0,
          notes: result.notes,
        },
        null,
        2,
      ),
    );
  } catch (err) {
    console.error(`[gen] FAILED ${target.displayName}:`, err instanceof Error ? err.message : err);
  }
}

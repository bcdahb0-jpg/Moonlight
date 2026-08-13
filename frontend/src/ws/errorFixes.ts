/**
 * 结构化错误码 → 用户可操作修复指引（Phase 3）。
 * 单一事实源：contracts/error-codes.json（文案以该文件为准，这里只做「修复动作」映射）。
 */
import type { ErrorCode } from '@/types/ws';

export interface ErrorFix {
  /** 一句话说明怎么修。 */
  action: string;
  /** 可选：点击后跳转的控制台分区（ControlSectionId：home/role/voice/sense/entertainment/task/system）。 */
  section?: 'role' | 'voice' | 'sense' | 'task' | 'home';
}

/** code → 修复指引；未列出的 code 用兜底。 */
const FIX_MAP: Partial<Record<ErrorCode, ErrorFix>> = {
  ASR_MODEL_MISSING: {
    action: '语音识别模型未下载（约 1GB）。后端启动时会自动下载，也可以稍后重试。',
  },
  ASR_LOAD_FAILED: {
    action: '语音识别引擎加载失败，请查看后端日志；若持续失败可重启应用。',
    section: 'voice',
  },
  ASR_TRANSCRIBE_FAILED: { action: '语音转写失败，可以改用文字输入。', section: 'voice' },
  LLM_UNREACHABLE: {
    action: '无法连接对话模型：检查网络，或在「角色 → 连接与模型」里重新测试配置。',
    section: 'role',
  },
  LLM_INVALID_KEY: {
    action: 'API Key 无效或未配置：到「角色 → 连接与模型」检查你的 Key。',
    section: 'role',
  },
  LLM_TIMEOUT: { action: '对话模型响应超时：试试换小一点的模型，或稍后再聊。', section: 'role' },
  TTS_FAILED: { action: '语音合成失败：到「语音」检查音色与引擎配置。', section: 'voice' },
  MIC_PERMISSION_DENIED: {
    action: '麦克风权限被拒绝：请在系统设置中允许 Moonlight 使用麦克风后重试。',
  },
  MODEL_NOT_FOUND: {
    action: 'Live2D 模型不存在或路径错误：检查 live2d-models/ 目录。',
  },
  CONFIG_INVALID: {
    action: '配置文件校验失败：可在「系统」里检查最近改动的设置，必要时恢复默认配置。',
    section: 'home',
  },
  MCP_SERVER_FAILED: { action: '工具 / MCP 服务器异常：在「任务 → MCP 服务」里检查。', section: 'task' },
  MEMORY_FAILED: { action: '记忆系统异常：到「感知 → 记忆」查看或重置记忆。', section: 'sense' },
  HISTORY_FAILED: { action: '历史会话操作失败：刷新历史列表重试。' },
  INTERNAL_ERROR: { action: '服务器内部错误：请查看后端日志，或重启应用后再试。' },
  UNKNOWN: { action: '发生未知错误：请查看后端日志。' },
};

export function getErrorFix(code: ErrorCode | null): ErrorFix {
  return FIX_MAP[code ?? 'UNKNOWN'] ?? FIX_MAP.UNKNOWN!;
}

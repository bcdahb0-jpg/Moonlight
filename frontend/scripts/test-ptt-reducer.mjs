/**
 * pttReducer 纯函数单元测试（pet-ptt-workflow Phase 4）。
 *
 * 项目无 vitest/jest，用 esbuild 把 pttReducer.ts 编译为 CJS 后直接 node 断言。
 * 运行：cd frontend && node scripts/test-ptt-reducer.mjs
 */
import { build } from 'esbuild';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';

const require = createRequire(import.meta.url);
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');

const outDir = fs.mkdtempSync(path.join(os.tmpdir(), 'ptt-test-'));
await build({
  entryPoints: [path.join(root, 'src/chat/pttReducer.ts')],
  outfile: path.join(outDir, 'pttReducer.cjs'),
  bundle: true,
  format: 'cjs',
  platform: 'node',
  logLevel: 'silent',
});

const { pttReducer, createInitialPttState, PTT_LONG_PRESS_MS, PTT_MIN_RECORD_MS } = require(
  path.join(outDir, 'pttReducer.cjs'),
);

let passed = 0;
function test(name, fn) {
  fn();
  passed += 1;
  console.log(`  ✓ ${name}`);
}

// ------------------------------------------------------------------ //
// idle：按住说话
// ------------------------------------------------------------------ //
test('idle + POINTER_DOWN(空闲) → recording + start_mic', () => {
  const { state, command } = pttReducer(createInitialPttState(), {
    type: 'POINTER_DOWN',
    now: 1000,
    aiSpeaking: false,
  });
  assert.equal(state.phase, 'recording');
  assert.equal(state.holding, true);
  assert.equal(state.startedAt, 1000);
  assert.deepEqual(command, { kind: 'start_mic' });
});

test('recording + POINTER_UP(时长足够) → idle + stop_and_send(discard=false)', () => {
  const s = { phase: 'recording', holding: true, startedAt: 1000, micError: null };
  const { state, command } = pttReducer(s, { type: 'POINTER_UP', now: 1000 + PTT_MIN_RECORD_MS + 1 });
  assert.equal(state.phase, 'idle');
  assert.equal(state.holding, false);
  assert.deepEqual(command, { kind: 'stop_and_send', discard: false });
});

test('recording + POINTER_UP(时长不足) → idle + stop_and_send(discard=true) 短按丢弃', () => {
  const s = { phase: 'recording', holding: true, startedAt: 1000, micError: null };
  const { state, command } = pttReducer(s, { type: 'POINTER_UP', now: 1000 + PTT_MIN_RECORD_MS - 50 });
  assert.equal(state.phase, 'idle');
  assert.deepEqual(command, { kind: 'stop_and_send', discard: true });
});

test('recording + POINTER_CANCEL → idle + cancel_recording（不发送）', () => {
  const s = { phase: 'recording', holding: true, startedAt: 1000, micError: null };
  const { state, command } = pttReducer(s, { type: 'POINTER_CANCEL' });
  assert.equal(state.phase, 'idle');
  assert.deepEqual(command, { kind: 'cancel_recording' });
});

// ------------------------------------------------------------------ //
// ai_speaking：短按打断 / 长按抢先发言
// ------------------------------------------------------------------ //
test('idle + POINTER_DOWN(AI 说话中) → ai_speaking + holding（等长按判定）', () => {
  const { state, command } = pttReducer(createInitialPttState(), {
    type: 'POINTER_DOWN',
    now: 2000,
    aiSpeaking: true,
  });
  assert.equal(state.phase, 'ai_speaking');
  assert.equal(state.holding, true);
  assert.deepEqual(command, { kind: 'none' });
});

test('ai_speaking + LONG_PRESS → recording + interrupt_then_record（抢先发言）', () => {
  const s = { phase: 'ai_speaking', holding: true, startedAt: null, micError: null };
  const { state, command } = pttReducer(s, { type: 'LONG_PRESS', now: 2000 + PTT_LONG_PRESS_MS });
  assert.equal(state.phase, 'recording');
  assert.equal(state.holding, true);
  assert.equal(state.startedAt, 2000 + PTT_LONG_PRESS_MS);
  assert.deepEqual(command, { kind: 'interrupt_then_record' });
});

test('ai_speaking + POINTER_UP(短按) → idle + interrupt（只打断不录音）', () => {
  const s = { phase: 'ai_speaking', holding: true, startedAt: null, micError: null };
  const { state, command } = pttReducer(s, { type: 'POINTER_UP', now: 2100 });
  assert.equal(state.phase, 'idle');
  assert.deepEqual(command, { kind: 'interrupt' });
});

test('ai_speaking + POINTER_CANCEL → 保持 ai_speaking + 放弃手势（不打断）', () => {
  const s = { phase: 'ai_speaking', holding: true, startedAt: null, micError: null };
  const { state, command } = pttReducer(s, { type: 'POINTER_CANCEL' });
  assert.equal(state.phase, 'ai_speaking');
  assert.equal(state.holding, false);
  assert.deepEqual(command, { kind: 'none' });
});

// ------------------------------------------------------------------ //
// 麦克风异步确认
// ------------------------------------------------------------------ //
test('recording + MIC_STARTED → 保持 recording', () => {
  const s = { phase: 'recording', holding: true, startedAt: 1000, micError: null };
  const { state, command } = pttReducer(s, { type: 'MIC_STARTED', now: 1050 });
  assert.equal(state.phase, 'recording');
  assert.deepEqual(command, { kind: 'none' });
});

test('MIC_START_FAILED → idle + micError（权限拒绝不卡死）', () => {
  const s = { phase: 'recording', holding: true, startedAt: 1000, micError: null };
  const { state, command } = pttReducer(s, {
    type: 'MIC_START_FAILED',
    error: 'Permission denied',
  });
  assert.equal(state.phase, 'idle');
  assert.equal(state.micError, 'Permission denied');
  assert.deepEqual(command, { kind: 'none' });
});

// ------------------------------------------------------------------ //
// AI 状态切换
// ------------------------------------------------------------------ //
test('AI_STATE_CHANGE: idle→ai_speaking / ai_speaking→idle', () => {
  const s1 = pttReducer(createInitialPttState(), {
    type: 'AI_STATE_CHANGE',
    speaking: true,
  }).state;
  assert.equal(s1.phase, 'ai_speaking');

  const s2 = pttReducer(s1, { type: 'AI_STATE_CHANGE', speaking: false }).state;
  assert.equal(s2.phase, 'idle');
});

test('recording 中 AI_STATE_CHANGE 不打断录音', () => {
  const s = { phase: 'recording', holding: true, startedAt: 1000, micError: null };
  const { state } = pttReducer(s, { type: 'AI_STATE_CHANGE', speaking: true });
  assert.equal(state.phase, 'recording');
});

// ------------------------------------------------------------------ //
// 幂等 / 防呆
// ------------------------------------------------------------------ //
test('重复 POINTER_DOWN（holding 已 true）→ none', () => {
  const s = { phase: 'recording', holding: true, startedAt: 1000, micError: null };
  const { state, command } = pttReducer(s, {
    type: 'POINTER_DOWN',
    now: 1100,
    aiSpeaking: false,
  });
  assert.equal(state.phase, 'recording');
  assert.deepEqual(command, { kind: 'none' });
});

test('未按住时 POINTER_UP → 只清 holding，无副作用', () => {
  const { state, command } = pttReducer(createInitialPttState(), {
    type: 'POINTER_UP',
    now: 1000,
  });
  assert.equal(state.phase, 'idle');
  assert.deepEqual(command, { kind: 'none' });
});

test('LONG_PRESS 非 ai_speaking → none（防幽灵 timer）', () => {
  const s = { phase: 'recording', holding: true, startedAt: 1000, micError: null };
  const { state, command } = pttReducer(s, { type: 'LONG_PRESS', now: 2000 });
  assert.equal(state.phase, 'recording');
  assert.deepEqual(command, { kind: 'none' });
});

// ------------------------------------------------------------------ //
// 常量
// ------------------------------------------------------------------ //
test('长按阈值 350ms / 最小时长 250ms（与计划 §3.1 一致）', () => {
  assert.equal(PTT_LONG_PRESS_MS, 350);
  assert.equal(PTT_MIN_RECORD_MS, 250);
});

console.log(`\n✅ pttReducer: ${passed} 个用例全部通过`);
fs.rmSync(outDir, { recursive: true, force: true });

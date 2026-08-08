/**
 * Onboarding — 首次启动五步向导（Phase 3）。
 *
 * 1 欢迎与连接 → 2 就绪度检查 → 3 对话大脑（复用 LLMSettings）→ 4 认识角色 → 5 完成。
 * 完成时写入 localStorage 标记（moonlight.onboarded=1），二次启动直达桌宠。
 * 向导内任何一步失败都不阻塞：可以跳过继续，之后在设置里补齐。
 */
import { useEffect, useMemo, useState, type ReactElement } from 'react';
import { useAppState } from '@/state/AppStateContext';
import { readinessApi, llmConfigApi, characterApi, type CharacterField } from '@/api/rest';
import { LLMSettings } from '@/settings/LLMSettings';

export const ONBOARDED_KEY = 'moonlight.onboarded';

export interface OnboardingProps {
  onComplete: () => void;
}

const STEPS = ['欢迎', '检查', '大脑', '角色', '完成'];

export function Onboarding({ onComplete }: OnboardingProps): ReactElement {
  const { state, dispatch } = useAppState();
  const [step, setStep] = useState(0);
  const [checks, setChecks] = useState<Array<{ id: string; passed: boolean; hint: string }> | null>(null);
  const [micPassed, setMicPassed] = useState<boolean | null>(null);
  const [llmStatus, setLlmStatus] = useState<{ configured: boolean } | null>(null);
  const [characters, setCharacters] = useState<CharacterField[]>([]);
  const [checking, setChecking] = useState(false);

  // 第 2 步：就绪度检查（后端四项 + 前端麦克风权限）。
  useEffect(() => {
    if (step !== 1 || checking || checks) return;
    setChecking(true);
    void readinessApi
      .get()
      .then((r) => setChecks(r.checks))
      .catch(() => setChecks([]))
      .finally(() => setChecking(false));

    if (navigator.mediaDevices?.getUserMedia) {
      void navigator.mediaDevices
        .getUserMedia({ audio: true })
        .then((stream) => {
          stream.getTracks().forEach((t) => t.stop());
          setMicPassed(true);
        })
        .catch(() => setMicPassed(false));
    } else {
      setMicPassed(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [step]);

  // 第 3 步：对话大脑当前状态。
  useEffect(() => {
    if (step !== 2) return;
    void llmConfigApi.get().then((r) => setLlmStatus({ configured: r.is_configured })).catch(() => undefined);
  }, [step]);

  // 第 4 步：角色列表。
  useEffect(() => {
    if (step !== 3) return;
    void characterApi
      .list()
      .then((r) => setCharacters(r.characters))
      .catch(() => undefined);
  }, [step]);

  const readyAll = useMemo(() => {
    const backendOk = (checks ?? []).every((c) => c.passed);
    return backendOk && micPassed === true;
  }, [checks, micPassed]);

  const canNext =
    step !== 1 || // 欢迎/大脑/角色/完成：可跳过
    checks !== null || checking; // 检查中或已出结果即可前进（不强制全过）

  const finish = (): void => {
    try {
      localStorage.setItem(ONBOARDED_KEY, '1');
    } catch {
      // storage unavailable; treat as onboarded anyway
    }
    onComplete();
  };

  const toggleSetting = (key: 'screenAwareEnabled' | 'proactiveEnabled', value: boolean): void => {
    dispatch({ type: 'UPDATE_SETTINGS', settings: { [key]: value } });
  };

  return (
    <div className="onboarding">
      <div className="onboarding-card">
        <div className="onboarding-steps">
          {STEPS.map((label, i) => (
            <div key={label} className={`onboarding-step ${i <= step ? 'on' : ''}`}>
              <span className="onboarding-dot">{i + 1}</span>
              <span className="onboarding-label">{label}</span>
            </div>
          ))}
        </div>

        <div className="onboarding-body">
          {step === 0 && (
            <div className="onboarding-pane">
              <h2>欢迎使用 Moonlight 🌙</h2>
              <p className="onboarding-desc">
                你的 AI 桌宠已经就绪。接下来几步我们帮你把「对话大脑」和「角色」配好，
                之后她就会一直陪在你身边。
              </p>
              <div className="onboarding-conn">
                <span className={`conn-dot ${state.connStatus === 'connected' ? '' : ''}`} data-status={state.connStatus} />
                {state.connStatus === 'connected'
                  ? '已连接后端，一切正常'
                  : state.connStatus === 'connecting'
                    ? '正在连接后端…'
                    : '后端未连接（首次启动可能需要下载语音模型）'}
              </div>
            </div>
          )}

          {step === 1 && (
            <div className="onboarding-pane">
              <h2>就绪度检查</h2>
              <p className="onboarding-desc">一次跑完所有检查；失败项有修复指引，也可以先跳过。</p>
              <div className="onboarding-checks">
                {(checks ?? []).map((c) => (
                  <div key={c.id} className={`ob-check-row ${c.passed ? 'ok' : 'warn'}`}>
                    <span className="ob-check-ico">{c.passed ? '✓' : '!'}</span>
                    <span className="ob-check-label">{checkLabel(c.id)}</span>
                    <span className="ob-check-hint">{c.hint}</span>
                  </div>
                ))}
                <div className={`ob-check-row ${micPassed === false ? 'warn' : 'ok'}`}>
                  <span className="ob-check-ico">{micPassed === false ? '!' : micPassed ? '✓' : '…'}</span>
                  <span className="ob-check-label">麦克风</span>
                  <span className="ob-check-hint">
                    {micPassed === false
                      ? '权限被拒绝：请在系统设置中允许麦克风后重试'
                      : micPassed
                        ? '权限正常'
                        : '检测中…'}
                  </span>
                </div>
                {checking ? <div className="ob-checking">正在检查…</div> : null}
              </div>
              {readyAll ? <div className="onboarding-tip">全部就绪，可以直接开始聊天！</div> : null}
            </div>
          )}

          {step === 2 && (
            <div className="onboarding-pane">
              <h2>选择对话大脑</h2>
              <p className="onboarding-desc">
                对话大脑决定「小月」的智力来源：云端 API（推荐，效果好）或本地 Ollama（免费、私密）。
              </p>
              {llmStatus ? (
                <div className={`onboarding-llm-status ${llmStatus.configured ? 'ok' : ''}`}>
                  {llmStatus.configured ? '✅ 对话大脑已配置可用' : '⚠️ 还没配置对话模型'}
                </div>
              ) : null}
              <LLMSettings />
            </div>
          )}

          {step === 3 && (
            <div className="onboarding-pane">
              <h2>认识你的角色</h2>
              <p className="onboarding-desc">选一个陪伴你的角色。之后随时可以在「设置 → 角色卡」里更换。</p>
              <div className="onboarding-characters">
                {characters.map((c) => (
                  <div
                    key={c.filename}
                    className={`ob-char ${c.conf_uid === state.confUid ? 'active' : ''}`}
                  >
                    <span className="ob-char-avatar">
                      {c.character_name?.[0] ?? c.conf_name?.[0] ?? '?'}
                    </span>
                    <span className="ob-char-name">{c.character_name ?? c.conf_name ?? c.filename}</span>
                  </div>
                ))}
                {characters.length === 0 ? <div className="ob-checking">加载角色中…</div> : null}
              </div>
              <div className="onboarding-features">
                <div className="toggle-row">
                  <span>记忆（她记得你说过的事）</span>
                  <span className="ob-feature-badge">已默认开启</span>
                </div>
                <label className="toggle-row">
                  <span>屏幕感知（了解你在用什么应用）</span>
                  <input
                    type="checkbox"
                    checked={state.settings.screenAwareEnabled}
                    onChange={(e) => toggleSetting('screenAwareEnabled', e.target.checked)}
                  />
                </label>
                <label className="toggle-row">
                  <span>主动搭话（空闲时她会主动找你聊）</span>
                  <input
                    type="checkbox"
                    checked={state.settings.proactiveEnabled}
                    onChange={(e) => toggleSetting('proactiveEnabled', e.target.checked)}
                  />
                </label>
              </div>
            </div>
          )}

          {step === 4 && (
            <div className="onboarding-pane">
              <h2>一切就绪 🎉</h2>
              <p className="onboarding-desc">
                你的桌宠已经准备好啦。点击「开始使用」后她会出现在桌面上，
                随时可以从托盘菜单或右上角设置里调整一切。
              </p>
              <div className="onboarding-tip">小提示：点一下她，她会回应哦。</div>
            </div>
          )}
        </div>

        <div className="onboarding-nav">
          <button className="btn btn-ghost" disabled={step === 0} onClick={() => setStep(step - 1)}>
            上一步
          </button>
          <span className="ob-pos">
            {step + 1} / {STEPS.length}
          </span>
          {step < STEPS.length - 1 ? (
            <button className="btn" disabled={!canNext} onClick={() => setStep(step + 1)}>
              下一步
            </button>
          ) : (
            <button className="btn btn-primary" onClick={finish}>
              开始使用
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

function checkLabel(id: string): string {
  const map: Record<string, string> = {
    live2d: 'Live2D 模型',
    llm: '对话模型',
    asr_model: '语音识别',
    ollama: '本地 Ollama',
  };
  return map[id] ?? id;
}

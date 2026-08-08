import { useCallback, useEffect, useState, type ReactElement } from 'react';
import {
  memoryApi,
  ApiError,
  type MemoryResult,
  type V2Fact,
  type V2Reflection,
  type V2Proposal,
} from '@/api/rest';
import { SettingStatus } from './SettingStatus';

export interface MemorySettingsProps {
  confUid: string;
}

/** v2 子区 Tab：提案 / 事实 / 反思 */
type V2Tab = 'proposals' | 'facts' | 'reflections';

const TAB_LABELS: Record<V2Tab, string> = {
  proposals: '待审提案',
  facts: '关于你的事实',
  reflections: '过去的反思',
};

export function MemorySettings({ confUid }: MemorySettingsProps): ReactElement {
  const [memory, setMemory] = useState<MemoryResult | null>(null);
  const [content, setContent] = useState('');
  const [status, setStatus] = useState('');
  const [v2Tab, setV2Tab] = useState<V2Tab>('proposals');
  const [v2Facts, setV2Facts] = useState<V2Fact[]>([]);
  const [v2Reflections, setV2Reflections] = useState<V2Reflection[]>([]);
  const [v2Proposals, setV2Proposals] = useState<V2Proposal[]>([]);

  const refreshV2 = useCallback(async (uid: string): Promise<void> => {
    try {
      const [f, r, p] = await Promise.all([
        memoryApi.getFacts(uid, 'active', 30),
        memoryApi.getReflections(uid, '', 30),
        memoryApi.getProposals(uid, 'pending', 30),
      ]);
      setV2Facts(f.facts ?? []);
      setV2Reflections(r.reflections ?? []);
      setV2Proposals(p.proposals ?? []);
    } catch (err) {
      setStatus(errorMessage(err));
    }
  }, []);

  const load = useCallback(async (): Promise<void> => {
    if (!confUid) return;
    try {
      const res = await memoryApi.get(confUid);
      setMemory(res);
      setContent(res.content);
      if (res.v2_enabled) {
        void refreshV2(confUid);
      }
    } catch (err) {
      setStatus(errorMessage(err));
    }
  }, [confUid, refreshV2]);

  useEffect(() => {
    void load();
  }, [load]);

  const save = async (): Promise<void> => {
    try {
      await memoryApi.save({ conf_uid: confUid, content });
      setStatus('核心画像已保存（重启/重选角色后完整生效，后续整理即时读取）');
    } catch (err) {
      setStatus(errorMessage(err));
    }
  };

  const toggle = async (enabled: boolean): Promise<void> => {
    try {
      await memoryApi.toggle({ conf_uid: confUid, enabled });
      setMemory((m) => (m ? { ...m, enabled } : m));
      setStatus(enabled ? '记忆已启用' : '记忆已停用');
    } catch (err) {
      setStatus(errorMessage(err));
    }
  };

  const runDream = async (): Promise<void> => {
    try {
      const r = await memoryApi.dream({ conf_uid: confUid, promote_min_age_days: 0 });
      const s = r.summary;
      setStatus(
        `睡眠合并完成：归并 ${s.hash_dedup} · 归档 ${s.archived} · 自动合并 ${s.auto_merged} · 提案 ${s.proposals} · 固化反思 ${s.reflections_fused}`,
      );
      await load();
    } catch (err) {
      setStatus(errorMessage(err));
    }
  };

  if (!confUid) {
    return <div className="settings-section">请先连接后端获取角色 conf_uid。</div>;
  }

  return (
    <div className="settings-section">
      {/* 标题行 + 记忆总开关 */}
      <div className="mem-header">
        <h3>记忆</h3>
        <label className="mem-master-toggle">
          <span>启用记忆</span>
          <input
            type="checkbox"
            checked={memory?.enabled ?? false}
            disabled={!memory}
            onChange={(e) => void toggle(e.target.checked)}
          />
        </label>
      </div>

      {memory && (
        <>
          {/* 状态总览：一屏看懂记忆体量 */}
          <div className="mem-overview">
            <span className="mem-chip">
              核心画像
              <b>
                {memory.char_count}/{memory.cap} 字
              </b>
            </span>
            <span className="mem-chip">
              事实
              <b>{memory.v2_facts}</b>
            </span>
            <span className="mem-chip">
              反思
              <b>{memory.v2_reflections}</b>
            </span>
            <span className={`mem-chip${memory.v2_proposals_pending > 0 ? ' warn' : ''}`}>
              待审提案
              <b>{memory.v2_proposals_pending}</b>
            </span>
          </div>

          {/* ① 核心画像：persona 注入源 */}
          <div className="mem-card">
            <div className="mem-card-head">
              <span className="mem-card-title">核心画像</span>
              <span className="mem-card-desc">AI 对你的稳定认知 · 每轮对话注入 · 可直接编辑修正</span>
            </div>
            <textarea
              className="mem-textarea"
              rows={6}
              value={content}
              disabled={!memory.enabled}
              onChange={(e) => setContent(e.target.value)}
            />
            <div className="btn-row">
              <button className="btn btn-primary" onClick={() => void save()} disabled={!memory.enabled}>
                保存画像
              </button>
              <button
                className="btn"
                disabled={!memory.enabled}
                onClick={() =>
                  void memoryApi
                    .clear({ conf_uid: confUid })
                    .then(() => {
                      setContent('');
                      setStatus('核心画像已清空');
                    })
                    .catch((err) => setStatus(errorMessage(err)))
                }
              >
                清空
              </button>
            </div>
          </div>

          {/* ② 记忆沉淀：v2 类型化记忆（事实 → 反思 → 睡眠合并 → 固化画像） */}
          <div className="mem-card">
            <div className="mem-card-head">
              <span className="mem-card-title">记忆沉淀</span>
              <span className="mem-card-desc">对话自动沉淀 · 事实与反思 · 矛盾需你审批</span>
              <label className="mem-master-toggle compact">
                <span>{memory.v2_enabled ? '已开启' : '已关闭'}</span>
                <input
                  type="checkbox"
                  checked={memory.v2_enabled}
                  onChange={(e) =>
                    void memoryApi
                      .setV2({ conf_uid: confUid, enabled: e.target.checked })
                      .then(() => load())
                      .catch((err) => setStatus(errorMessage(err)))
                  }
                />
              </label>
            </div>
            {memory.v2_enabled ? (
              <>
                <div className="mem-tabs">
                  <button
                    className={`mem-tab${v2Tab === 'proposals' ? ' active' : ''}${v2Proposals.length ? ' has-badge' : ''}`}
                    onClick={() => setV2Tab('proposals')}
                  >
                    {TAB_LABELS.proposals}
                    {v2Proposals.length > 0 && <em>{v2Proposals.length}</em>}
                  </button>
                  <button
                    className={`mem-tab${v2Tab === 'facts' ? ' active' : ''}`}
                    onClick={() => setV2Tab('facts')}
                  >
                    {TAB_LABELS.facts}
                    {v2Facts.length > 0 && <em>{v2Facts.length}</em>}
                  </button>
                  <button
                    className={`mem-tab${v2Tab === 'reflections' ? ' active' : ''}`}
                    onClick={() => setV2Tab('reflections')}
                  >
                    {TAB_LABELS.reflections}
                    {v2Reflections.length > 0 && <em>{v2Reflections.length}</em>}
                  </button>
                  <span className="mem-tabs-actions">
                    <button className="btn btn-sm" onClick={() => void runDream()}>
                      立即合并（睡眠）
                    </button>
                    <button className="btn btn-sm" onClick={() => void refreshV2(confUid)}>
                      刷新
                    </button>
                  </span>
                </div>

                <div className="mem-panel">
                  {v2Tab === 'proposals' &&
                    (v2Proposals.length === 0 ? (
                      <p className="mem-empty">没有待审提案 · 矛盾或低置信合并会出现在这里</p>
                    ) : (
                      <ul className="v2-list">
                        {v2Proposals.map((p) => (
                          <li key={p.id} className="v2-item">
                            <span className={`v2-badge${p.kind === 'conflict' ? ' danger' : ''}`}>
                              {p.kind === 'merge' ? '合并' : '矛盾'}
                            </span>
                            <span className="v2-text">
                              {p.proposed_text || '（矛盾，需人工判断）'}
                              <span className="v2-meta"> 置信 {Math.round(p.confidence * 100)}%</span>
                            </span>
                            <span className="v2-actions">
                              <button
                                className="btn btn-sm"
                                onClick={() =>
                                  void memoryApi
                                    .decideProposal({ conf_uid: confUid, proposal_id: p.id, action: 'approve' })
                                    .then(() => refreshV2(confUid))
                                    .catch((err) => setStatus(errorMessage(err)))
                                }
                              >
                                批准
                              </button>
                              <button
                                className="btn btn-sm"
                                onClick={() =>
                                  void memoryApi
                                    .decideProposal({ conf_uid: confUid, proposal_id: p.id, action: 'reject' })
                                    .then(() => refreshV2(confUid))
                                    .catch((err) => setStatus(errorMessage(err)))
                                }
                              >
                                拒绝
                              </button>
                            </span>
                          </li>
                        ))}
                      </ul>
                    ))}
                  {v2Tab === 'facts' &&
                    (v2Facts.length === 0 ? (
                      <p className="mem-empty">还没有沉淀出事实 · 多聊几轮，AI 会从这里记住你</p>
                    ) : (
                      <ul className="v2-list">
                        {v2Facts.map((f) => (
                          <li key={f.id} className="v2-item">
                            <span className="v2-badge">
                              {Math.round(f.importance)}
                            </span>
                            <span className="v2-text">{f.text}</span>
                            <span className="v2-meta">
                              {' '}
                              ↑{f.reinforcement.toFixed(1)} ↓{f.disputation.toFixed(1)}
                            </span>
                            <span className="v2-actions">
                              <button
                                className="btn btn-sm"
                                title="强化这条事实（增加证据分）"
                                onClick={() =>
                                  void memoryApi
                                    .signalFact({ conf_uid: confUid, fact_id: f.id, action: 'reinforce' })
                                    .then(() => refreshV2(confUid))
                                    .catch((err) => setStatus(errorMessage(err)))
                                }
                              >
                                +
                              </button>
                              <button
                                className="btn btn-sm"
                                title="反驳这条事实（扣减证据分）"
                                onClick={() =>
                                  void memoryApi
                                    .signalFact({ conf_uid: confUid, fact_id: f.id, action: 'rebut' })
                                    .then(() => refreshV2(confUid))
                                    .catch((err) => setStatus(errorMessage(err)))
                                }
                              >
                                −
                              </button>
                            </span>
                          </li>
                        ))}
                      </ul>
                    ))}
                  {v2Tab === 'reflections' &&
                    (v2Reflections.length === 0 ? (
                      <p className="mem-empty">还没有归纳出反思 · 足够多事实后会自动合成</p>
                    ) : (
                      <ul className="v2-list">
                        {v2Reflections.map((r) => (
                          <li key={r.id} className="v2-item">
                            <span className={`v2-badge st-${r.status}`}>{statusLabel(r.status)}</span>
                            <span className="v2-text">{r.text}</span>
                          </li>
                        ))}
                      </ul>
                    ))}
                </div>
              </>
            ) : (
              <p className="mem-empty">记忆沉淀未开启 · 开启后每轮对话会自动抽取事实并合成反思</p>
            )}
          </div>

          {/* ③ 回忆检索：对话时能翻出什么 */}
          <div className="mem-card">
            <div className="mem-card-head">
              <span className="mem-card-title">回忆检索</span>
              <span className="mem-card-desc">对话时从历史里翻出相关内容注入提示词</span>
            </div>
            <div className="mem-grid-2">
              <label className="toggle-row">
                <span>全文检索 FTS{memory.fts_indexed ? '' : '（尚未建立索引）'}</span>
                <input
                  type="checkbox"
                  checked={memory.fts_enabled}
                  onChange={(e) =>
                    void memoryApi
                      .setFts({ conf_uid: confUid, enabled: e.target.checked })
                      .then(() => load())
                      .catch((err) => setStatus(errorMessage(err)))
                  }
                />
              </label>
              <label className="toggle-row">
                <span>
                  向量语义检索（{memory.vector_count} 条
                  {memory.vector_embedding?.model
                    ? ` · ${memory.vector_embedding.model.split('/').pop()}`
                    : ''}）
                </span>
                <input
                  type="checkbox"
                  checked={memory.vector_enabled}
                  onChange={(e) =>
                    void memoryApi
                      .setVector({ conf_uid: confUid, enabled: e.target.checked })
                      .then(() => load())
                      .catch((err) => setStatus(errorMessage(err)))
                  }
                />
              </label>
              {memory.fts_enabled && (
                <label className="field">
                  <span>FTS 检索条数</span>
                  <input
                    type="number"
                    min={memory.fts_top_k_min}
                    max={memory.fts_top_k_max}
                    value={memory.fts_top_k}
                    onChange={(e) =>
                      void memoryApi
                        .setFts({ conf_uid: confUid, top_k: Number(e.target.value) })
                        .then(() => load())
                        .catch((err) => setStatus(errorMessage(err)))
                    }
                  />
                </label>
              )}
              {memory.vector_enabled && (
                <label className="field">
                  <span>向量检索条数</span>
                  <input
                    type="number"
                    min={memory.vector_top_k_min}
                    max={memory.vector_top_k_max}
                    value={memory.vector_top_k}
                    onChange={(e) =>
                      void memoryApi
                        .setVector({ conf_uid: confUid, top_k: Number(e.target.value) })
                        .then(() => load())
                        .catch((err) => setStatus(errorMessage(err)))
                    }
                  />
                </label>
              )}
            </div>
            <div className="btn-row">
              <button
                className="btn"
                onClick={() =>
                  void memoryApi
                    .reindex({ conf_uid: confUid })
                    .then((r) => setStatus(`全文索引重建完成（${r.indexed_count} 条片段）`))
                    .catch((err) => setStatus(errorMessage(err)))
                }
              >
                重建全文索引
              </button>
            </div>
          </div>

          {/* ④ 整理策略：记忆如何自动维护 */}
          <div className="mem-card">
            <div className="mem-card-head">
              <span className="mem-card-title">整理策略</span>
              <span className="mem-card-desc">自动整理的频率与容量</span>
            </div>
            <div className="mem-grid-2">
              <label className="field">
                <span>画像整理间隔（每几轮对话整理一次）</span>
                <select
                  value={memory.consolidation_interval}
                  onChange={(e) =>
                    void memoryApi
                      .setConsolidation({ conf_uid: confUid, interval: Number(e.target.value) })
                      .then(() => load())
                      .catch((err) => setStatus(errorMessage(err)))
                  }
                >
                  {memory.consolidation_interval_choices.map((n) => (
                    <option key={n} value={n}>
                      {n} 轮
                    </option>
                  ))}
                </select>
              </label>
              <label className="field">
                <span>事实沉淀上限（{memory.v2_max_facts} 条）</span>
                <input
                  type="number"
                  min={50}
                  max={5000}
                  step={50}
                  value={memory.v2_max_facts}
                  onChange={(e) =>
                    void memoryApi
                      .setV2({ conf_uid: confUid, max_facts: Number(e.target.value) })
                      .then(() => load())
                      .catch((err) => setStatus(errorMessage(err)))
                  }
                />
              </label>
            </div>
            <label className="field">
              <span>核心画像容量（{memory.cap} 字）</span>
              <div className="range-row">
                <input
                  type="range"
                  min={memory.cap_min}
                  max={memory.cap_max}
                  step={100}
                  value={memory.cap}
                  onChange={(e) =>
                    void memoryApi
                      .setCap({ conf_uid: confUid, cap: Number(e.target.value) })
                      .then(() => load())
                      .catch((err) => setStatus(errorMessage(err)))
                  }
                />
                <span className="range-val">{memory.cap}</span>
              </div>
            </label>
          </div>
        </>
      )}
      <SettingStatus message={status} />
    </div>
  );
}

function statusLabel(s: string): string {
  switch (s) {
    case 'confirmed':
      return '已确认';
    case 'promoted':
      return '已固化';
    case 'archived':
      return '已归档';
    default:
      return '待确认';
  }
}

function errorMessage(err: unknown): string {
  if (err instanceof ApiError) return err.message;
  return err instanceof Error ? err.message : '发生未知错误';
}

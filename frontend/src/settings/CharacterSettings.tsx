import { useCallback, useEffect, useRef, useState, type ReactElement } from 'react';
import {
  characterApi,
  voiceApi,
  ttsApi,
  engineApi,
  live2dCatalogApi,
  ApiError,
  API_BASE,
  type CharacterField,
  type CatalogModel,
  type TtsVoiceCatalogResult,
  type EngineInfo,
} from '@/api/rest';
import { useAppState } from '@/state/AppStateContext';
import { renderModelToPng } from '@/live2d/captureThumbnail';
import { SettingStatus } from './SettingStatus';
import {
  SettingsActionBar,
  SettingsGroup,
  SettingsMetricStrip,
  SettingsRow,
} from './SettingsConsole';
import type { WSClient } from '@/api/wsClient';

export interface CharacterSettingsProps {
  /** 当前 WS 连接；用于「启用角色」/ 保存后热重载（sendSwitchConfig）。 */
  ws?: () => WSClient | null;
}

export function CharacterSettings({ ws }: CharacterSettingsProps): ReactElement {
  const { state } = useAppState();
  const [characters, setCharacters] = useState<CharacterField[]>([]);
  /** 模型清单（单一数据源：live2d_catalog 扫描，含专属 Prompt 与缩略图）。 */
  const [catalog, setCatalog] = useState<CatalogModel[]>([]);
  const [selectedFile, setSelectedFile] = useState('');
  const [form, setForm] = useState({
    conf_name: '',
    persona: '',
    skin: '',
    model_prompt: '',
    voice: '',
    tts_engine: '',
  });
  const [status, setStatus] = useState('');
  /** 已配置可用的 TTS 引擎（角色卡只显示这些，中文名）。 */
  const [ttsEngines, setTtsEngines] = useState<EngineInfo[]>([]);
  const [ttsEnginesLoading, setTtsEnginesLoading] = useState(false);
  const ttsLoadStarted = useRef(false);
  /** 当前引擎的音色目录（list=下拉 / input=手填）。 */
  const [voiceCatalog, setVoiceCatalog] = useState<TtsVoiceCatalogResult | null>(null);
  const [previewing, setPreviewing] = useState(false);
  const [generating, setGenerating] = useState<string | null>(null);
  const [editing, setEditing] = useState(false);

  /** 默认试听文本（后端 /api/tts-voice-sample 未传 text 时使用，VOICEVOX 会用日语版）。 */
  const PREVIEW_TEXT = '月光落在窗台上，晚风轻拂过耳畔，想与你说声晚安。';

  const previewVoice = async (): Promise<void> => {
    if (!form.voice) {
      setStatus('请先选择一个音色');
      return;
    }
    try {
      setPreviewing(true);
      // 用真实引擎 + 指定音色合成（edge/voicevox/云端可用；克隆引擎需服务在线）
      const url = await voiceApi.sample(form.tts_engine || 'edge_tts', form.voice);
      const audio = new Audio(url);
      audio.onended = () => URL.revokeObjectURL(url);
      audio.onerror = () => {
        URL.revokeObjectURL(url);
        setStatus('试听失败（引擎不可用或未配置密钥）');
      };
      void audio.play();
    } catch (err) {
      setStatus(errorMessage(err));
    } finally {
      setPreviewing(false);
    }
  };

  const load = useCallback(async (): Promise<void> => {
    // 分别更新：模型目录不再等待角色列表，角色列表也不再等待更慢的引擎探测。
    const results = await Promise.allSettled([
      characterApi.list().then((result) => {
        setCharacters(result.characters);
        return result;
      }),
      live2dCatalogApi.models().then((result) => {
        setCatalog(result.models);
        return result;
      }),
    ]);
    const failed = results.find((r) => r.status === 'rejected');
    if (failed) setStatus(errorMessage(failed.reason));
  }, []);

  /**
   * 引擎目录会扫描本地引擎并探测服务，首屏不需要它。
   * 仅在用户进入编辑器时懒加载，且整个组件生命周期只发起一次请求。
   */
  const loadTtsEngines = useCallback(async (): Promise<void> => {
    if (ttsLoadStarted.current) return;
    ttsLoadStarted.current = true;
    setTtsEnginesLoading(true);
    try {
      const result = await engineApi.configured('tts');
      setTtsEngines(result.engines);
    } catch (err) {
      // 允许用户返回列表后再次进入编辑器重试。
      ttsLoadStarted.current = false;
      setStatus(errorMessage(err));
    } finally {
      setTtsEnginesLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    if (editing) void loadTtsEngines();
  }, [editing, loadTtsEngines]);

  /** 拉取某引擎的音色目录（list=下拉 / input=手填）。 */
  const loadVoiceCatalog = async (engine: string): Promise<void> => {
    setVoiceCatalog(null);
    try {
      if (!engine) return;
      setVoiceCatalog(await ttsApi.listVoices(engine));
    } catch (err) {
      setStatus(errorMessage(err));
    }
  };

  const handleEngineChange = (engine: string): void => {
    setForm((f) => ({ ...f, tts_engine: engine, voice: '' }));
    void loadVoiceCatalog(engine);
  };

  const selectCharacter = (c: CharacterField): void => {
    setSelectedFile(c.filename);
    setForm({
      conf_name: c.conf_name ?? '',
      persona: c.persona_prompt ?? '',
      skin: c.live2d_model_name ?? '',
      model_prompt: c.model_prompt ?? '',
      voice: c.voice ?? '',
      tts_engine: c.tts_model ?? '',
    });
    setEditing(true);
    void loadVoiceCatalog(c.tts_model ?? '');
  };

  /** 选择 Live2D 模型：写入 skin，并把该模型的专属 Prompt 带入文本域（可微调）。 */
  const handleModelSelect = (name: string): void => {
    const model = catalog.find((m) => m.name === name);
    setForm((f) => ({
      ...f,
      skin: name,
      model_prompt: model?.custom_prompt ?? f.model_prompt,
    }));
  };

  /** 把当前选中模型的默认专属 Prompt 重新带入（覆盖文本域）。 */
  const resetModelPrompt = (): void => {
    const model = catalog.find((m) => m.name === form.skin);
    if (!model) return;
    setForm((f) => ({ ...f, model_prompt: model.custom_prompt ?? '' }));
    setStatus(`已从「${model.name}」重新带入默认专属指令`);
  };

  /** 把当前文本域内容保存为该模型的默认专属 Prompt（model_prompt.txt），影响后续新建角色。 */
  const saveModelDefaultPrompt = async (): Promise<void> => {
    if (!form.skin) return;
    if (!window.confirm(`将当前指令保存为「${form.skin}」模型的默认专属 Prompt（model_prompt.txt）？\n新建角色时默认带入此内容。`)) return;
    try {
      await live2dCatalogApi.savePrompt(form.skin, form.model_prompt);
      setStatus(`已保存为「${form.skin}」模型默认专属指令`);
      await load();
    } catch (err) {
      setStatus(errorMessage(err));
    }
  };

  /** 某角色文件是否就是当前激活角色（按 conf_uid 判定）。 */
  const isActiveFile = (file: string): boolean => {
    const c = characters.find((x) => x.filename === file);
    return c != null && c.conf_uid === state.confUid;
  };

  /**
   * 通过 WS switch-config 热切换角色：后端重新读盘 + load_from_config，
   * Live2D / persona / TTS / ASR 立即生效，无需重启。
   */
  const applyCharacter = (file: string): void => {
    const client = ws?.();
    if (!client) {
      setStatus('未连接到后端，无法立即切换（WebSocket 未建立）');
      return;
    }
    client.sendSwitchConfig(file);
    setStatus(isActiveFile(file) ? '已重新加载该角色，修改已生效' : `正在切换到「${file === 'conf.yaml' ? '基础角色' : file}」…`);
  };

  /** 删除当前编辑中的角色卡（非基础卡）。 */
  const removeSelected = async (): Promise<void> => {
    if (!selectedFile || selectedFile === 'conf.yaml') return;
    const c = characters.find((x) => x.filename === selectedFile);
    if (!c) return;
    if (!window.confirm(`确定删除角色「${c.conf_name ?? c.filename}」？`)) return;
    const wasActive = c.conf_uid === state.confUid;
    try {
      await characterApi.remove(selectedFile);
      setEditing(false);
      setSelectedFile('');
      await load();
      if (wasActive) {
        // 删掉了当前激活角色 → 自动切回基础角色，避免悬空配置。
        applyCharacter('conf.yaml');
        setStatus('已删除，并已自动切回基础角色');
      } else {
        setStatus('已删除');
      }
    } catch (err) {
      setStatus(errorMessage(err));
    }
  };

  /** 保存角色卡；返回保存/新建到的文件名（失败或未进入编辑返回 null）。 */
  const save = async (): Promise<string | null> => {
    // 新建角色卡（原本未选中任何文件）保存成功后自动返回角色卡列表
    const wasCreating = !selectedFile;
    // 前端必填校验（后端同样校验，这里提前拦截并给出清晰提示）
    if (!form.conf_name.trim()) {
      setStatus('创建失败：请填写「显示名称」');
      return null;
    }
    if (!form.persona.trim()) {
      setStatus('创建失败：请填写「人设 (persona)」');
      return null;
    }
    if (!form.skin) {
      setStatus('创建失败：请选择「Live2D 模型」');
      return null;
    }
    const body: Record<string, unknown> = {
      conf_name: form.conf_name,
      persona_prompt: form.persona,
      live2d_model_name: form.skin,
      model_prompt: form.model_prompt,
      voice: form.voice,
    };
    if (form.tts_engine) body.tts_model = form.tts_engine;
    try {
      let savedFile: string;
      if (selectedFile === 'conf.yaml') {
        await characterApi.update('conf.yaml', body);
        savedFile = 'conf.yaml';
      } else if (selectedFile) {
        await characterApi.update(selectedFile, body);
        savedFile = selectedFile;
      } else {
        const created = await characterApi.create(body);
        savedFile = created.filename;
      }
      await load();
      // 修改的是当前激活角色 → 自动热重载，立即生效（无需重启 / 重新选择）。
      if (savedFile && isActiveFile(savedFile)) {
        applyCharacter(savedFile);
      } else if (wasCreating) {
        // 新建角色成功 → 自动跳回角色卡列表，新卡已出现在列表中
        setEditing(false);
        setSelectedFile('');
        setStatus(`已创建角色「${savedFile.replace(/\.yaml$/, '')}」，点击「启用」立即切换`);
      } else {
        setStatus(savedFile ? '已保存，点击「启用」立即切换' : '已创建，点击「启用」立即切换');
      }
      return savedFile;
    } catch (err) {
      setStatus(`创建失败：${errorMessage(err)}`);
      return null;
    }
  };

  /** 保存 + 立即切换到该角色，成功后跳回角色卡列表（新建 / 非当前角色也强制生效）。 */
  const saveAndApply = async (): Promise<void> => {
    const file = await save();
    if (!file) return;
    applyCharacter(file);
    setEditing(false);
    setSelectedFile('');
    setStatus(`已保存并启用「${file === 'conf.yaml' ? '基础角色' : file.replace(/\.yaml$/, '')}」，修改已生效`);
  };

  /** 开始新建角色卡（清空表单，进入编辑视图）。 */
  const startNew = (): void => {
    setSelectedFile('');
    setForm({ conf_name: '', persona: '', skin: '', model_prompt: '', voice: '', tts_engine: '' });
    setStatus('填写下方信息，点击「保存」即可创建新角色卡');
    setEditing(true);
  };

  /** 补全后端静态资源的相对路径（live2d-models/... 等）。 */
  const resolveAvatar = (avatar: string | null): string | null => {
    if (!avatar) return null;
    if (/^https?:\/\//.test(avatar)) return avatar;
    return `${API_BASE}/${avatar.replace(/^\/+/, '')}`;
  };

  /** 取某 Live2D 模型的缩略图 URL。 */
  const modelAvatar = (modelName: string): string | null => {
    const m = catalog.find((x) => x.name === modelName);
    return resolveAvatar(m?.thumbnail ?? null);
  };

  const currentCharacter = characters.find((c) => c.conf_uid === state.confUid);

  /** 渲染模型生成完整立绘缩略图并保存到模型文件夹。 */
  const generateThumbnail = async (m: CatalogModel): Promise<void> => {
    if (generating) return;
    setGenerating(m.name);
    setStatus('');
    try {
      const url = `${API_BASE}/${m.model_url.replace(/^\/+/, '')}`;
      const png = await renderModelToPng(url);
      await live2dCatalogApi.saveThumbnail(m.name, png);
      await load();
      setStatus(`已生成「${m.name}」立绘缩略图`);
    } catch (err) {
      setStatus(errorMessage(err));
    } finally {
      setGenerating(null);
    }
  };

  return (
    <div className="settings-section settings-console-root settings-character-console">
      <SettingsMetricStrip
        metrics={[
          { label: '当前角色', value: currentCharacter?.conf_name ?? state.confName ?? '基础角色', tone: currentCharacter ? 'ok' : 'neutral' },
          { label: '角色卡', value: `${characters.length} 个` },
          { label: 'Live2D', value: currentCharacter?.live2d_model_name ?? '未选择', tone: currentCharacter?.live2d_model_name ? 'ok' : 'warn' },
        ]}
      />

      {!editing ? (
        <SettingsGroup
          title="角色卡"
          className="console-group-primary character-gallery-group"
        >
          <div className="character-grid" data-setting-key="setting-character-cards">
            {characters.map((c) => {
              const avatar = modelAvatar(c.live2d_model_name ?? '');
              const isCurrent = c.conf_uid === state.confUid;
              return (
                <div
                  key={c.filename}
                  className={`character-card ${isCurrent ? 'current' : ''}`}
                  onClick={() => selectCharacter(c)}
                  title={isCurrent ? '当前角色（点击编辑）' : '点击编辑此角色'}
                >
                  <div className="character-card-avatar">
                    {avatar ? (
                      <img src={avatar} alt={c.conf_name ?? c.filename} loading="lazy" decoding="async" />
                    ) : (
                      <span>🎭</span>
                    )}
                    {isCurrent ? <span className="character-card-badge">当前</span> : null}
                  </div>
                  <div className="character-card-copy">
                    <div className="character-card-name">{c.conf_name ?? c.filename}</div>
                  </div>
                  {!isCurrent ? (
                    <button
                      className="character-card-enable"
                      onClick={(e) => {
                        e.stopPropagation();
                        applyCharacter(c.filename);
                      }}
                      title={`切换到「${c.conf_name ?? c.filename}」，立即生效`}
                    >
                      启用角色
                    </button>
                  ) : (
                    <button
                      className="character-card-enable current"
                      onClick={(e) => {
                        e.stopPropagation();
                        applyCharacter(c.filename);
                      }}
                      title="重新加载当前角色（修改后立即生效）"
                    >
                      已启用 · 重新加载
                    </button>
                  )}
                </div>
              );
            })}
            <button className="character-card character-card-new" type="button" onClick={startNew} title="创建新角色卡">
              <span className="character-card-new-icon" aria-hidden="true">＋</span>
              <span className="character-card-new-label">新建角色</span>
            </button>
          </div>
        </SettingsGroup>
      ) : (
        <>
          <SettingsGroup
            title={selectedFile ? '编辑角色卡' : '新建角色卡'}
            className="character-editor-group"
          >
          <SettingsActionBar className="character-editor-back">
            <button className="btn" onClick={() => setEditing(false)}>
              ← 返回角色卡列表
            </button>
          </SettingsActionBar>
          <SettingsRow label="显示名称" settingKey="setting-character-persona">
            <input value={form.conf_name} onChange={(e) => setForm({ ...form, conf_name: e.target.value })} />
          </SettingsRow>
          <SettingsRow label="Live2D 模型" className="character-model-row">
            <select value={form.skin} onChange={(e) => handleModelSelect(e.target.value)}>
              <option value="">选择模型…</option>
              {catalog.map((s) => (
                <option key={s.name} value={s.name}>
                  {s.name}
                </option>
              ))}
            </select>
          </SettingsRow>
          <div className="model-picker">
            <div className="model-grid">
              {catalog.map((m) => (
                <div
                  key={m.name}
                  className={`model-card ${form.skin === m.name ? 'active' : ''}`}
                  onClick={() => handleModelSelect(m.name)}
                >
                  <div className="model-card-avatar">
                    {resolveAvatar(m.thumbnail) ? (
                      <img src={resolveAvatar(m.thumbnail) ?? undefined} alt={m.name} loading="lazy" decoding="async" />
                    ) : (
                      <span>🎭</span>
                    )}
                  </div>
                  <div className="model-card-name">{m.name}</div>
                  <button
                    className="model-card-gen"
                    onClick={(e) => {
                      e.stopPropagation();
                      void generateThumbnail(m);
                    }}
                    disabled={generating !== null}
                    title="渲染模型并生成立绘缩略图"
                  >
                    {generating === m.name ? '生成中…' : '🎞 生成立绘'}
                  </button>
                </div>
              ))}
            </div>
          </div>
          <SettingsRow
            label="模型专属指令"
            description={`已带入「${form.skin || '—'}」的默认指令，可微调；保存后写入角色卡`}
          >
            <div className="console-stack">
              <textarea
                className="console-textarea"
                rows={3}
                value={form.model_prompt}
                onChange={(e) => setForm({ ...form, model_prompt: e.target.value })}
                placeholder="选择模型后自动带入其专属指令，可在此微调…"
              />
              <div className="console-btn-row">
                <button type="button" className="console-btn" onClick={() => void resetModelPrompt()} disabled={!form.skin} title="重新带入所选模型的默认专属指令">
                  重置为模型默认
                </button>
                <button type="button" className="console-btn" onClick={() => void saveModelDefaultPrompt()} disabled={!form.skin} title="将当前内容保存为模型的默认专属指令（model_prompt.txt）">
                  存为模型默认
                </button>
              </div>
            </div>
          </SettingsRow>
          <SettingsRow label="TTS 引擎">
            {ttsEnginesLoading ? (
              <span className="character-inline-label">正在读取可用引擎…</span>
            ) : (
              <select value={form.tts_engine} onChange={(e) => handleEngineChange(e.target.value)}>
                <option value="">继承默认（基础配置）</option>
                {ttsEngines.map((e) => (
                  <option key={e.key} value={e.key}>
                    {e.zh}
                  </option>
                ))}
              </select>
            )}
          </SettingsRow>
          {form.tts_engine ? (
            <div className="console-row character-voice-row">
              <div className="console-row-copy"><span className="console-row-label">音色</span></div>
              <div className="console-row-control">
              {voiceCatalog?.mode === 'input' ? (
                <>
                  <span className="character-inline-label">{voiceCatalog.input?.label}</span>
                  <div className="voice-row">
                    <input
                      value={form.voice}
                      placeholder={voiceCatalog.input?.hint}
                      onChange={(e) => setForm({ ...form, voice: e.target.value })}
                    />
                    {voiceCatalog?.previewable && form.voice ? (
                      <button
                        type="button"
                        className="btn"
                        onClick={() => void previewVoice()}
                        disabled={previewing}
                        title={`${voiceCatalog.previewNote || '用真实引擎合成试听'}（试听文本：${PREVIEW_TEXT}）`}
                      >
                        {previewing ? '播放中…' : '🎧 试听'}
                      </button>
                    ) : null}
                  </div>
                </>
              ) : (
                <>
                  <div className="voice-row">
                    <select value={form.voice} onChange={(e) => setForm({ ...form, voice: e.target.value })}>
                      <option value="">继承默认</option>
                      {(voiceCatalog?.voices ?? []).map((v) => (
                        <option key={v.value} value={v.value}>
                          {v.label}
                        </option>
                      ))}
                    </select>
                    {voiceCatalog?.previewable && form.voice ? (
                      <button
                        type="button"
                        className="btn"
                        onClick={() => void previewVoice()}
                        disabled={previewing}
                        title={`${voiceCatalog.previewNote || '用真实引擎合成试听'}（试听文本：${PREVIEW_TEXT}）`}
                      >
                        {previewing ? '播放中…' : '🎧 试听'}
                      </button>
                    ) : null}
                  </div>
                </>
              )}
              </div>
            </div>
          ) : null}
          <SettingsRow label="人设（persona）">
            <textarea
              rows={5}
              value={form.persona}
              onChange={(e) => setForm({ ...form, persona: e.target.value })}
            />
          </SettingsRow>
          <SettingsActionBar>
            <button className="btn btn-primary" onClick={() => void save()}>
              保存
            </button>
            <button className="btn" onClick={() => void saveAndApply()}>
              保存并启用
            </button>
            {selectedFile && selectedFile !== 'conf.yaml' ? (
              <button className="btn btn-danger" onClick={() => void removeSelected()}>
                删除
              </button>
            ) : null}
          </SettingsActionBar>
          <SettingStatus message={status} />
          </SettingsGroup>
        </>
      )}
    </div>
  );
}

function errorMessage(err: unknown): string {
  if (err instanceof ApiError) return err.message;
  return err instanceof Error ? err.message : '发生未知错误';
}

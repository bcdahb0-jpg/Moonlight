import { useEffect, useState, type ReactElement } from 'react';
import { get } from '@/api/rest';
import { SettingsGroup, SettingsRow, SettingsStatusBadge } from '@/settings/SettingsConsole';

/**
 * 技能系统（P5 闭环，task_platform/skills 已实现 catalog/frontmatter/tools）：
 * 技能索引 + 详情展示（元数据渐进注入、指令按需加载的契约说明）。
 *
 * 数据源：GET /api/skills（已存在于 task_platform Phase 3）、GET /api/skills/{name}。
 * 技能格式：SKILL.md + frontmatter（name/description/allowed-tools/required-secrets）。
 */

interface SkillMeta {
  name: string;
  description: string;
  'allowed-tools'?: string[];
  'required-secrets'?: string[];
  [k: string]: unknown;
}

interface SkillsResult {
  ok: boolean;
  skills: SkillMeta[];
}

export function SkillLibrarySettings(): ReactElement {
  const [skills, setSkills] = useState<SkillMeta[]>([]);
  const [detail, setDetail] = useState<string>('');
  const [notice, setNotice] = useState('');

  const refresh = (): void => {
    void get<SkillsResult>('/api/skills')
      .then((res) => {
        if (res.ok) setSkills(res.skills ?? []);
      })
      .catch(() => setNotice('技能库拉取失败（后端未重启？）'));
  };

  useEffect(() => {
    refresh();
  }, []);

  const openDetail = (name: string): void => {
    void get<{ ok: boolean; skill?: { body?: string } }>(`/api/skills/${name}`)
      .then((res: { ok: boolean; skill?: { body?: string } }) =>
        setDetail(res.skill?.body ? res.skill.body.slice(0, 600) : '(无正文)'),
      )
      .catch(() => setNotice('技能详情拉取失败'));
  };

  return (
    <SettingsGroup title="技能系统" description="SKILL.md + frontmatter：元数据渐进注入，指令按需加载">
      <SettingsRow label="技能系统" description="catalog 扫描 public/custom/ + .disabled 跳过">
        <SettingsStatusBadge tone={skills.length > 0 ? 'ok' : 'neutral'}>
          {skills.length} 个技能已索引
        </SettingsStatusBadge>
      </SettingsRow>

      <SettingsRow label="渐进加载" description="仅元数据进 prompt，指令（正文）按需注入">
        <div className="console-tag-wrap">
          <span className="console-tag">frontmatter → prompt</span>
          <span className="console-tag">body → 按需 read_skill</span>
        </div>
      </SettingsRow>

      <SettingsRow label="技能库">
        <ul style={{ listStyle: 'none', margin: 0, padding: 0, display: 'flex', flexDirection: 'column', gap: 6, width: '100%' }}>
          {skills.length === 0 ? (
            <li style={{ fontSize: 12, color: 'var(--text-faint,rgba(236,231,251,0.45))' }}>
              暂无技能（backend/task_platform/skills 下 SKILL.md 目录）
            </li>
          ) : (
            skills.map((s) => (
              <li key={s.name} style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8, padding: '8px 10px', borderRadius: 8, background: 'rgba(236,231,251,0.05)' }}>
                <div style={{ display: 'flex', flexDirection: 'column', gap: 3, minWidth: 0 }}>
                  <span style={{ fontWeight: 600, fontSize: 12.5, color: 'var(--text-primary,#ece7fb)' }}>{s.name}</span>
                  <span style={{ fontSize: 11.5, color: 'var(--text-secondary,rgba(236,231,251,0.72))' }}>{s.description}</span>
                  {s['allowed-tools'] && s['allowed-tools'].length > 0 && (
                    <div className="console-tag-wrap" style={{ marginTop: 2 }}>
                      {s['allowed-tools'].map((t) => (
                        <span key={t} className="console-tag" style={{ color: 'var(--success,#6ee7a8)' }}>{t}</span>
                      ))}
                    </div>
                  )}
                </div>
                <button type="button" className="console-btn" onClick={() => openDetail(s.name)}>
                  详情
                </button>
              </li>
            ))
          )}
        </ul>
        {detail && (
          <pre style={{ width: '100%', boxSizing: 'border-box', maxHeight: 160, overflow: 'auto', marginTop: 8, padding: 8, borderRadius: 8, background: 'rgba(10,12,28,0.6)', fontSize: 11, color: 'var(--text-secondary,rgba(236,231,251,0.72))', whiteSpace: 'pre-wrap' }}>
            {detail}
          </pre>
        )}
      </SettingsRow>

      <SettingsRow label="状态">
        <SettingsStatusBadge tone={notice ? 'warn' : 'ok'}>
          {notice || '技能链路闭环（frontmatter → catalog → tools）'}
        </SettingsStatusBadge>
      </SettingsRow>
    </SettingsGroup>
  );
}

export default SkillLibrarySettings;

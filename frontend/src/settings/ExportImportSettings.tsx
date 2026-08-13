import { useRef, useState, type ReactElement } from 'react';
import { exportApi } from '@/api/rest';
import { SettingsGroup, SettingsRow, SettingsStatusBadge } from '@/settings/SettingsConsole';

/**
 * 导出与分享（P5，mea-pet store.py 思路）：角色卡 / 全配置导出（脱敏）+ 导入合并。
 *
 * 导出：GET /api/export/character|config（后端 scrub_secrets：清空 api_key/token/
 * sessdata 等敏感值但保留字段形状）；导入：POST /api/import（schema 校验 →
 * 白名单节 merge → surgical 写入 conf.yaml）。
 */

function downloadJson(filename: string, data: unknown): void {
  const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

export function ExportImportSettings(): ReactElement {
  const [notice, setNotice] = useState('');
  const [busy, setBusy] = useState(false);
  const fileRef = useRef<HTMLInputElement | null>(null);

  const exportCharacter = (): void => {
    setBusy(true);
    void exportApi
      .character()
      .then((data) => downloadJson(`moonlight-character-${Date.now()}.json`, data))
      .catch(() => setNotice('导出角色卡失败'))
      .finally(() => setBusy(false));
  };

  const exportConfig = (): void => {
    setBusy(true);
    void exportApi
      .config()
      .then((data) => downloadJson(`moonlight-config-${Date.now()}.json`, data))
      .catch(() => setNotice('导出配置失败'))
      .finally(() => setBusy(false));
  };

  const onFile = (file: File): void => {
    setBusy(true);
    void file
      .text()
      .then((text) => JSON.parse(text) as unknown)
      .then((payload) => exportApi.import(payload))
      .then((res) => {
        setNotice(res.ok ? `导入成功：${res.imported?.join(', ') ?? ''}` : `导入失败: ${res.error ?? ''}`);
      })
      .catch(() => setNotice('文件解析失败（需合法 JSON）'))
      .finally(() => setBusy(false));
  };

  return (
    <SettingsGroup title="导出与分享" description="导出文件已脱敏（密钥清空、字段形状保留）">
      <SettingsRow label="导出角色卡" description="角色卡 + 人设 + 玩家提示词 JSON">
        <button type="button" className="console-btn" onClick={exportCharacter} disabled={busy}>
          导出 JSON
        </button>
      </SettingsRow>

      <SettingsRow label="导出配置" description="全部 conf 节（脱敏后）">
        <button type="button" className="console-btn" onClick={exportConfig} disabled={busy}>
          导出全部配置
        </button>
      </SettingsRow>

      <SettingsRow label="从文件导入" description="白名单节合并（character_config / system_config 子节），不覆盖敏感项">
        <button type="button" className="console-btn" onClick={() => fileRef.current?.click()} disabled={busy}>
          选择文件
        </button>
        <input
          ref={fileRef}
          type="file"
          accept="application/json,.json"
          style={{ display: 'none' }}
          onChange={(e) => {
            const f = e.target.files?.[0];
            if (f) onFile(f);
            e.target.value = '';
          }}
        />
      </SettingsRow>

      <SettingsRow label="状态">
        <SettingsStatusBadge tone={notice ? 'warn' : 'ok'}>
          {notice || '导出不含真实密钥，可放心分享'}
        </SettingsStatusBadge>
      </SettingsRow>
    </SettingsGroup>
  );
}

export default ExportImportSettings;

import { useCallback, useEffect, useRef, useState, type ReactElement } from 'react';
import { pluginApi, pluginConfigApi, type PluginItem } from '@/api/rest';
import { SettingsGroup, SettingsRow, SettingsStatusBadge } from '@/settings/SettingsConsole';

/**
 * 插件管理器（P5）：已安装列表 + 启停 + 生命周期钩子展示。
 *
 * 数据源：GET /api/plugin/list（registry 扫描 backend/plugins/{builtin,community}）；
 * 启停：POST /api/plugin/toggle（写 data/plugins_enabled.json + 触发 on_load/on_unload）。
 * 本地安装 zip 需后端提供 upload 端点（P6 探索，先用提示占位）。
 */

function PluginRow({ item, onToggle }: { item: PluginItem; onToggle: (id: string) => void }): ReactElement {
  return (
    <li style={{ display: 'flex', flexDirection: 'column', gap: 4, padding: '8px 10px', borderRadius: 8, background: 'rgba(236,231,251,0.05)' }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8 }}>
        <span style={{ fontWeight: 600, fontSize: 12.5, color: 'var(--text-primary,#ece7fb)' }}>
          {item.title}
          <span style={{ opacity: 0.55, fontWeight: 400, marginLeft: 6, fontSize: 11 }}>
            v{item.version} · {item.author || '匿名'}
          </span>
        </span>
        <button
          type="button"
          className="console-btn"
          onClick={() => onToggle(item.plugin_id)}
        >
          {item.enabled ? '停用' : '启用'}
        </button>
      </div>
      <div style={{ fontSize: 11.5, color: 'var(--text-secondary,rgba(236,231,251,0.72))' }}>{item.description}</div>
      <div className="console-tag-wrap">
        <span className="console-tag">{item.category}</span>
        <span className="console-tag">{item.plugin_id}</span>
        {item.hooks.map((h) => (
          <span key={h} className="console-tag" style={{ color: 'var(--success,#6ee7a8)' }}>
            {h}
          </span>
        ))}
      </div>
    </li>
  );
}

export function PluginManagerSettings(): ReactElement {
  const [plugins, setPlugins] = useState<PluginItem[]>([]);
  const [notice, setNotice] = useState('');
  // P6：本地 zip 安装
  const zipRef = useRef<HTMLInputElement | null>(null);
  const [zipping, setZipping] = useState(false);

  const refresh = useCallback((): void => {
    void pluginApi
      .list()
      .then((res) => {
        if (res.ok) setPlugins(res.plugins);
      })
      .catch(() => setNotice('插件列表拉取失败（后端未重启？）'));
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const installZip = (file: File): void => {
    setZipping(true);
    void pluginConfigApi
      .installZip(file)
      .then((res) => {
        setNotice(res.ok ? `已安装 ${res.plugin_id ?? res.name ?? ''}` : `安装失败: ${res.error ?? ''}`);
        refresh();
      })
      .catch((e: unknown) => setNotice(`安装失败: ${e instanceof Error ? e.message : String(e)}`))
      .finally(() => setZipping(false));
  };

  const toggle = (plugin_id: string): void => {
    void pluginApi.toggle(plugin_id).then((res) => {
      setNotice(res.ok ? (res.enabled ? `已启用 ${plugin_id}` : `已停用 ${plugin_id}`) : `操作失败: ${res.error ?? ''}`);
      refresh();
    });
  };

  const enabledCount = plugins.filter((p) => p.enabled).length;

  return (
    <SettingsGroup title="插件管理器" description="SDK 契约：on_load / on_unload / on_message">
      <SettingsRow label="已安装插件">
        <SettingsStatusBadge tone={enabledCount > 0 ? 'ok' : 'neutral'}>
          {plugins.length} 个已安装 · {enabledCount} 个已启用
        </SettingsStatusBadge>
      </SettingsRow>

      <SettingsRow label="生命周期钩子" description="on_message 返回非 None 即吞掉消息（对话链路已接入：聊天里发 echo: xxx 试试）">
        <div className="console-tag-wrap">
          <span className="console-tag">on_load(ctx)</span>
          <span className="console-tag">on_unload()</span>
          <span className="console-tag">on_message(msg) → str | None</span>
        </div>
      </SettingsRow>

      <SettingsRow label="插件列表">
        <ul style={{ listStyle: 'none', margin: 0, padding: 0, display: 'flex', flexDirection: 'column', gap: 6, width: '100%' }}>
          {plugins.length === 0 ? (
            <li style={{ fontSize: 12, color: 'var(--text-faint,rgba(236,231,251,0.45))' }}>
              无插件（扫描 backend/plugins/，放入 plugin.json 目录即被识别）
            </li>
          ) : (
            plugins.map((p) => <PluginRow key={p.plugin_id} item={p} onToggle={toggle} />)
          )}
        </ul>
      </SettingsRow>

      <SettingsRow label="本地安装" description="安装 .zip（含 plugin.json，防路径穿越）">
        <div className="console-btn-row" style={{ display: 'flex', gap: 8 }}>
          <button type="button" className="console-btn" onClick={() => zipRef.current?.click()} disabled={zipping}>
            {zipping ? '安装中…' : '选择 zip 安装'}
          </button>
          <input
            ref={zipRef}
            type="file"
            accept=".zip,application/zip"
            style={{ display: 'none' }}
            onChange={(e) => {
              const f = e.target.files?.[0];
              if (f) installZip(f);
              e.target.value = '';
            }}
          />
          <SettingsStatusBadge tone="neutral">或从市场安装（下方「技能市场」卡）</SettingsStatusBadge>
        </div>
      </SettingsRow>

      <SettingsRow label="状态">
        <SettingsStatusBadge tone={notice ? 'warn' : 'ok'}>
          {notice || '插件运行时正常'}
        </SettingsStatusBadge>
      </SettingsRow>
    </SettingsGroup>
  );
}

export default PluginManagerSettings;

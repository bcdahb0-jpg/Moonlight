import { useEffect, useState, type ReactElement } from 'react';
import { marketplaceApi, pluginConfigApi, type InstallStatus, type MarketplacePlugin } from '@/api/rest';
import { SettingsGroup, SettingsRow, SettingsStatusBadge } from '@/settings/SettingsConsole';

/**
 * 技能市场（P5）：目录册（远程 URL 可配置 / 内置兜底）+ 搜索 + 安装进度。
 *
 * 数据源：GET /api/plugin/marketplace；安装：POST /api/plugin/marketplace/install
 * （后台线程下载 zip → 解压 → 校验 → 落盘 plugins/community/）；
 * 进度：轮询 GET /api/plugin/marketplace/install-status/{name}。
 * 内置目录册的条目 download_url 为空（离线占位）→ 安装时后端返回真实报错。
 */

function InstallBar({ status }: { status: InstallStatus }): ReactElement | null {
  if (status.status === 'idle') return null;
  const tone = status.status === 'error' ? 'warn' : 'ok';
  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6, fontSize: 11.5 }}>
      <SettingsStatusBadge tone={tone}>{status.status}</SettingsStatusBadge>
      <span style={{ color: 'var(--text-faint,rgba(236,231,251,0.45))' }}>
        {status.progress}%{status.error ? ` · ${status.error}` : ''}
      </span>
    </span>
  );
}

export function SkillMarketplaceSettings(): ReactElement {
  const [plugins, setPlugins] = useState<MarketplacePlugin[]>([]);
  const [query, setQuery] = useState('');
  const [source, setSource] = useState<'remote' | 'builtin'>('builtin');
  const [installStates, setInstallStates] = useState<Record<string, InstallStatus>>({});
  const [notice, setNotice] = useState('');
  // P6：远程目录册 URL 配置
  const [marketUrl, setMarketUrl] = useState('');
  const [urlSaved, setUrlSaved] = useState('');

  const refresh = (): void => {
    void marketplaceApi
      .list()
      .then((res) => {
        setPlugins(res.plugins);
        setSource(res.source);
      })
      .catch(() => setNotice('目录册拉取失败'));
  };

  useEffect(() => {
    refresh();
    void pluginConfigApi
      .get()
      .then((cfg) => setMarketUrl(cfg.marketplace_url ?? ''))
      .catch(() => undefined);
  }, []);

  const saveUrl = (): void => {
    void pluginConfigApi
      .save({ marketplace_url: marketUrl.trim() })
      .then(() => {
        setUrlSaved(`目录册地址已保存（${marketUrl.trim() || '内置兜底'}），已刷新列表`);
        refresh();
      })
      .catch(() => setNotice('保存失败（后端未重启？）'));
  };

  const install = (name: string): void => {
    setInstallStates((prev) => ({ ...prev, [name]: { status: 'installing', progress: 0 } }));
    void marketplaceApi.install(name).then((res) => {
      if (!res.ok) {
        setInstallStates((prev) => ({ ...prev, [name]: { status: 'error', progress: 0, error: res.error ?? '安装失败' } }));
        setNotice(res.error ?? '安装失败');
        return;
      }
      // 后台线程：轮询进度直至终态
      const timer = window.setInterval(() => {
        void marketplaceApi.installStatus(name).then((st) => {
          setInstallStates((prev) => ({ ...prev, [name]: st }));
          if (st.status === 'error' || st.status === '完成') {
            window.clearInterval(timer);
            if (st.status === '完成') setNotice(`已安装 ${name}`);
          }
        });
      }, 1500);
    });
  };

  const filtered = plugins.filter(
    (p) => !query || p.display_name.toLowerCase().includes(query.toLowerCase()) || p.description.includes(query),
  );

  return (
    <SettingsGroup title="技能市场" description={`目录册来源：${source === 'remote' ? '远程（可配置 URL）' : '内置（离线兜底）'}`}>
      <SettingsRow label="远程目录册地址" description="GitHub raw JSON 或自建静态 JSON（plugins 数组，字段 name/display_name/description/author/version/category/repo）">
        <div className="console-stack">
          <input
            className="console-input"
            style={{ maxWidth: 360 }}
            placeholder="https://example.com/marketplace.json（留空 = 内置目录册）"
            value={marketUrl}
            onChange={(e) => setMarketUrl(e.target.value)}
          />
          <button type="button" className="console-btn" onClick={saveUrl}>
            保存并刷新
          </button>
        </div>
      </SettingsRow>

      <SettingsRow label="搜索技能">
        <input
          className="console-input"
          style={{ maxWidth: 260 }}
          placeholder="搜索…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
      </SettingsRow>

      <SettingsRow label="可安装技能">
        <ul style={{ listStyle: 'none', margin: 0, padding: 0, display: 'flex', flexDirection: 'column', gap: 6, width: '100%' }}>
          {filtered.length === 0 ? (
            <li style={{ fontSize: 12, color: 'var(--text-faint,rgba(236,231,251,0.45))' }}>无匹配技能</li>
          ) : (
            filtered.map((p) => (
              <li key={p.name} style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8, padding: '8px 10px', borderRadius: 8, background: 'rgba(236,231,251,0.05)' }}>
                <div style={{ display: 'flex', flexDirection: 'column', gap: 3, minWidth: 0 }}>
                  <span style={{ fontWeight: 600, fontSize: 12.5, color: 'var(--text-primary,#ece7fb)' }}>
                    {p.display_name}
                    <span style={{ opacity: 0.55, fontWeight: 400, marginLeft: 6, fontSize: 11 }}>v{p.version} · {p.author || '社区'}</span>
                  </span>
                  <span style={{ fontSize: 11.5, color: 'var(--text-secondary,rgba(236,231,251,0.72))' }}>{p.description}</span>
                  <span className="console-tag-wrap" style={{ marginTop: 2 }}>
                    <span className="console-tag">{p.category}</span>
                    <span className="console-tag">{p.name}</span>
                  </span>
                </div>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                  <InstallBar status={installStates[p.name] ?? { status: 'idle', progress: 0 }} />
                  <button type="button" className="console-btn" onClick={() => install(p.name)} disabled={installStates[p.name]?.status === 'installing'}>
                    安装
                  </button>
                </div>
              </li>
            ))
          )}
        </ul>
      </SettingsRow>

      <SettingsRow label="状态">
        <SettingsStatusBadge tone={notice ? 'warn' : 'ok'}>
          {urlSaved || notice || `${filtered.length} 个技能 · ${source === 'builtin' ? '内置（离线可用）' : '远程目录册'}`}
        </SettingsStatusBadge>
      </SettingsRow>
    </SettingsGroup>
  );
}

export default SkillMarketplaceSettings;

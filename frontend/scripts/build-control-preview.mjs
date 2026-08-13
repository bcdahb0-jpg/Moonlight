/**
 * 构建「控制台雏形」独立预览页 —— docs/control-preview.html
 *
 * 用途：不启动 Electron/Vite，直接在浏览器打开即可预览设置控制台雏形
 * （与 React 版 ControlCenter 同数据源 controlData.ts、同配色、同布局）。
 *
 * 运行：cd frontend && node scripts/build-control-preview.mjs
 * 产物：docs/control-preview.html（self-contained，可单文件分发）
 */
import { build } from 'esbuild';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(__dirname, '..');
const outRoot = path.resolve(root, '..', 'docs');

/* 1. 编译 controlData.ts → IIFE（window.ControlData） */
const bundle = await build({
  entryPoints: [path.join(root, 'src/control/controlData.ts')],
  bundle: true,
  format: 'iife',
  globalName: 'ControlData',
  write: false,
  minify: false,
  platform: 'browser',
  logLevel: 'error',
});
const dataJs = bundle.outputFiles[0].text;

/* 1.1 编译 iconPaths.ts → IIFE（window.IconPaths，纯 SVG path 数据，零 React 依赖） */
const iconBundle = await build({
  entryPoints: [path.join(root, 'src/ui/iconPaths.ts')],
  bundle: true,
  format: 'iife',
  globalName: 'IconPaths',
  write: false,
  minify: false,
  platform: 'browser',
  logLevel: 'error',
});
const iconPathsJs = iconBundle.outputFiles[0].text;

/* 2. 提取 global.css :root 暗色主题变量 */
const globalCss = fs.readFileSync(path.join(root, 'src/styles/global.css'), 'utf8');
const rootMatch = globalCss.match(/:root\s*\{([\s\S]*?)\n\}/);
const rootVars = rootMatch ? rootMatch[0] : ':root {}';

/* 3. 控制台样式（去注释，保持原样） */
const controlCss = fs.readFileSync(path.join(root, 'src/control/ControlCenter.css'), 'utf8');

/* 4. 渲染脚本（复刻 ControlCenter.tsx 的 vanilla 版） */
const renderJs = `
(function () {
  var D = window.ControlData;
  var STATUS_LABEL = { live: '已实现', planned: '规划中' };
  var SOURCE_NAMES = D.SOURCE_NAMES;

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }

  /* 线性 SVG 图标（与 React 版 icons.tsx 同源数据、同描边参数） */
  function iconSVG(id, size) {
    return '<svg width="' + size + '" height="' + size + '" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
      (window.IconPaths[id] || '') + '</svg>';
  }

  function controlHTML(field, onClick) {
    var status = field.status || 'live';
    var cls = 'cc-ctrl cc-ctrl-' + field.type + (status === 'planned' ? ' planned' : '');
    switch (field.type) {
      case 'toggle':
        return '<button type="button" class="' + cls + ' cc-toggle' + (field.value ? ' on' : '') + '" role="switch" onclick="' + onClick + '">' +
          '<span class="cc-toggle-track"><span class="cc-toggle-thumb"></span></span>' +
          '<span class="cc-toggle-text">' + (field.value ? '开' : '关') + '</span></button>';
      case 'select':
        return '<button type="button" class="' + cls + ' cc-select" onclick="' + onClick + '">' +
          '<span>' + esc(field.textValue || (field.options && field.options[0]) || '—') + '</span>' +
          '<svg viewBox="0 0 16 16" width="14" height="14"><path d="M4 6l4 4 4-4" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg></button>';
      case 'text':
        return '<div class="' + cls + ' cc-text"><span class="cc-text-value">' + esc(field.textValue || '') + '</span>' +
          (field.badge ? '<span class="cc-mini-badge">' + esc(field.badge) + '</span>' : '') + '</div>';
      case 'slider': {
        var pct = ((field.valueNum || 0) / (field.max || 1)) * 100;
        return '<div class="' + cls + ' cc-slider"><div class="cc-slider-track">' +
          '<div class="cc-slider-fill" style="width:' + pct + '%"></div>' +
          '<span class="cc-slider-thumb" style="left:' + pct + '%"></span></div>' +
          '<span class="cc-slider-value">' + field.valueNum + (field.suffix || '') + '</span></div>';
      }
      case 'progress': {
        var pp = ((field.valueNum || 0) / (field.max || 100)) * 100;
        return '<div class="' + cls + ' cc-progress"><div class="cc-progress-track">' +
          '<div class="cc-progress-fill ' + (field.tone || 'neutral') + '" style="width:' + pp + '%"></div></div>' +
          '<span class="cc-progress-value">' + field.valueNum + (field.suffix || '') + '</span></div>';
      }
      case 'stat':
        return '<div class="' + cls + ' cc-stat"><span class="cc-stat-value ' + (field.tone || 'neutral') + '">' + esc(field.textValue || '—') + '</span>' +
          (field.badge ? '<span class="cc-mini-badge">' + esc(field.badge) + '</span>' : '') + '</div>';
      case 'tags':
        return '<div class="' + cls + ' cc-tags">' + (field.tags || []).map(function (t) {
          return '<span class="cc-tag">' + esc(t) + '</span>';
        }).join('') + '</div>';
      case 'button':
        return '<div class="' + cls + ' cc-btns">' + (field.actions || []).map(function (a) {
          return '<button type="button" class="cc-btn" onclick="' + onClick + '">' + esc(a) + '</button>';
        }).join('') + '</div>';
      default:
        return '<span>—</span>';
    }
  }

  function cardHTML(card, onClick) {
    var status = card.status || 'live';
    var srcTag = status === 'planned' && card.source ? ' · ' + esc(SOURCE_NAMES[card.source]) : '';
    var fields = card.component
      ? '<div class="cc-component-placeholder"><strong>⚡ 真实功能组件</strong><span>已在 Electron 控制台接入（' + esc(card.title) + '），此预览页仅展示展位结构</span></div>'
      : card.fields.map(function (f) {
          return '<div class="cc-field"><div class="cc-field-copy">' +
            '<span class="cc-field-label">' + esc(f.label) + '</span>' +
            (f.description ? '<span class="cc-field-desc">' + esc(f.description) + '</span>' : '') +
            '</div><div class="cc-field-control">' + controlHTML(f, onClick) + '</div></div>';
        }).join('');
    return '<section class="cc-card' + (status === 'planned' ? ' cc-card-planned' : '') + '">' +
      '<header class="cc-card-head"><span class="cc-card-icon">' + card.icon + '</span>' +
      '<div class="cc-card-title"><h4>' + esc(card.title) + '</h4>' +
      (card.description ? '<p>' + esc(card.description) + '</p>' : '') + '</div>' +
      '<span class="cc-field-status ' + status + '">' + (status === 'live' ? '●' : '🔧') + ' ' + STATUS_LABEL[status] +
      (srcTag ? '<em>' + srcTag + '</em>' : '') + '</span></header>' +
      '<div class="cc-card-body">' + fields + '</div></section>';
  }

  var root = document.getElementById('cc-app');
  var state = { active: 'overview', query: '' };
  var onClick = 'window.__ccRipple&&window.__ccRipple(event)';

  window.__ccRipple = function (e) {
    var el = e.currentTarget;
    el.classList.remove('cc-ripple');
    void el.offsetWidth;
    el.classList.add('cc-ripple');
  };

  function render() {
    var top = '<header class="cc-topbar"><div class="cc-topbar-brand"><span class="cc-logo">🌙</span>' +
      '<div><h2>Moonlight 控制台</h2><p>桌宠全功能配置中心 · 雏形预览</p></div><span class="cc-version">v0.12 雏形</span></div>' +
      '<div class="cc-topbar-stats">' + D.TOP_STATS.map(function (s) {
        return '<div class="cc-topstat ' + s.tone + '" title="' + esc(s.hint || '') + '">' +
          '<span class="cc-topstat-label">' + esc(s.label) + '</span><strong>' + esc(s.value) + '</strong></div>';
      }).join('') + '</div></header>';

    var groups = D.SECTION_GROUPS;
    var nav = '<nav class="cc-nav" aria-label="控制台分区"><div class="cc-nav-search">' +
      '<svg viewBox="0 0 16 16" width="14" height="14"><circle cx="7" cy="7" r="4.5" fill="none" stroke="currentColor" stroke-width="1.4"/><path d="M10.5 10.5L14 14" stroke="currentColor" stroke-width="1.4" stroke-linecap="round"/></svg>' +
      '<input type="text" placeholder="搜索配置…" value="' + esc(state.query) + '" oninput="window.__ccSearch(this.value)" aria-label="搜索配置"/></div>' +
      groups.map(function (g) {
        return '<div class="cc-nav-group"><span class="cc-nav-group-label">' + esc(g) + '</span>' +
          D.CONTROL_SECTIONS.filter(function (s) { return s.group === g; }).map(function (s) {
            return '<button type="button" class="cc-nav-item' + (s.id === state.active ? ' active' : '') + '" onclick="window.__ccNav(\\'' + s.id + '\\')">' +
              '<span class="cc-nav-icon">' + iconSVG(s.icon, 17) + '</span><span class="cc-nav-copy"><strong>' + esc(s.label) + '</strong>' +
              '<em>' + esc(s.description) + '</em></span></button>';
          }).join('') + '</div>';
      }).join('') +
      '<div class="cc-nav-foot"><span>已实现 ' + D.CONTROL_SECTIONS.reduce(function (n, s) {
        return n + s.cards.filter(function (c) { return (c.status || 'live') === 'live'; }).length;
      }, 0) + ' 组 · 规划中 ' + D.CONTROL_SECTIONS.reduce(function (n, s) {
        return n + s.cards.filter(function (c) { return (c.status || 'live') === 'planned'; }).length;
      }, 0) + ' 组</span></div></nav>';

    var section = D.CONTROL_SECTIONS.find(function (s) { return s.id === state.active; }) || D.CONTROL_SECTIONS[0];
    var q = state.query.trim().toLowerCase();
    var cards = section.cards;
    if (q) {
      cards = section.cards.map(function (card) {
        var kept = card.fields.filter(function (f) {
          return (f.label + ' ' + (f.description || '') + ' ' + card.title + ' ' + section.label).toLowerCase().indexOf(q) >= 0;
        });
        if (kept.length > 0) return { fields: kept, title: card.title, description: card.description, icon: card.icon, status: card.status, source: card.source, id: card.id };
        return (card.title + ' ' + (card.description || '')).toLowerCase().indexOf(q) >= 0 ? card : null;
      }).filter(Boolean);
    }

    var main = '<main class="cc-main"><div class="cc-main-head"><div><h3><span class="cc-main-icon">' + iconSVG(section.icon, 22) + '</span>' +
      esc(section.label) + '</h3><p>' + esc(section.description) + '</p></div>' +
      '<span class="cc-main-count">' + cards.length + ' 个功能组</span></div>' +
      (q ? '<p class="cc-search-hint">匹配到 ' + cards.length + ' 个功能组</p>' : '') +
      '<div class="cc-cards">' + (cards.length
        ? cards.map(function (c) { return cardHTML(c, onClick); }).join('')
        : '<div class="cc-empty"><span>🔍</span><p>没有匹配「' + esc(state.query) + '」的配置项</p></div>') + '</div>' +
      '<footer class="cc-foot-note"><span>💡 当前为<b>雏形版</b>：按钮可点击但暂无实际功能，用于预览控制台结构。</span>' +
      '<span>配色沿用 Moonlight 暗夜月光主题 · 参考 UI：PetGPT 管理页 / SoulLink 可视化面板 / N.E.K.O 分区结构</span></footer></main>';

    root.innerHTML = '<div class="cc-root"><div class="cc-body">' + nav + main + '</div></div>';
    document.querySelector('.cc-root').insertBefore(
      (function () { var d = document.createElement('div'); d.innerHTML = top; return d.firstChild; })(),
      document.querySelector('.cc-body')
    );
  }

  window.__ccNav = function (id) { state.active = id; render(); };
  window.__ccSearch = function (v) { state.query = v; render(); };
  render();
})();
`;

/* 5. 组装 HTML */
const html = `<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1.0" />
<title>Moonlight 设置控制台 · 雏形预览</title>
<style>
${rootVars}
body { margin: 0; background: #0c0a1f; overflow: hidden; }
#cc-app { height: 100vh; }
/* 控制台组件样式 */
${controlCss}
</style>
</head>
<body>
<div id="cc-app"></div>
<script>
${iconPathsJs}
</script>
<script>
${dataJs}
</script>
<script>
${renderJs}
</script>
</body>
</html>`;

fs.mkdirSync(outRoot, { recursive: true });
const outFile = path.join(outRoot, 'control-preview.html');
fs.writeFileSync(outFile, html, 'utf8');
console.log('✅ 控制台雏形预览页已生成:', outFile, '(' + (fs.statSync(outFile).size / 1024).toFixed(1) + ' KB)');

/**
 * generate-config-types.mjs — 从契约层 config-schema.json 生成 TS 类型。
 *
 * 输入：contracts/config-schema.json（由后端 pydantic Config.model_json_schema() 生成，
 *       见 backend/src/open_llm_vtuber/routes.py GET /api/config/schema）
 * 输出：src/types/generated/config.ts
 *
 * 用法：node scripts/generate-config-types.mjs
 */
import { readFileSync, writeFileSync, mkdirSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..'); // frontend/
const schemaPath = resolve(root, '../contracts/config-schema.json');
const outDir = resolve(root, 'src/types/generated');
const outPath = resolve(outDir, 'config.ts');

const schema = JSON.parse(readFileSync(schemaPath, 'utf-8'));
const defs = schema.$defs ?? {};

/** 生成 TS 类型表达式（不含 export）。 */
function toTs(s, indent = 0) {
  const pad = '  '.repeat(indent);
  if (s == null) return 'any';
  if (s.$ref) {
    const name = s.$ref.replace(/^#\/\$defs\//, '');
    return name;
  }
  if (Array.isArray(s.type)) {
    // 多 type（如 ["string","null"]）→ 联合
    return s.type.map((t) => toTs({ ...s, type: t }, indent)).join(' | ');
  }
  switch (s.type) {
    case 'string':
      return s.enum ? s.enum.map((v) => JSON.stringify(v)).join(' | ') || 'string' : 'string';
    case 'number':
    case 'integer':
      return 'number';
    case 'boolean':
      return 'boolean';
    case 'array': {
      const items = s.items ? toTs(s.items, indent) : 'unknown';
      return `Array<${items}>`;
    }
    case 'object': {
      const props = s.properties ?? {};
      const required = new Set(s.required ?? []);
      const lines = Object.entries(props).map(([key, ps]) => {
        const opt = required.has(key) ? '' : '?';
        const type = toTs(ps, indent + 1);
        // key 含特殊字符或非合法标识符时加引号
        const k = /^[A-Za-z_$][\w$]*$/.test(key) ? key : JSON.stringify(key);
        return `${pad}  ${k}${opt}: ${type};`;
      });
      const additional = s.additionalProperties
        ? `${pad}  [key: string]: ${toTs(s.additionalProperties, indent + 1)};`
        : '';
      return `{\n${lines.join('\n')}${lines.length && additional ? '\n' : ''}${additional}\n${pad}}`;
    }
    case 'null':
      return 'null';
    default:
      if (s.anyOf) return s.anyOf.map((x) => toTs(x, indent)).join(' | ');
      if (s.oneOf) return s.oneOf.map((x) => toTs(x, indent)).join(' | ');
      if (s.allOf) return s.allOf.map((x) => toTs(x, indent)).join(' & ');
      return 'unknown';
  }
}

const lines = [
  '// 本文件由 scripts/generate-config-types.mjs 自动生成，请勿手改。',
  '// 数据源：contracts/config-schema.json（后端 pydantic Config.model_json_schema()）。',
  '// 重新生成：node scripts/generate-config-types.mjs',
  '',
];

// $defs 里的模型逐个生成 interface
for (const [name, def] of Object.entries(defs)) {
  if (def.type === 'object') {
    lines.push(`export interface ${name} ${toTs(def, 0)}`);
    lines.push('');
  } else {
    lines.push(`export type ${name} = ${toTs(def, 0)};`);
    lines.push('');
  }
}

// 顶层 Config
lines.push(`export interface Config ${toTs(schema, 0)}`);
lines.push('');

mkdirSync(outDir, { recursive: true });
writeFileSync(outPath, lines.join('\n'), 'utf-8');
console.log(`✔ 已生成 ${outPath}（${defs.length ? Object.keys(defs).length : 0} 个模型 + Config）`);

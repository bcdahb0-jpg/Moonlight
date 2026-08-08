import { build } from 'esbuild';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.dirname(path.dirname(fileURLToPath(import.meta.url)));

const shared = {
  bundle: true,
  platform: 'node',
  format: 'cjs',
  target: 'node20',
  external: ['electron', 'active-win'],
  sourcemap: true,
  logLevel: 'info',
};

await build({
  ...shared,
  entryPoints: [path.join(root, 'electron', 'main.ts')],
  outfile: path.join(root, 'dist-electron', 'main.js'),
});

await build({
  ...shared,
  entryPoints: [path.join(root, 'electron', 'preload.ts')],
  outfile: path.join(root, 'dist-electron', 'preload.js'),
});

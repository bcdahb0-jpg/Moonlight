import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import { resolve } from 'node:path';

// Renderer-only Vite config. The Electron main process is compiled separately
// by esbuild (scripts/build-electron.mjs / scripts/dev.mjs).
export default defineConfig({
  plugins: [react()],
  base: './',
  root: resolve(__dirname, '.'),
  resolve: {
    alias: {
      '@': resolve(__dirname, 'src'),
    },
  },
  build: {
    outDir: 'dist',
    emptyOutDir: true,
    target: 'chrome120',
    sourcemap: true,
    rollupOptions: {
      input: resolve(__dirname, 'index.html'),
    },
  },
  server: {
    port: 5173,
    strictPort: false,
    host: '127.0.0.1',
  },
});

import path from 'node:path';
import tailwindcss from '@tailwindcss/vite';
import react from '@vitejs/plugin-react';
import { defineConfig } from 'vite';

// FastAPI(:8765)가 dist를 정적 서빙한다 — dev 서버는 API만 프록시.
export default defineConfig({
  define: { __BUILD__: JSON.stringify(new Date().toISOString().slice(0, 16).replace('T', ' ')) },
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: { '@': path.resolve(__dirname, './src') },
  },
  server: {
    proxy: {
      '/api': 'http://127.0.0.1:8765',
      '/demos': 'http://127.0.0.1:8765',
      '/fonts': 'http://127.0.0.1:8765',
    },
  },
  build: { outDir: 'dist', assetsDir: 'assets' },
});

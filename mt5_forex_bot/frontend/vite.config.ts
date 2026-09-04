import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// The dashboard talks to the local FastAPI backend. In development Vite proxies
// /api and /ws to it, so the browser only ever sees one origin.
const BACKEND = process.env.VITE_BACKEND_URL ?? 'http://127.0.0.1:8000';

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    strictPort: false,
    proxy: {
      '/api': { target: BACKEND, changeOrigin: true },
      '/ws': { target: BACKEND.replace('http', 'ws'), ws: true },
    },
  },
  build: { outDir: 'dist', sourcemap: false, chunkSizeWarningLimit: 1200 },
});

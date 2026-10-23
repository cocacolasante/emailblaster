import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// `/api/*` → the backend, prefix stripped.  Used by the public build
// (VITE_API_BASE_URL=/api, served by `vite preview` behind the Cloudflare
// tunnel) so the dashboard and the API share ONE origin: the session cookie
// stays first-party and no CORS is involved.  The local dev server keeps
// calling http://localhost:8000 directly unless VITE_API_BASE_URL says so.
const apiProxy = {
  '/api': {
    target: process.env.VITE_API_PROXY_TARGET || 'http://backend:8000',
    changeOrigin: true,
    rewrite: (path) => path.replace(/^\/api/, '') || '/',
  },
};

// Hostnames `vite preview` answers to besides localhost (comma-separated).
const allowedHosts = (process.env.VITE_ALLOWED_HOSTS || '')
  .split(',')
  .map((h) => h.trim())
  .filter(Boolean);

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    host: true,
    proxy: apiProxy,
  },
  preview: {
    port: 4173,
    host: true,
    strictPort: true,
    proxy: apiProxy,
    allowedHosts,
  },
  test: {
    globals: true,
    environment: 'jsdom',
    setupFiles: ['./src/setupTests.js'],
    css: false,
  },
});

import {defineConfig} from 'vite'
import react from '@vitejs/plugin-react'
import path from 'path'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  server: {
    proxy: {
      '/api': {
        // The browser always talks to the Vite dev server (localhost:5173);
        // Vite forwards /api to the API service.  Inside Docker the API is
        // reached via PROXY_TARGET (http://app:8000, set in docker-compose.yml);
        // when running `npm run dev` on the host it defaults to localhost:8000.
        // changeOrigin stays disabled so API redirects keep the browser-
        // reachable host instead of leaking a container-only hostname.
        target: process.env.PROXY_TARGET || 'http://localhost:8000',
        changeOrigin: false,
        secure: false,
      },
    },
  },
})
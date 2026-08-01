import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Proxy API + websocket calls to the FastAPI backend during development so the
// frontend can use same-origin relative URLs. Override the backend location
// with VITE_PROXY_TARGET (e.g. VITE_PROXY_TARGET=http://localhost:8077).
const target = process.env.VITE_PROXY_TARGET || 'http://localhost:8000'

export default defineConfig({
  plugins: [react()],
  server: {
    port: Number(process.env.VITE_PORT) || 5173,
    proxy: {
      '/tasks': { target, changeOrigin: true, ws: true },
      '/mcp': { target, changeOrigin: true },
      '/projects': { target, changeOrigin: true },
      '/schedules': { target, changeOrigin: true },
      '/templates': { target, changeOrigin: true },
      '/approvals': { target, changeOrigin: true },
      '/usage': { target, changeOrigin: true },
      '/findings': { target, changeOrigin: true },
      '/system': { target, changeOrigin: true },
      '/health': { target, changeOrigin: true },
    },
  },
})

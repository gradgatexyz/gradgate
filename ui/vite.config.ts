import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// The terminal reads everything from the engine (engine/, FastAPI on :8765), which also serves the built dist/.
// In dev (`npm run dev`) Vite proxies /api and /img to it, so the client only ever uses relative URLs.
// ENGINE points the dev proxy at another engine (e.g. a second one on :8766).
const engine = process.env.ENGINE || 'http://127.0.0.1:8765'
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/api': { target: engine, changeOrigin: true },
      '/img': { target: engine, changeOrigin: true },
    },
  },
})

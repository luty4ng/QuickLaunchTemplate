import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// One source tree, three products:
//   web      -> VITE_WEB_BASE=/      served by FastAPI at the site root
//   desktop  -> VITE_WEB_BASE=./     loaded from file:// inside Electron
//   android  -> VITE_WEB_BASE=./     loaded by the Capacitor WebView
// VITE_API_BASE is baked in at build time; empty means "same origin as the page",
// which is what the single-image web deployment wants.
export default defineConfig({
  base: process.env.VITE_WEB_BASE ?? '/',
  plugins: [react()],
  server: {
    port: 5173,
    // Local development talks to the API through the dev server, so the browser
    // sees one origin and cookies behave exactly like they do in production.
    proxy: {
      '/api': { target: process.env.VITE_DEV_API ?? 'http://127.0.0.1:8000', changeOrigin: false },
    },
  },
  build: {
    outDir: process.env.VITE_OUT_DIR ?? 'dist',
    emptyOutDir: true,
    sourcemap: false,
  },
})

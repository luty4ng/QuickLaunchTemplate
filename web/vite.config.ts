import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'

// One source tree, three products:
//   web      -> base '/'    served by FastAPI at the site root
//   desktop  -> base './'   loaded from app://bundle inside Electron
//   android  -> base './'   loaded by the Capacitor WebView
//
// The default lives in `.env` (VITE_WEB_BASE=./) rather than in each CI job, so
// the four places that build this bundle cannot drift apart. `loadEnv` is what
// makes a .env value visible here: Vite only injects VITE_* into import.meta.env
// for application code, not into this config file.
//
// VITE_API_BASE is baked in at build time; empty means "same origin as the
// page", which is what the single-image web deployment wants.
export default defineConfig(({ mode }) => {
  const env = { ...loadEnv(mode, process.cwd(), ''), ...process.env }

  return {
    base: env.VITE_WEB_BASE ?? '/',
    plugins: [react()],
    server: {
      port: 5173,
      // Local development talks to the API through the dev server, so the
      // browser sees one origin and cookies behave exactly like in production.
      proxy: {
        '/api': { target: env.VITE_DEV_API ?? 'http://127.0.0.1:8000', changeOrigin: false },
      },
    },
    build: {
      outDir: env.VITE_OUT_DIR ?? 'dist',
      emptyOutDir: true,
      sourcemap: false,
    },
  }
})

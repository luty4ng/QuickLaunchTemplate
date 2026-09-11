import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'

// One source tree, three products:
//   web      -> served by FastAPI, desktop shell, Android WebView
//   desktop  -> loaded from app://bundle inside Electron
//   android  -> loaded by the Capacitor WebView
//
// The base is relative by DEFAULT, and that default lives here rather than in an
// environment variable or a .env file, because two of the three targets cannot
// load root-absolute asset URLs (/assets/...) and a per-job variable is exactly
// the kind of thing that drifts out of sync. (A repo .gitignore rule for `.env`
// silently excluded a web/.env that tried to do this - the pipeline caught it.)
//
// A subpath deployment overrides it:  VITE_WEB_BASE=/quicklaunch/ npm run build
//
// `loadEnv` is used because Vite only injects VITE_* into import.meta.env for
// application code, not into this config file.
export default defineConfig(({ mode }) => {
  const env = { ...loadEnv(mode, process.cwd(), ''), ...process.env }

  return {
    base: env.VITE_WEB_BASE ?? './',
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

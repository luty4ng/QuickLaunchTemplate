// Minimal, security-first Electron shell.
//
// The renderer is the exact same bundle the web deployment serves. Two things
// differ, and both are handled here rather than in the UI:
//   * it is loaded from file://, so the app reads its API origin from
//     `QL_API_BASE` (build time) or the in-app "Server" setting (runtime);
//   * it gets no Node integration - only a tiny preload surface.
//
// `--ql-self-test` turns the app into its own integration test: boot, wait for
// React to mount, call the API from inside the renderer, print one JSON line and
// exit. That is how CI proves the packaged artifact works, not just that it
// built. See the `desktop-self-test` job in .github/workflows/pipeline.yml.

const { app, BrowserWindow, shell, Menu } = require('electron')
const path = require('node:path')
const fs = require('node:fs')

const WEB_ROOT = path.join(process.resourcesPath ?? '', 'web-dist')
const DEV_URL = process.env.QL_DEV_URL
const API_BASE = process.env.QL_API_BASE ?? ''
const SELF_TEST = process.argv.includes('--ql-self-test')

function resolveWebRoot() {
  // Packaged: resources/web-dist. Unpackaged (`npm start`): ./web-dist.
  const packaged = path.join(WEB_ROOT, 'index.html')
  if (fs.existsSync(packaged)) return WEB_ROOT
  return path.join(__dirname, 'web-dist')
}

function createWindow() {
  const window = new BrowserWindow({
    width: 980,
    height: 720,
    minWidth: 420,
    minHeight: 480,
    backgroundColor: '#0f1115',
    title: 'QuickLaunch',
    show: !SELF_TEST,
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      additionalArguments: [`--ql-api-base=${API_BASE}`],
    },
  })

  // External links open in the real browser, never inside the app shell.
  window.webContents.setWindowOpenHandler(({ url }) => {
    void shell.openExternal(url)
    return { action: 'deny' }
  })

  // A blank page or a dead preload must fail the build, not show up as a
  // white window in front of a user.
  window.webContents.on('render-process-gone', (_event, details) => {
    if (SELF_TEST) finish({ ok: false, reason: `renderer gone: ${details.reason}` })
  })
  window.webContents.on('did-fail-load', (_event, code, description) => {
    if (SELF_TEST) finish({ ok: false, reason: `load failed ${code}: ${description}` })
  })

  const loaded = DEV_URL ? window.loadURL(DEV_URL) : window.loadFile(path.join(resolveWebRoot(), 'index.html'))
  loaded.catch((error) => {
    if (SELF_TEST) finish({ ok: false, reason: `could not load the bundle: ${error.message}` })
  })

  if (SELF_TEST) void runSelfTest(window)
  return window
}

async function waitForMount(window, timeoutMs = 20000) {
  const deadline = Date.now() + timeoutMs
  while (Date.now() < deadline) {
    const mounted = await window.webContents.executeJavaScript(
      'document.getElementById("root")?.dataset.appReady === "true"',
    )
    if (mounted) return true
    await new Promise((resolve) => setTimeout(resolve, 250))
  }
  return false
}

async function runSelfTest(window) {
  const result = { ok: false, checks: {} }
  try {
    result.checks.bundleMounted = await waitForMount(window)
    if (!result.checks.bundleMounted) return finish({ ...result, reason: 'React never mounted' })

    // Proves the preload bridge survived packaging.
    result.checks.preloadBridge = await window.webContents.executeJavaScript(
      'typeof window.quicklaunch === "object" && typeof window.quicklaunch.defaultApiBase === "string"',
    )

    // Proves the renderer can reach the configured API - the thing that is
    // actually broken when a desktop build "opens but does nothing".
    result.checks.apiReachable = await window.webContents.executeJavaScript(`
      fetch(${JSON.stringify(`${API_BASE}/api/health`)})
        .then((r) => r.ok)
        .catch(() => false)
    `)

    result.ok = Object.values(result.checks).every(Boolean)
    if (!result.ok) result.reason = 'one or more checks failed'
  } catch (error) {
    result.reason = error.message
  }
  finish(result)
}

let finished = false
function finish(result) {
  if (finished) return
  finished = true
  console.log(`QL_SELF_TEST ${JSON.stringify(result)}`)
  app.exit(result.ok ? 0 : 1)
}

app.whenReady().then(() => {
  Menu.setApplicationMenu(null)
  createWindow()
  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow()
  })
})

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit()
})

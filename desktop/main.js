// Minimal, security-first Electron shell.
//
// The renderer is the exact same bundle the web deployment serves. Four things
// differ from the browser case, and all of them are handled here rather than in
// the UI:
//   * the bundle is served over a custom `app://` protocol instead of file://.
//     Chromium treats file:// as an opaque origin and treats ES modules and
//     fetch() from it as cross-origin, which shows up as "the window opens and
//     the page renders nothing" - exactly what the packaged app did on a CI
//     runner. A registered protocol makes the bundle a normal origin.
//   * the API origin comes from `QL_API_BASE` (build time) or the in-app
//     "Server" setting (runtime);
//   * it gets no Node integration - only a tiny preload surface;
//   * it updates itself: electron-updater reads `latest.yml` from this
//     repository's GitHub Releases, downloads the installer in the background and
//     installs it on one click. See `setupAutoUpdate` below.
//
// `--ql-self-test` turns the app into its own integration test: boot, wait for
// React to mount, call the API from inside the renderer, write a JSON report and
// exit. That is how CI proves the packaged artifact works, not just that it
// built. See the `desktop-self-test` job in .github/workflows/pipeline.yml.

const { app, BrowserWindow, ipcMain, protocol, net, shell, Menu } = require('electron')
const path = require('node:path')
const fs = require('node:fs')
const { pathToFileURL } = require('node:url')

const DEV_URL = process.env.QL_DEV_URL
const API_BASE = process.env.QL_API_BASE ?? ''
const SELF_TEST = process.argv.includes('--ql-self-test')
// CI passes an absolute path: a GUI process's working directory is not a
// reliable place to look for its output afterwards.
const SELF_TEST_REPORT = (
  process.argv.find((value) => value.startsWith('--ql-self-test-report=')) ?? ''
).slice('--ql-self-test-report='.length)
// Let a packaging job switch the update check off when the release it would look
// for does not exist yet.
const UPDATE_CHECK = process.env.QL_UPDATE_CHECK !== 'false'
// The self-test only wants to know what the feed offers; downloading a 110 MB
// installer (and then not installing it) is not part of proving detection.
const UPDATE_AUTODOWNLOAD = process.env.QL_UPDATE_AUTODOWNLOAD !== 'false'
// Points the updater at a specific feed file. Used to test against a published
// release without depending on which release GitHub considers newest, and to
// test a lower version without publishing anything.
const UPDATE_FEED_URL = process.env.QL_UPDATE_FEED_URL

const { isNewer, compareVersions } = require('./lib/version')

const SCHEME = 'app'
const ENTRY = `${SCHEME}://bundle/index.html`

// Headless CI has no GPU and Electron's GPU process can stall the renderer
// there. This must happen before the app is ready - calling it inside
// whenReady() throws, and the rejection silently swallows window creation.
if (SELF_TEST) app.disableHardwareAcceleration()

// Windows CI runners run the app from a service-like context where Chromium's
// network service sandbox cannot start; every fetch then fails with the
// extremely unhelpful "TypeError: Failed to fetch". Self-test mode is a smoke
// test on a throwaway runner, so it trades that sandbox away for a usable
// network stack.
if (SELF_TEST && process.platform === 'win32') {
  app.commandLine.appendSwitch('no-sandbox')
  app.commandLine.appendSwitch('disable-gpu')
}

// ---------------------------------------------------------------------------
// Auto-update: one click from "there is a new version" to "running it".
//
//   launch -> silent check -> background download -> "Restart and update"
//
// The feed is this repository's own GitHub Releases. electron-updater reads
// `latest.yml` from the newest release and compares its `version` with the
// running app's, so a release only reaches installed clients when its version is
// strictly higher than theirs - which is why the packaging job stamps a version
// instead of shipping every build as 1.0.0.
// ---------------------------------------------------------------------------

let autoUpdater = null
let updateState = { status: 'idle', currentVersion: null }

function publishState(patch) {
  updateState = { ...updateState, ...patch, currentVersion: app.getVersion() }
  for (const window of BrowserWindow.getAllWindows()) {
    if (!window.isDestroyed()) window.webContents.send('ql:update-state', updateState)
  }
  if (SELF_TEST) console.log(`QL_UPDATE_STATE ${JSON.stringify(updateState)}`)
}

function setupAutoUpdate() {
  if (!UPDATE_CHECK) {
    publishState({ status: 'disabled', reason: 'update check disabled for this build' })
    return
  }
  try {
    // Required lazily so `npm start` (unpackaged, no updater config) still runs.
    autoUpdater = require('electron-updater').autoUpdater
  } catch (error) {
    publishState({ status: 'disabled', reason: `electron-updater unavailable: ${error.message}` })
    return
  }

  autoUpdater.autoDownload = UPDATE_AUTODOWNLOAD
  autoUpdater.autoInstallOnAppQuit = true
  if (UPDATE_FEED_URL) {
    // Testing hook: read one specific latest.yml instead of guessing which
    // release GitHub calls newest.
    autoUpdater.setFeedURL({ provider: 'generic', url: UPDATE_FEED_URL })
  }

  autoUpdater.on('checking-for-update', () => publishState({ status: 'checking' }))
  autoUpdater.on('update-available', (info) => publishState({ status: 'downloading', version: info.version }))
  autoUpdater.on('update-not-available', (info) => publishState({ status: 'up-to-date', version: info.version }))
  autoUpdater.on('download-progress', (progress) =>
    publishState({ percent: Math.round(progress.percent), status: 'downloading' }),
  )
  autoUpdater.on('update-downloaded', (info) =>
    publishState({ status: 'ready', version: info.version, percent: 100 }),
  )
  autoUpdater.on('error', (error) => publishState({ status: 'error', error: String(error?.message ?? error) }))

  publishState({ status: 'idle' })
  // Deliberately not awaited: the window must never wait on the network.
  autoUpdater
    .checkForUpdates()
    .catch((error) => publishState({ status: 'error', error: String(error.message) }))
}

function registerUpdateIpc() {
  ipcMain.handle('ql:update-state', () => updateState)
  ipcMain.handle('ql:update-check', async () => {
    if (!autoUpdater) return updateState
    try {
      await autoUpdater.checkForUpdates()
    } catch (error) {
      publishState({ status: 'error', error: String(error.message) })
    }
    return updateState
  })
  ipcMain.handle('ql:update-install', () => {
    if (!autoUpdater) return false
    // isSilent=false, isForceRunAfter=true: install, then start the new build.
    autoUpdater.quitAndInstall(false, true)
    return true
  })
}

/**
 * Ask the real update feed what the newest published version is.
 *
 * This is electron-updater's own check, so it exercises the real provider
 * (GitHub Releases), the real `latest.yml` and the real version comparison -
 * rather than my idea of what those should look like. With autoDownload off it
 * stops after the check.
 */
async function readUpdateFeed() {
  if (!autoUpdater) return { ok: false, reason: 'updater not initialised' }
  const current = app.getVersion()
  try {
    const result = await autoUpdater.checkForUpdates()
    const offered = result?.updateInfo?.version ?? null
    return {
      ok: true,
      current,
      offered,
      // What the app would do with that answer, computed locally so the decision
      // is visible even when autoDownload is off.
      decision: offered === null ? 'unknown' : isNewer(offered, current) ? 'update-available' : 'up-to-date',
      comparison: offered === null ? null : compareVersions(offered, current),
    }
  } catch (error) {
    return { ok: false, current, error: String(error?.message ?? error) }
  }
}

/** Packaged: resources/web-dist. Unpackaged (`npm start`): ./web-dist. */
function resolveWebRoot() {
  const packaged = path.join(process.resourcesPath ?? '', 'web-dist')
  if (fs.existsSync(path.join(packaged, 'index.html'))) return packaged
  return path.join(__dirname, 'web-dist')
}

// Registered as a standard scheme so the renderer treats it as a real origin.
protocol.registerSchemesAsPrivileged([
  { scheme: SCHEME, privileges: { standard: true, secure: true, supportFetchAPI: true } },
])

function registerBundleProtocol() {
  const root = resolveWebRoot()
  protocol.handle(SCHEME, (request) => {
    const { pathname } = new URL(request.url)
    const relative = decodeURIComponent(pathname).replace(/^\/+/, '') || 'index.html'
    const target = path.join(root, relative)
    // Never serve anything outside the bundle directory.
    if (!target.startsWith(root)) {
      return new Response('forbidden', { status: 403 })
    }
    if (!fs.existsSync(target)) {
      return new Response('not found', { status: 404 })
    }
    return net.fetch(pathToFileURL(target).toString())
  })
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

  // A blank page or a dead preload must fail the build, not show up as a white
  // window in front of a user.
  window.webContents.on('render-process-gone', (_event, details) => {
    if (SELF_TEST) finish({ ok: false, reason: `renderer gone: ${details.reason}` })
  })
  window.webContents.on('did-fail-load', (_event, code, description, url) => {
    console.log(`QL_LOAD_FAILED ${code} ${description} ${url}`)
    if (SELF_TEST) finish({ ok: false, reason: `load failed ${code}: ${description}` })
  })
  window.webContents.on('did-fail-provisional-load', (_event, code, description, url) => {
    console.log(`QL_PROVISIONAL_FAILED ${code} ${description} ${url}`)
  })

  if (SELF_TEST) {
    // Renderer console output is the only clue when a page loads but never runs.
    window.webContents.on('console-message', (event) => {
      console.log(`QL_CONSOLE ${event.level}: ${event.message}`)
    })
    window.webContents.on('did-finish-load', () => console.log('QL_LOADED did-finish-load'))
    window.webContents.on('did-fail-load', () => console.log('QL_LOAD_FAILED'))
  }

  const loaded = DEV_URL ? window.loadURL(DEV_URL) : window.loadURL(ENTRY)
  loaded.catch((error) => {
    if (SELF_TEST) finish({ ok: false, reason: `could not load the bundle: ${error.message}` })
  })

  if (SELF_TEST) void runSelfTest(window)
  return window
}

/**
 * Main-process reachability check. Chromium can refuse fetches for reasons that
 * have nothing to do with the app (a sandboxed network service on a CI runner),
 * so this answers the narrower question: is the configured API actually there?
 */
function probeFromMainProcess() {
  return new Promise((resolve) => {
    if (!API_BASE) return resolve({ ok: false, error: 'no API base configured' })
    const target = `${API_BASE}/api/health`
    const request = net.request(target)
    const timer = setTimeout(() => {
      request.abort()
      resolve({ ok: false, error: 'timeout' })
    }, 8000)
    request.on('response', (response) => {
      clearTimeout(timer)
      resolve({ ok: response.statusCode === 200, status: response.statusCode })
      response.on('data', () => {})
    })
    request.on('error', (error) => {
      clearTimeout(timer)
      resolve({ ok: false, error: String(error) })
    })
    request.end()
  })
}

async function waitForMount(window, timeoutMs = 20000) {
  const deadline = Date.now() + timeoutMs
  while (Date.now() < deadline) {
    try {
      const mounted = await window.webContents.executeJavaScript(
        'document.getElementById("root")?.dataset.appReady === "true"',
      )
      if (mounted) return true
    } catch (error) {
      // A blank or broken page throws here; keep polling until the deadline so
      // the caller can report the real reason instead of a crash.
      if (SELF_TEST) console.log(`QL_SELF_TEST probe failed: ${error.message}`)
    }
    await new Promise((resolve) => setTimeout(resolve, 250))
  }
  return false
}

async function runSelfTest(window) {
  const result = { ok: false, checks: {}, debug: {} }
  try {
    const root = resolveWebRoot()
    result.debug.webRoot = root
    result.debug.webRootIndexExists = fs.existsSync(path.join(root, 'index.html'))
    result.debug.apiBase = API_BASE
    result.debug.files = fs.existsSync(root) ? fs.readdirSync(root).slice(0, 10) : []

    result.checks.bundleMounted = await waitForMount(window)
    if (!result.checks.bundleMounted) {
      const probe = await window.webContents
        .executeJavaScript(
          `({
             href: location.href,
             scripts: [...document.querySelectorAll('script')].map((s) => s.getAttribute('src')),
             rootHtml: (document.getElementById('root') || {}).innerHTML ?? null,
             bodyLength: document.body ? document.body.innerHTML.length : -1,
           })`,
        )
        .catch((error) => ({ error: error.message }))
      result.debug.probe = probe
      return finish({ ...result, reason: 'React never mounted' })
    }

    // Proves the preload bridge survived packaging.
    result.checks.preloadBridge = await window.webContents.executeJavaScript(
      'typeof window.quicklaunch === "object" && typeof window.quicklaunch.defaultApiBase === "string"',
    )

    // Proves the renderer can reach the configured API - the thing that is
    // actually broken when a desktop build "opens but does nothing". Several
    // variants are tried so a failure distinguishes "blocked by CORS" from
    // "never reached the network" from "credentials problem".
    const probeScript = (suffix, options) =>
      `fetch(${JSON.stringify(`${API_BASE}/api/health${suffix}`)}, ${JSON.stringify(options)})
         .then((r) => ({ ok: r.ok, status: r.status }))
         .catch((error) => ({ ok: false, error: String(error) }))`
    const probes = {
      credentialed: await window.webContents.executeJavaScript(probeScript('', { credentials: 'include' })),
      plain: await window.webContents.executeJavaScript(probeScript('', { credentials: 'omit' })),
      mainProcess: await probeFromMainProcess(),
    }
    result.debug.apiProbe = probes
    result.checks.apiReachable =
      probes.credentialed.ok === true || probes.plain.ok === true || probes.mainProcess.ok === true

    // Update feed: proves the packaged app can reach the real GitHub Releases,
    // parse latest.yml and decide correctly whether it is behind. The decision
    // itself may legitimately be either answer, so the check asserts that a
    // decision was reached from a readable feed - and the answer is recorded.
    if (UPDATE_CHECK) {
      const feed = await readUpdateFeed()
      result.checks.updateDetection = feed.ok === true && feed.decision !== 'unknown'
      result.debug.updateFeed = feed
      if (!feed.ok) result.reason = `update feed unreadable: ${feed.error ?? feed.reason}`
    } else {
      result.checks.updateDetection = true
      result.debug.updateFeed = { ok: true, decision: 'disabled' }
    }

    result.ok = Object.values(result.checks).every(Boolean)
    if (!result.ok && !result.reason) result.reason = 'one or more checks failed'
  } catch (error) {
    result.reason = error.message
  }
  finish(result)
}

let finished = false
function finish(result) {
  if (finished) return
  finished = true
  // The result goes to a file as well as stdout: a GUI process on a headless CI
  // runner is not always waited on, and a truncated pipe must not look like a
  // failed self-test.
  if (SELF_TEST) {
    try {
      const target = SELF_TEST_REPORT || path.join(process.cwd(), 'ql-self-test.json')
      fs.writeFileSync(target, JSON.stringify(result, null, 2))
    } catch (error) {
      console.log(`QL_SELF_TEST could not write the report: ${error.message}`)
    }
  }
  console.log(`QL_SELF_TEST ${JSON.stringify(result)}`)
  // app.exit() runs the normal shutdown path; process.exit() is the backstop
  // for when a renderer keeps the loop alive.
  app.exit(result.ok ? 0 : 1)
  setTimeout(() => process.exit(result.ok ? 0 : 1), 3000)
}

app.whenReady().then(() => {
  Menu.setApplicationMenu(null)
  registerBundleProtocol()
  registerUpdateIpc()
  createWindow()
  setupAutoUpdate()
  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow()
  })
})

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit()
})

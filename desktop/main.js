// Minimal, security-first Electron shell.
//
// The renderer is the exact same bundle the web deployment serves. Two things
// differ, and both are handled here rather than in the UI:
//   * it is loaded from file://, so the app reads its API origin from
//     `QL_API_BASE` (build time) or the in-app "Server" setting (runtime);
//   * it gets no Node integration - only a tiny preload surface.

const { app, BrowserWindow, shell, Menu } = require('electron')
const path = require('node:path')
const fs = require('node:fs')

const WEB_ROOT = path.join(process.resourcesPath ?? '', 'web-dist')
const DEV_URL = process.env.QL_DEV_URL
const API_BASE = process.env.QL_API_BASE ?? ''

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

  if (DEV_URL) {
    void window.loadURL(DEV_URL)
  } else {
    void window.loadFile(path.join(resolveWebRoot(), 'index.html'))
  }
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

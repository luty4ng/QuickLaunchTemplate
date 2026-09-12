// The only bridge between the shell and the page.
//
// The web client can read its API origin from localStorage, but a freshly
// installed desktop build has nothing stored yet, so the shell seeds it from the
// compiled-in default on first run. It also exposes the update surface: read
// state, ask again, install. No filesystem, no shell, no arbitrary IPC.
const { contextBridge, ipcRenderer } = require('electron')

function readApiBase() {
  const arg = process.argv.find((value) => value.startsWith('--ql-api-base='))
  return arg ? arg.slice('--ql-api-base='.length) : ''
}

contextBridge.exposeInMainWorld('quicklaunch', {
  platform: process.platform,
  defaultApiBase: readApiBase(),
  updates: {
    /** Current update state: { status, version?, percent?, error? }. */
    getState: () => ipcRenderer.invoke('ql:update-state'),
    /** Ask the feed again, e.g. from a "Check for updates" button. */
    check: () => ipcRenderer.invoke('ql:update-check'),
    /** Install the downloaded update and restart. */
    install: () => ipcRenderer.invoke('ql:update-install'),
    /** Subscribe to state changes. Returns an unsubscribe function. */
    onChange: (listener) => {
      const handler = (_event, state) => listener(state)
      ipcRenderer.on('ql:update-state', handler)
      return () => ipcRenderer.removeListener('ql:update-state', handler)
    },
  },
})

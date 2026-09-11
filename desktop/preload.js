// The only bridge between the shell and the page.
//
// The web client can read its API origin from localStorage, but a freshly
// installed desktop build has nothing stored yet, so the shell seeds it from
// the compiled-in default on first run. No filesystem, no shell, no IPC surface.
const { contextBridge } = require('electron')

function readApiBase() {
  const arg = process.argv.find((value) => value.startsWith('--ql-api-base='))
  return arg ? arg.slice('--ql-api-base='.length) : ''
}

contextBridge.exposeInMainWorld('quicklaunch', {
  platform: process.platform,
  defaultApiBase: readApiBase(),
})

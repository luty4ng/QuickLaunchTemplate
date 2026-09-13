import { useEffect, useState } from 'react'

import { updateBridge, type UpdateState } from '../api/core'

/**
 * Update banner for the desktop build.
 *
 * The shell checks the update feed on launch and downloads in the background, so
 * by the time anyone looks at this the work is usually done and the only thing
 * left is one click. Renders nothing at all on the web and Android targets, where
 * there is no update bridge.
 */
export function UpdateBanner() {
  const bridge = updateBridge()
  const [state, setState] = useState<UpdateState | null>(null)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    if (!bridge) return
    let alive = true
    void bridge.getState().then((current) => {
      if (alive) setState(current)
    })
    const unsubscribe = bridge.onChange((next) => {
      if (alive) setState(next)
    })
    return () => {
      alive = false
      unsubscribe()
    }
  }, [bridge])

  if (!bridge || !state) return null

  const check = async () => {
    setBusy(true)
    try {
      setState(await bridge.check())
    } finally {
      setBusy(false)
    }
  }

  const install = () => {
    void bridge.install()
  }

  return (
    <div className="update" role="status">
      <span className="update-text">{describe(state)}</span>

      {state.status === 'ready' && (
        <button onClick={install}>
          Restart and update{state.version ? ` to ${state.version}` : ''}
        </button>
      )}

      {(state.status === 'up-to-date' || state.status === 'error' || state.status === 'disabled') && (
        <button className="secondary" onClick={check} disabled={busy || state.status === 'disabled'}>
          {busy ? 'Checking...' : 'Check for updates'}
        </button>
      )}

      {state.status === 'idle' && (
        <button className="secondary" onClick={check} disabled={busy}>
          {busy ? 'Checking...' : 'Check for updates'}
        </button>
      )}
    </div>
  )
}

function describe(state: UpdateState): string {
  const current = state.currentVersion ? ` (you have ${state.currentVersion})` : ''
  switch (state.status) {
    case 'idle':
      return `Updates ready to check${current}`
    case 'checking':
      return 'Checking for updates...'
    case 'up-to-date':
      return `You are up to date${current}`
    case 'downloading':
      return state.version
        ? `Downloading ${state.version}... ${state.percent ?? 0}%`
        : `Downloading update... ${state.percent ?? 0}%`
    case 'ready':
      return `Update ${state.version ?? ''} is downloaded and ready`.trim()
    case 'error':
      return `Update check failed: ${state.error ?? 'unknown error'}`
    case 'disabled':
      return `Updates are disabled in this build${state.reason ? `: ${state.reason}` : ''}`
    default:
      return ''
  }
}

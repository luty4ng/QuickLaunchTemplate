import { useCallback, useEffect, useState } from 'react'

import { api, describeError, getApiBase, setApiBase, type User } from './api/core'
import { AuthPanel } from './components/AuthPanel'
import { UpdateBanner } from './components/UpdateBanner'
import { FEATURES } from './features'

/**
 * The app shell. It owns the session, the health badge and the error line, and
 * knows nothing about what the signed-in panels do: it renders the feature
 * registry and hands each panel a way to report back.
 */
type Health = { state: 'checking' | 'ok' | 'down'; version?: string }

/**
 * The display name, as one literal on purpose: a bundle cannot read project.env
 * at runtime, so `scripts/project_env.py bootstrap --write` rewrites this line
 * and `check` fails when it drifts from the file (see MIGRATION.md §1).
 */
export const APP_NAME = 'QuickLaunch'

export function App() {
  const [user, setUser] = useState<User | null>(null)
  const [booting, setBooting] = useState(true)
  const [health, setHealth] = useState<Health>({ state: 'checking' })
  const [error, setError] = useState<string | null>(null)
  const [apiBase, setApiBaseState] = useState(getApiBase())
  const [showSettings, setShowSettings] = useState(false)

  useEffect(() => {
    let cancelled = false
    void (async () => {
      try {
        const status = await api.health()
        if (!cancelled) setHealth({ state: status.status === 'ok' ? 'ok' : 'down', version: status.version })
      } catch {
        if (!cancelled) setHealth({ state: 'down' })
      }
      try {
        const me = await api.me()
        if (!cancelled) setUser(me)
      } catch {
        // Not signed in yet - that is the normal first-run state.
      } finally {
        // The panels load their own data once they mount.
        if (!cancelled) setBooting(false)
      }
    })()
    return () => {
      cancelled = true
    }
  }, [])

  const signIn = async (mode: 'login' | 'register', email: string, password: string) => {
    setError(null)
    try {
      setUser(mode === 'login' ? await api.login(email, password) : await api.register(email, password))
    } catch (cause) {
      setError(describeError(cause))
    }
  }

  const signOut = async () => {
    setError(null)
    try {
      await api.logout()
    } catch {
      // Even if the call fails the local state must not claim a session.
    }
    setUser(null)
  }

  /** A panel saw a 401, so the session is gone. Its own state dies with it. */
  const handleUnauthorized = useCallback(() => {
    setError(null)
    setUser(null)
  }, [])

  const applyApiBase = (value: string) => {
    setApiBase(value)
    setApiBaseState(getApiBase())
    window.location.reload()
  }

  return (
    <div className="app">
      <header className="header">
        <h1 className="brand">
          <Brand name={APP_NAME} />
        </h1>
        {user && (
          <div className="row">
            <span className="muted">{user.email}</span>
            <button className="secondary" onClick={signOut}>
              Sign out
            </button>
          </div>
        )}
      </header>

      {error && <p className="error">{error}</p>}

      <UpdateBanner />

      {booting ? (
        <div className="card empty">Loading...</div>
      ) : user ? (
        // One plain wrapper per feature: the shell owns the slot and its order,
        // the feature owns everything inside. `data-feature` makes a registered
        // panel assertable without the test knowing which features exist.
        FEATURES.map(({ id, Panel }) => (
          <div key={id} data-feature={id}>
            <Panel onError={setError} onUnauthorized={handleUnauthorized} />
          </div>
        ))
      ) : (
        <AuthPanel onSubmit={signIn} />
      )}

      <footer className="footer">
        <div className="row" style={{ justifyContent: 'space-between' }}>
          <span className="badge">
            <span className={`dot ${health.state === 'ok' ? 'ok' : health.state === 'down' ? 'down' : ''}`} />
            API {health.state === 'checking' ? 'checking' : health.state}
            {health.version ? ` v${health.version}` : ''}
          </span>
          <button className="ghost" onClick={() => setShowSettings((open) => !open)}>
            {showSettings ? 'Hide' : 'Server'}
          </button>
        </div>

        {showSettings && (
          <ApiBaseSetting current={apiBase} onApply={applyApiBase} />
        )}
      </footer>
    </div>
  )
}

/**
 * The brand, with the accent colour on the last word (`.brand span` in
 * styles.css). A one-word name is split on its camel-case boundary, which is how
 * this template ships it: Quick|Launch.
 */
function Brand({ name }: { name: string }) {
  const spaced = name.includes(' ')
  const words = (spaced ? name.split(' ') : name.split(/(?=[A-Z])/)).filter(Boolean)
  const tail = words.pop() ?? name

  return (
    <>
      {words.join(spaced ? ' ' : '')}
      {spaced && words.length > 0 ? ' ' : null}
      <span>{tail}</span>
    </>
  )
}

/**
 * The desktop and Android builds ship a compiled default API origin. This lets
 * an installed app be repointed without shipping a new build - the reason the
 * base URL lives in localStorage rather than only in the bundle.
 */
function ApiBaseSetting({ current, onApply }: { current: string; onApply: (value: string) => void }) {
  const [value, setValue] = useState(current)

  return (
    <form
      className="stack"
      onSubmit={(event) => {
        event.preventDefault()
        onApply(value)
      }}
    >
      <div>
        <label htmlFor="api-base">API base URL (empty = same origin)</label>
        <input
          id="api-base"
          type="url"
          placeholder="https://api.example.com"
          value={value}
          onChange={(event) => setValue(event.target.value)}
        />
      </div>
      <button type="submit" className="secondary">
        Save and reload
      </button>
    </form>
  )
}

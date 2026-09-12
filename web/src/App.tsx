import { useCallback, useEffect, useState } from 'react'

import {
  ApiError,
  api,
  getApiBase,
  setApiBase,
  type BillingMe,
  type Todo,
  type User,
} from './api'
import { AuthPanel } from './components/AuthPanel'
import { PlanPanel } from './components/PlanPanel'
import { TodoList } from './components/TodoList'
import { UpdateBanner } from './components/UpdateBanner'

type Health = { state: 'checking' | 'ok' | 'down'; version?: string }

export function App() {
  const [user, setUser] = useState<User | null>(null)
  const [booting, setBooting] = useState(true)
  const [health, setHealth] = useState<Health>({ state: 'checking' })
  const [todos, setTodos] = useState<Todo[]>([])
  const [billing, setBilling] = useState<BillingMe | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [apiBase, setApiBaseState] = useState(getApiBase())
  const [showSettings, setShowSettings] = useState(false)

  const describe = (cause: unknown): string =>
    cause instanceof ApiError ? cause.message : 'Could not reach the server.'

  /** Loads todos for the signed-in user; a 401 simply means "signed out". */
  const refreshTodos = useCallback(async () => {
    try {
      setTodos(await api.listTodos())
    } catch (cause) {
      if (cause instanceof ApiError && cause.status === 401) {
        setUser(null)
        setTodos([])
        return
      }
      setError(describe(cause))
    }
  }, [])

  const refreshBilling = useCallback(async () => {
    try {
      setBilling(await api.billingMe())
    } catch (cause) {
      // Billing is optional: an unconfigured server answers 503, and the rest of
      // the app must keep working.
      if (!(cause instanceof ApiError && cause.status === 401)) setBilling(null)
    }
  }, [])

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
        if (cancelled) return
        setUser(me)
        await refreshTodos()
        await refreshBilling()
        // Returning from the provider: the plan is granted by a webhook, which
        // may land a moment after the redirect, so re-check a few times instead
        // of assuming it already happened.
        if (window.location.search.includes('billing=success')) {
          for (let attempt = 0; attempt < 5 && !cancelled; attempt += 1) {
            await new Promise((resolve) => setTimeout(resolve, 1500))
            await refreshBilling()
          }
        }
      } catch {
        // Not signed in yet - that is the normal first-run state.
      } finally {
        if (!cancelled) setBooting(false)
      }
    })()
    return () => {
      cancelled = true
    }
  }, [refreshTodos, refreshBilling])

  const signIn = async (mode: 'login' | 'register', email: string, password: string) => {
    setError(null)
    try {
      const me = mode === 'login' ? await api.login(email, password) : await api.register(email, password)
      setUser(me)
      await refreshTodos()
      await refreshBilling()
    } catch (cause) {
      setError(describe(cause))
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
    setTodos([])
    setBilling(null)
  }

  const addTodo = async (title: string) => {
    setError(null)
    try {
      const created = await api.createTodo(title)
      setTodos((current) => [created, ...current])
      // The quota moved, so the "3 of 10" line and the upgrade prompt must too.
      await refreshBilling()
    } catch (cause) {
      setError(describe(cause))
      if (cause instanceof ApiError && cause.status === 402) await refreshBilling()
    }
  }

  const patchTodo = async (id: string, patch: { title?: string; done?: boolean }) => {
    setError(null)
    try {
      const updated = await api.updateTodo(id, patch)
      setTodos((current) => current.map((todo) => (todo.id === id ? updated : todo)))
    } catch (cause) {
      setError(describe(cause))
    }
  }

  const removeTodo = async (id: string) => {
    setError(null)
    try {
      await api.deleteTodo(id)
      setTodos((current) => current.filter((todo) => todo.id !== id))
      await refreshBilling()
    } catch (cause) {
      setError(describe(cause))
    }
  }

  const applyApiBase = (value: string) => {
    setApiBase(value)
    setApiBaseState(getApiBase())
    window.location.reload()
  }

  const remaining = todos.filter((todo) => !todo.done).length

  return (
    <div className="app">
      <header className="header">
        <h1 className="brand">
          Quick<span>Launch</span>
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
        <>
          <div className="card">
            <PlanPanel billing={billing} onRefresh={refreshBilling} />
          </div>
          <div className="card">
            <TodoList
              todos={todos}
              remaining={remaining}
              onCreate={addTodo}
              onPatch={patchTodo}
              onDelete={removeTodo}
            />
          </div>
        </>
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

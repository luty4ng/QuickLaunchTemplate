/**
 * Typed API client.
 *
 * Three targeting rules live here and nowhere else:
 *  1. Same-origin by default (`VITE_API_BASE` empty). The web build is served by
 *     FastAPI itself, so cookies are first-party and there is no CORS at all.
 *  2. The desktop build bakes in a default remote origin, and can be pointed at
 *     a different one at runtime (see `setApiBase`) - an installed app must not
 *     need a rebuild to talk to a different server.
 *  3. The Android build bakes in its own origin; the backend allows it via CORS.
 */

export type Todo = {
  id: string
  title: string
  done: boolean
  created_at: string
  updated_at: string
}

export type User = { id: string; email: string }

export type ApiErrorBody = { error: { code: string; message: string } }

export class ApiError extends Error {
  readonly status: number
  readonly code: string

  constructor(status: number, code: string, message: string) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.code = code
  }
}

const RUNTIME_OVERRIDE_KEY = 'quicklaunch.apiBase'

/** Injected by the Electron preload script; absent on web and Android. */
type ShellBridge = { platform?: string; defaultApiBase?: string }
declare global {
  interface Window {
    quicklaunch?: ShellBridge
  }
}

function compiledBase(): string {
  // Injected by Vite at build time. `import.meta.env` is replaced statically,
  // so this stays a plain string in the bundle.
  return import.meta.env.VITE_API_BASE ?? ''
}

function seedFromShell(): void {
  // A freshly installed desktop build has an empty localStorage, so the shell
  // hands over its compiled-in default exactly once.
  const shell = typeof window === 'undefined' ? undefined : window.quicklaunch
  if (!shell?.defaultApiBase) return
  if (typeof localStorage === 'undefined') return
  if (localStorage.getItem(RUNTIME_OVERRIDE_KEY)) return
  localStorage.setItem(RUNTIME_OVERRIDE_KEY, shell.defaultApiBase.replace(/\/+$/, ''))
}

export function getApiBase(): string {
  if (typeof localStorage !== 'undefined') {
    const stored = localStorage.getItem(RUNTIME_OVERRIDE_KEY)
    if (stored) return stored.replace(/\/+$/, '')
  }
  seedFromShell()
  const stored = typeof localStorage === 'undefined' ? null : localStorage.getItem(RUNTIME_OVERRIDE_KEY)
  if (stored) return stored.replace(/\/+$/, '')
  return compiledBase().replace(/\/+$/, '')
}

export function setApiBase(base: string): void {
  if (typeof localStorage === 'undefined') return
  const trimmed = base.trim().replace(/\/+$/, '')
  if (trimmed) localStorage.setItem(RUNTIME_OVERRIDE_KEY, trimmed)
  else localStorage.removeItem(RUNTIME_OVERRIDE_KEY)
}

export function apiUrl(path: string): string {
  return `${getApiBase()}${path}`
}

const DEFAULT_MESSAGES: Record<number, string> = {
  401: 'Please sign in again.',
  500: 'The server hit an unexpected error.',
}

async function toApiError(response: Response): Promise<ApiError> {
  let code = 'http_error'
  let message = DEFAULT_MESSAGES[response.status] ?? `Request failed (${response.status}).`
  try {
    const body = (await response.json()) as Partial<ApiErrorBody> & { detail?: unknown }
    if (body?.error?.code) {
      code = body.error.code
      message = body.error.message || message
    } else if (response.status === 422) {
      // FastAPI's own schema-validation shape; keep the distinction visible.
      code = 'validation_error'
      message = 'Please check the values you entered.'
    }
  } catch {
    // Non-JSON body (proxy error, gateway HTML...): keep the status text.
  }
  return new ApiError(response.status, code, message)
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(apiUrl(path), {
    ...init,
    // Session lives in an httpOnly cookie, so every call must carry it.
    credentials: 'include',
    headers: {
      ...(init.body ? { 'Content-Type': 'application/json' } : {}),
      ...init.headers,
    },
  })

  if (!response.ok) throw await toApiError(response)
  if (response.status === 204) return undefined as T
  return (await response.json()) as T
}

export const api = {
  health: () => request<{ status: string; database: string; version: string }>('/api/health'),

  // GATE CHECK: deliberate type error. `number` is not assignable to `string`.
  label: (): string => 42,

  me: () => request<User>('/api/auth/me'),
  register: (email: string, password: string) =>
    request<User>('/api/auth/register', { method: 'POST', body: JSON.stringify({ email, password }) }),
  login: (email: string, password: string) =>
    request<User>('/api/auth/login', { method: 'POST', body: JSON.stringify({ email, password }) }),
  logout: () => request<void>('/api/auth/logout', { method: 'POST' }),

  listTodos: () => request<Todo[]>('/api/todos'),
  createTodo: (title: string) =>
    request<Todo>('/api/todos', { method: 'POST', body: JSON.stringify({ title }) }),
  updateTodo: (id: string, patch: { title?: string; done?: boolean }) =>
    request<Todo>(`/api/todos/${encodeURIComponent(id)}`, {
      method: 'PATCH',
      body: JSON.stringify(patch),
    }),
  deleteTodo: (id: string) => request<void>(`/api/todos/${encodeURIComponent(id)}`, { method: 'DELETE' }),
}

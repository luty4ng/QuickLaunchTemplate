import { useState } from 'react'

type Mode = 'login' | 'register'

export function AuthPanel({
  onSubmit,
}: {
  onSubmit: (mode: Mode, email: string, password: string) => Promise<void>
}) {
  const [mode, setMode] = useState<Mode>('login')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [busy, setBusy] = useState(false)

  const submit = async (event: React.FormEvent) => {
    event.preventDefault()
    setBusy(true)
    try {
      await onSubmit(mode, email, password)
    } finally {
      setBusy(false)
    }
  }

  return (
    <form className="card stack" onSubmit={submit}>
      <div className="tabs">
        <button
          type="button"
          aria-pressed={mode === 'login'}
          onClick={() => setMode('login')}
        >
          Sign in
        </button>
        <button
          type="button"
          aria-pressed={mode === 'register'}
          onClick={() => setMode('register')}
        >
          Create account
        </button>
      </div>

      <div>
        <label htmlFor="email">Email</label>
        <input
          id="email"
          type="email"
          autoComplete="email"
          required
          value={email}
          onChange={(event) => setEmail(event.target.value)}
        />
      </div>

      <div>
        <label htmlFor="password">Password (min 8 characters)</label>
        <input
          id="password"
          type="password"
          autoComplete={mode === 'login' ? 'current-password' : 'new-password'}
          minLength={8}
          required
          value={password}
          onChange={(event) => setPassword(event.target.value)}
        />
      </div>

      <button type="submit" disabled={busy}>
        {busy ? 'Working...' : mode === 'login' ? 'Sign in' : 'Create account'}
      </button>
    </form>
  )
}

import { act, type ReactElement } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { App, APP_NAME } from './App'
import { FEATURES } from './features'

/**
 * The shell, rendered for real.
 *
 * This is the test that survives a migration: it never names a feature, it
 * asserts that whatever `src/features/index.ts` registers actually mounts. A
 * registry that typechecks but renders nothing is exactly the failure mode the
 * split introduces, and only a render catches it.
 */

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true

const HEALTH = { status: 'ok', database: 'ok', version: '9.9.9' }
const USER = { id: 'u1', email: 'me@example.test' }
/** The shape every feature sees; a real one comes from the server. */
const BILLING = {
  plan: 'free',
  quota: { plan: 'free', limit: 10, used: 0, remaining: 10, can_create: true },
  status: null,
  current_period_end: null,
  cancel_at_period_end: false,
  has_customer: false,
  plans: [],
  billing_enabled: false,
}

const mounted: { container: HTMLElement; unmount: () => Promise<void> }[] = []

function stubApi(routes: Record<string, unknown>) {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL) => {
      const path = new URL(String(input), 'http://localhost').pathname
      if (!(path in routes)) {
        return new Response(JSON.stringify({ error: { code: 'not_found', message: path } }), {
          status: 404,
          headers: { 'Content-Type': 'application/json' },
        })
      }
      return new Response(JSON.stringify(routes[path]), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      })
    }),
  )
}

async function mount(element: ReactElement): Promise<HTMLElement> {
  const container = document.createElement('div')
  document.body.append(container)
  const root = createRoot(container)
  await act(async () => {
    root.render(element)
  })
  mounted.push({
    container,
    unmount: async () => {
      await act(async () => {
        root.unmount()
      })
      container.remove()
    },
  })
  return container
}

const signedIn = { '/api/health': HEALTH, '/api/auth/me': USER, '/api/billing/me': BILLING }

beforeEach(() => localStorage.clear())

afterEach(async () => {
  for (const entry of mounted.splice(0)) await entry.unmount()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
  localStorage.clear()
  delete window.quicklaunch
})

describe('app shell', () => {
  it('asks a signed-out visitor to sign in and shows no feature slot', async () => {
    stubApi({ '/api/health': HEALTH })

    const view = await mount(<App />)

    expect(view.querySelector('input[type="email"]')).not.toBeNull()
    expect(view.querySelectorAll('[data-feature]')).toHaveLength(0)
  })

  it('renders the session chrome and one slot per registered feature', async () => {
    stubApi(signedIn)

    const view = await mount(<App />)

    expect(view.textContent).toContain(USER.email)
    expect(view.textContent).toContain('API ok v9.9.9')
    // The brand is one rewritable literal, and the accent lives on a span inside
    // it - a split that a careless rename would silently lose.
    const brand = view.querySelector('.brand')
    expect(brand?.textContent).toBe(APP_NAME)
    expect(brand?.querySelector('span')).not.toBeNull()
    expect([...view.querySelectorAll('[data-feature]')].map((slot) => slot.getAttribute('data-feature'))).toEqual(
      FEATURES.map((feature) => feature.id),
    )
  })

  it('mounts every registered panel without throwing', async () => {
    stubApi(signedIn)
    const noop = () => {}

    for (const feature of FEATURES) {
      await expect(
        mount(<feature.Panel onError={noop} onUnauthorized={noop} />),
      ).resolves.toBeInstanceOf(HTMLElement)
    }
  })
})

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { ApiError, api } from './api'

/**
 * Billing calls from the client.
 *
 * The assertion that matters is what goes over the wire: the client must send a
 * plan NAME and never a price, because the price decides what the user is
 * charged and only the server may choose it.
 */

type Call = { url: string; init: RequestInit }

function stubFetch(handler: (url: string, init: RequestInit) => Response) {
  const calls: Call[] = []
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const call = { url: String(input), init: init ?? {} }
      calls.push(call)
      return handler(call.url, call.init)
    }),
  )
  return calls
}

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })

const ME = {
  plan: 'free',
  quota: { plan: 'free', limit: 10, used: 3, remaining: 7, can_create: true },
  status: null,
  current_period_end: null,
  cancel_at_period_end: false,
  has_customer: false,
  plans: [
    { id: 'plus', name: 'Plus', limit: 200, price_id: 'price_plus', available: true },
    { id: 'pro', name: 'Pro', limit: null, price_id: 'price_pro', available: true },
  ],
  billing_enabled: true,
}

describe('billing client', () => {
  beforeEach(() => localStorage.clear())
  afterEach(() => {
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
    localStorage.clear()
  })

  it('reads the plan, quota and purchasable plans', async () => {
    stubFetch(() => json(ME))

    const billing = await api.billingMe()

    expect(billing.plan).toBe('free')
    expect(billing.quota.limit).toBe(10)
    expect(billing.plans.map((plan) => plan.id)).toEqual(['plus', 'pro'])
  })

  it('sends a plan name to checkout, never a price', async () => {
    const calls = stubFetch(() => json({ url: 'https://checkout.example.test/session' }))

    await api.startCheckout('plus')

    const body = JSON.parse(String(calls[0]?.init.body))
    expect(body).toEqual({ plan: 'plus' })
    // A price id here would let the caller decide what they pay.
    expect(JSON.stringify(body)).not.toContain('price_')
    expect(calls[0]?.init.method).toBe('POST')
  })

  it('carries the session cookie when starting a checkout', async () => {
    const calls = stubFetch(() => json({ url: 'https://checkout.example.test/session' }))

    await api.startCheckout('pro')

    expect(calls[0]?.init.credentials).toBe('include')
  })

  it('surfaces a 503 as a typed error so the UI can explain itself', async () => {
    stubFetch(() =>
      json({ error: { code: 'billing_unavailable', message: 'Billing is not configured.' } }, 503),
    )

    const error = (await api.startCheckout('plus').catch((cause: unknown) => cause)) as ApiError

    expect(error).toBeInstanceOf(ApiError)
    expect(error.status).toBe(503)
    expect(error.code).toBe('billing_unavailable')
  })

  it('exposes the quota error code the UI keys off', async () => {
    stubFetch(() =>
      json({ error: { code: 'quota_exceeded', message: 'The free plan allows 10 todos.' } }, 402),
    )

    const error = (await api.createTodo('one too many').catch((cause: unknown) => cause)) as ApiError

    expect(error.status).toBe(402)
    expect(error.code).toBe('quota_exceeded')
  })

  it('opens the customer portal and the reconcile endpoint', async () => {
    const calls = stubFetch((url) =>
      url.endsWith('/portal') ? json({ url: 'https://portal.example.test/x' }) : json({ plan: 'plus', status: 'active', changed: true }),
    )

    await api.openPortal()
    const synced = await api.syncBilling()

    expect(calls.map((call) => call.url)).toEqual(['/api/billing/portal', '/api/billing/sync'])
    expect(calls.every((call) => call.init.method === 'POST')).toBe(true)
    expect(synced.changed).toBe(true)
  })
})

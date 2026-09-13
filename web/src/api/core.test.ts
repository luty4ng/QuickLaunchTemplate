import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { ApiError, api, apiUrl, describeError, getApiBase, setApiBase } from './core'

/** A fetch stub whose call arguments stay typed, so assertions can read them. */
function stubFetch(handler: (url: string, init: RequestInit) => Response | Promise<Response>) {
  const calls: { url: string; init: RequestInit }[] = []
  const mock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const request = { url: String(input), init: init ?? {} }
    calls.push(request)
    return handler(request.url, request.init)
  })
  vi.stubGlobal('fetch', mock)
  return calls
}

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })

describe('api base resolution', () => {
  beforeEach(() => localStorage.clear())
  afterEach(() => localStorage.clear())

  it('defaults to same-origin when nothing is configured', () => {
    expect(getApiBase()).toBe('')
    expect(apiUrl('/api/todos')).toBe('/api/todos')
  })

  it('trims trailing slashes from the configured base', () => {
    setApiBase('https://api.example.com/')
    expect(getApiBase()).toBe('https://api.example.com')
    expect(apiUrl('/api/todos')).toBe('https://api.example.com/api/todos')
  })

  it('clears the override when given an empty value', () => {
    setApiBase('https://api.example.com')
    setApiBase('   ')
    expect(getApiBase()).toBe('')
  })
})

describe('request handling', () => {
  beforeEach(() => localStorage.clear())
  afterEach(() => {
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
    localStorage.clear()
  })

  it('sends cookies with every request', async () => {
    const calls = stubFetch(() => json({ id: '1', email: 'a@b.com' }))

    await api.me()

    expect(calls).toHaveLength(1)
    expect(calls[0]?.url).toBe('/api/auth/me')
    expect(calls[0]?.init.credentials).toBe('include')
  })

  it('targets the configured base URL when one is set', async () => {
    setApiBase('https://api.example.com/')
    const calls = stubFetch(() => json({ id: '1', email: 'a@b.com' }))

    await api.me()

    expect(calls[0]?.url).toBe('https://api.example.com/api/auth/me')
  })

  it('sends JSON bodies for writes', async () => {
    const calls = stubFetch(() => json({ id: '1', email: 'a@b.com' }))

    await api.login('a@b.com', 'sup3rsecret')

    expect(calls[0]?.init.method).toBe('POST')
    expect(JSON.parse(String(calls[0]?.init.body))).toEqual({
      email: 'a@b.com',
      password: 'sup3rsecret',
    })
  })

  it('maps the API error envelope onto ApiError', async () => {
    stubFetch(() => json({ error: { code: 'invalid_credentials', message: 'Nope.' } }, 401))

    await expect(api.login('a@b.com', 'sup3rsecret')).rejects.toMatchObject({
      name: 'ApiError',
      status: 401,
      code: 'invalid_credentials',
      message: 'Nope.',
    })
  })

  it('labels FastAPI validation failures as validation_error', async () => {
    stubFetch(() => json({ detail: [] }, 422))

    const error = (await api.register('x', 'y').catch((cause: unknown) => cause)) as ApiError
    expect(error).toBeInstanceOf(ApiError)
    expect(error.code).toBe('validation_error')
  })

  it('falls back to a generic message for non-JSON bodies', async () => {
    stubFetch(() => new Response('<html>502</html>', { status: 502 }))

    const error = (await api.health().catch((cause: unknown) => cause)) as ApiError
    expect(error.status).toBe(502)
    expect(error.code).toBe('http_error')
    expect(error.message).toContain('502')
  })

  it('resolves 204 responses without parsing a body', async () => {
    stubFetch(() => new Response(null, { status: 204 }))

    await expect(api.logout()).resolves.toBeUndefined()
  })

  it('describes a transport failure without leaking a stack trace', () => {
    expect(describeError(new ApiError(500, 'http_error', 'The server hit an unexpected error.'))).toBe(
      'The server hit an unexpected error.',
    )
    expect(describeError(new TypeError('Failed to fetch'))).toBe('Could not reach the server.')
  })
})

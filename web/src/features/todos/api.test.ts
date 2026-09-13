import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { ApiError } from '../../api/core'
import { todosApi } from './api'

/**
 * The todos feature's wire contract. It goes through the same skeleton
 * `request` wrapper as everything else, so what is worth pinning here is the
 * path, the method and the body - not cookies or error mapping (see
 * `src/api/core.test.ts`).
 */

function stubFetch(handler: (url: string, init: RequestInit) => Response) {
  const calls: { url: string; init: RequestInit }[] = []
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

describe('todos client', () => {
  beforeEach(() => localStorage.clear())
  afterEach(() => {
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
    localStorage.clear()
  })

  it('lists todos from the collection path', async () => {
    const calls = stubFetch(() => json([]))

    await todosApi.list()

    expect(calls[0]?.url).toBe('/api/todos')
    expect(calls[0]?.init.method).toBeUndefined()
  })

  it('sends only the title when creating', async () => {
    const calls = stubFetch(() => json({ id: '1', title: 'buy milk', done: false }))

    await todosApi.create('buy milk')

    expect(calls[0]?.init.method).toBe('POST')
    expect(JSON.parse(String(calls[0]?.init.body))).toEqual({ title: 'buy milk' })
  })

  it('patches a single field and url-encodes the id', async () => {
    const calls = stubFetch(() => json({ id: 'x', title: 't', done: true }))

    await todosApi.update('id with spaces/and-slash', { done: true })

    expect(calls[0]?.url).toBe('/api/todos/id%20with%20spaces%2Fand-slash')
    expect(calls[0]?.init.method).toBe('PATCH')
    expect(JSON.parse(String(calls[0]?.init.body))).toEqual({ done: true })
  })

  it('deletes with DELETE and no body', async () => {
    const calls = stubFetch(() => new Response(null, { status: 204 }))

    await expect(todosApi.remove('abc')).resolves.toBeUndefined()

    expect(calls[0]?.init.method).toBe('DELETE')
    expect(calls[0]?.init.body).toBeUndefined()
  })

  it('exposes the quota error code the UI keys off', async () => {
    stubFetch(() =>
      json({ error: { code: 'quota_exceeded', message: 'The free plan allows 10 todos.' } }, 402),
    )

    const error = (await todosApi.create('one too many').catch((cause: unknown) => cause)) as ApiError

    expect(error.status).toBe(402)
    expect(error.code).toBe('quota_exceeded')
  })
})

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { updateBridge, type UpdatesBridge } from './core'

describe('update bridge', () => {
  beforeEach(() => localStorage.clear())
  afterEach(() => {
    vi.unstubAllGlobals()
    delete window.quicklaunch
    localStorage.clear()
  })

  it('is absent in a plain browser, so the update UI renders nothing', () => {
    delete window.quicklaunch
    expect(updateBridge()).toBeUndefined()
  })

  it('is absent when the shell exposes no updater (older desktop build)', () => {
    window.quicklaunch = { platform: 'win32', defaultApiBase: '' }
    expect(updateBridge()).toBeUndefined()
  })

  it('is exposed when the desktop shell provides it', async () => {
    const state = { status: 'ready' as const, version: '1.2.3', percent: 100 }
    const bridge: UpdatesBridge = {
      getState: vi.fn(async () => state),
      check: vi.fn(async () => state),
      install: vi.fn(async () => true),
      onChange: vi.fn(() => () => {}),
    }
    window.quicklaunch = { platform: 'win32', defaultApiBase: '', updates: bridge }

    const exposed = updateBridge()
    expect(exposed).toBe(bridge)
    await expect(exposed?.getState()).resolves.toMatchObject({ status: 'ready', version: '1.2.3' })
    await expect(exposed?.install()).resolves.toBe(true)
  })
})

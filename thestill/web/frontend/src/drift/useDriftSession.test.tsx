import { renderHook } from '@testing-library/react'
import { CHROME_UA } from '../test/setup'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { beginAudioLoad, setCorsVerdict, setCrossOriginLoadingEnabled } from './corsVerdict'
import { mediaReportsFor, page, resetDriftServiceWorkerClient } from './swClient'
import { useDriftSession } from './useDriftSession'

const auth = { isLoading: false, permissions: [] as string[] }
vi.mock('../contexts/AuthContext', () => ({
  useAuth: () => ({ isLoading: auth.isLoading, can: (p: string) => auth.permissions.includes(p) }),
}))

const URL_A = 'https://cdn.test/a.mp3'
const swListeners: ((event: { data: unknown }) => void)[] = []
const controller = { scriptURL: 'https://thestill.me/sw.js', postMessage: vi.fn() }
let controlledBy: typeof controller | null = null
const unregister = vi.fn(async () => true)
const other = vi.fn(async () => true)

beforeEach(() => {
  localStorage.clear()
  unregister.mockClear()
  controller.postMessage.mockClear()
  controlledBy = null
  sessionStorage.clear()
  page.reload = vi.fn()
  swListeners.length = 0
  resetDriftServiceWorkerClient()
  other.mockClear()
  setCrossOriginLoadingEnabled(false)
  setCorsVerdict(URL_A, 'cors_ok')
  Object.assign(auth, { isLoading: false, permissions: [] })
  vi.stubGlobal('navigator', {
    userAgent: CHROME_UA,
    serviceWorker: {
      get controller() {
        return controlledBy
      },
      addEventListener: (_name: string, listener: (event: { data: unknown }) => void) => swListeners.push(listener),
      getRegistrations: async () => [
        { active: { scriptURL: 'https://thestill.me/sw.js' }, unregister },
        { active: { scriptURL: 'https://thestill.me/some-other-worker.js' }, unregister: other },
      ],
    },
  })
})

describe('useDriftSession', () => {
  it('lets the player load with crossorigin for a session that may align', () => {
    auth.permissions = ['drift.align']
    renderHook(() => useDriftSession())
    expect(beginAudioLoad(URL_A, true)).toBe(true)
    expect(unregister).not.toHaveBeenCalled()
  })

  it('removes the service worker, and only ours, for a session that may not', async () => {
    renderHook(() => useDriftSession())
    await vi.waitFor(() => expect(unregister).toHaveBeenCalledTimes(1))
    expect(other).not.toHaveBeenCalled()
    expect(beginAudioLoad(URL_A, true)).toBe(false)
  })

  it('changes nothing while the session is still loading', async () => {
    auth.isLoading = true
    setCrossOriginLoadingEnabled(true)
    renderHook(() => useDriftSession())
    await Promise.resolve()
    expect(unregister).not.toHaveBeenCalled()
    expect(beginAudioLoad(URL_A, true)).toBe(true)
  })

  it('follows the permission when it is withdrawn', async () => {
    auth.permissions = ['drift.align']
    const { rerender } = renderHook(() => useDriftSession())
    auth.permissions = []
    rerender()
    await vi.waitFor(() => expect(unregister).toHaveBeenCalledTimes(1))
    expect(beginAudioLoad(URL_A, true)).toBe(false)
  })

  it('keeps worker reports from the start of an eligible session, wherever playback begins', () => {
    // Continue listening starts playback without opening the reader; the
    // worker's report of the first response is sent once.
    auth.permissions = ['drift.align']
    renderHook(() => useDriftSession())
    expect(swListeners).toHaveLength(1)
    const report = { url: URL_A, range: 'bytes=0-', status: 206, opaque: false, contentLength: 9, blockIndex: null, crc: null, adler: null, t: 5 }
    swListeners[0]({ data: { kind: 'drift-media', report } })
    swListeners[0]({ data: { kind: 'something-else' } })
    expect(mediaReportsFor(URL_A, null, 0)).toEqual([report])
  })

  it('does not listen for a session that may not align', () => {
    renderHook(() => useDriftSession())
    expect(swListeners).toHaveLength(0)
  })

  it('gets a page out from under a worker that already controls it', async () => {
    // Unregistering alone leaves this page's media requests going through
    // the worker until the page is gone.
    controlledBy = controller
    const { rerender } = renderHook(() => useDriftSession())
    await vi.waitFor(() => expect(page.reload).toHaveBeenCalledTimes(1))
    expect(controller.postMessage).toHaveBeenCalledWith({ kind: 'drift-disable' })
    expect(unregister).toHaveBeenCalledTimes(1)

    // Never a second reload in the same tab, whatever the next load finds.
    auth.permissions = ['drift.align']
    rerender()
    auth.permissions = []
    rerender()
    await vi.waitFor(() => expect(unregister).toHaveBeenCalledTimes(2))
    expect(page.reload).toHaveBeenCalledTimes(1)
  })

  it('does not reload a page the worker does not control', async () => {
    renderHook(() => useDriftSession())
    await vi.waitFor(() => expect(unregister).toHaveBeenCalledTimes(1))
    expect(page.reload).not.toHaveBeenCalled()
    expect(controller.postMessage).not.toHaveBeenCalled()
  })

  it('treats a browser the feature is not proven in as not holding the permission', async () => {
    // Safari, with the worker and the remembered setting left by an earlier
    // release: everything is taken away, permission or not.
    const safari =
      'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/27.2 Safari/605.1.15'
    localStorage.setItem('thestill:drift:enabled', '1')
    controlledBy = controller
    vi.stubGlobal('navigator', {
      userAgent: safari,
      serviceWorker: {
        get controller() {
          return controlledBy
        },
        addEventListener: (_name: string, listener: (event: { data: unknown }) => void) => swListeners.push(listener),
        getRegistrations: async () => [{ active: { scriptURL: 'https://thestill.me/sw.js' }, unregister }],
      },
    })
    auth.permissions = ['drift.align']
    renderHook(() => useDriftSession())

    await vi.waitFor(() => expect(unregister).toHaveBeenCalledTimes(1))
    expect(controller.postMessage).toHaveBeenCalledWith({ kind: 'drift-disable' })
    await vi.waitFor(() => expect(page.reload).toHaveBeenCalledTimes(1))
    expect(localStorage.getItem('thestill:drift:enabled')).toBeNull()
    expect(beginAudioLoad(URL_A, true)).toBe(false)
    expect(swListeners).toHaveLength(0)
  })
})

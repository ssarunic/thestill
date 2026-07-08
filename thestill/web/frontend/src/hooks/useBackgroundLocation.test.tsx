import { describe, it, expect } from 'vitest'
import { renderHook } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { useBackgroundLocation, useIsNavActive } from './useBackgroundLocation'

const inboxLocation = {
  pathname: '/inbox',
  search: '',
  hash: '',
  state: null,
  key: 'inbox-entry',
}

function wrapperAt(entry: string | { pathname: string; state?: unknown }) {
  return function Wrapper({ children }: { children: React.ReactNode }) {
    return <MemoryRouter initialEntries={[entry]}>{children}</MemoryRouter>
  }
}

describe('useBackgroundLocation', () => {
  it('returns undefined without navigation state', () => {
    const { result } = renderHook(() => useBackgroundLocation(), {
      wrapper: wrapperAt('/podcasts/pod/episodes/ep'),
    })
    expect(result.current).toBeUndefined()
  })

  it('returns the background location carried in navigation state', () => {
    const { result } = renderHook(() => useBackgroundLocation(), {
      wrapper: wrapperAt({
        pathname: '/podcasts/pod/episodes/ep',
        state: { backgroundLocation: inboxLocation },
      }),
    })
    expect(result.current?.pathname).toBe('/inbox')
  })
})

describe('useIsNavActive (spec #52 sidebar highlight)', () => {
  it('matches the current pathname exactly and by sub-path', () => {
    const exact = renderHook(() => useIsNavActive('/inbox'), {
      wrapper: wrapperAt('/inbox'),
    })
    expect(exact.result.current).toBe(true)

    const subPath = renderHook(() => useIsNavActive('/podcasts'), {
      wrapper: wrapperAt('/podcasts/pod/episodes/ep'),
    })
    expect(subPath.result.current).toBe(true)

    const noPrefixConfusion = renderHook(() => useIsNavActive('/pod'), {
      wrapper: wrapperAt('/podcasts'),
    })
    expect(noPrefixConfusion.result.current).toBe(false)
  })

  it('highlights Inbox — not Podcasts — while the reader overlay is open', () => {
    const overlayEntry = {
      pathname: '/podcasts/pod/episodes/ep',
      state: { backgroundLocation: inboxLocation },
    }

    const inbox = renderHook(() => useIsNavActive('/inbox'), {
      wrapper: wrapperAt(overlayEntry),
    })
    expect(inbox.result.current).toBe(true)

    const podcasts = renderHook(() => useIsNavActive('/podcasts'), {
      wrapper: wrapperAt(overlayEntry),
    })
    expect(podcasts.result.current).toBe(false)
  })

  it('highlights Podcasts on the standalone episode page (no overlay)', () => {
    const podcasts = renderHook(() => useIsNavActive('/podcasts'), {
      wrapper: wrapperAt('/podcasts/pod/episodes/ep'),
    })
    expect(podcasts.result.current).toBe(true)

    const inbox = renderHook(() => useIsNavActive('/inbox'), {
      wrapper: wrapperAt('/podcasts/pod/episodes/ep'),
    })
    expect(inbox.result.current).toBe(false)
  })
})

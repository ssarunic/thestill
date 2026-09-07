import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { renderHook, act } from '@testing-library/react'
import { useCollapsingHeader } from './useCollapsingHeader'

type Entry = Partial<IntersectionObserverEntry>
let callback: ((entries: Entry[]) => void) | null
let options: IntersectionObserverInit | undefined
const observe = vi.fn()
const disconnect = vi.fn()

beforeEach(() => {
  callback = null
  options = undefined
  observe.mockClear()
  disconnect.mockClear()
  vi.stubGlobal(
    'IntersectionObserver',
    class {
      constructor(cb: (entries: Entry[]) => void, opts?: IntersectionObserverInit) {
        callback = cb
        options = opts
      }
      observe = observe
      disconnect = disconnect
      unobserve() {}
    },
  )
})
afterEach(() => vi.unstubAllGlobals())

describe('useCollapsingHeader (spec #76 §3.7)', () => {
  it('observes the title once it mounts, offset by the fixed chrome, and flips on exit above the top', () => {
    const { result } = renderHook(() => useCollapsingHeader(undefined, 56))
    expect(observe).not.toHaveBeenCalled()

    const title = document.createElement('h1')
    act(() => result.current.titleRef(title))
    expect(observe).toHaveBeenCalledWith(title)
    expect(options?.rootMargin).toBe('-56px 0px 0px 0px')
    expect(options?.root).toBeNull()

    act(() => callback!([{ isIntersecting: false, boundingClientRect: { bottom: 10 } as DOMRect, rootBounds: { top: 56 } as DOMRect }]))
    expect(result.current.collapsed).toBe(true)

    // Not intersecting because it is *below* the viewport: not collapsed.
    act(() => callback!([{ isIntersecting: false, boundingClientRect: { bottom: 900 } as DOMRect, rootBounds: { top: 56 } as DOMRect }]))
    expect(result.current.collapsed).toBe(false)
  })

  it('uses the scroll container as root and resets when the title unmounts', () => {
    const container = document.createElement('div')
    const ref = { current: container }
    const { result } = renderHook(() => useCollapsingHeader(ref, 0))
    const title = document.createElement('h1')
    act(() => result.current.titleRef(title))
    expect(options?.root).toBe(container)

    act(() => callback!([{ isIntersecting: false, boundingClientRect: { bottom: -1 } as DOMRect, rootBounds: { top: 0 } as DOMRect }]))
    expect(result.current.collapsed).toBe(true)

    act(() => result.current.titleRef(null))
    expect(disconnect).toHaveBeenCalled()
    expect(result.current.collapsed).toBe(false)
  })
})

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { renderHook } from '@testing-library/react'
import { fireEvent } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { useReadingPosition } from './useReadingPosition'

// Spec #52 — the hook must work against an overlay's own scroll container,
// not just the window (standalone-page default, unchanged).

function wrapper({ children }: { children: React.ReactNode }) {
  return <MemoryRouter initialEntries={['/podcasts/p/episodes/e']}>{children}</MemoryRouter>
}

function makeContainer({ scrollTop = 0, scrollHeight = 2000, clientHeight = 1000 } = {}) {
  const el = document.createElement('div')
  Object.defineProperty(el, 'scrollHeight', { value: scrollHeight, configurable: true })
  Object.defineProperty(el, 'clientHeight', { value: clientHeight, configurable: true })
  el.scrollTop = scrollTop
  el.scrollTo = vi.fn()
  document.body.appendChild(el)
  return el
}

describe('useReadingPosition scroll-container awareness', () => {
  beforeEach(() => {
    localStorage.clear()
    vi.spyOn(window, 'scrollTo').mockImplementation(() => {})
  })

  afterEach(() => {
    vi.restoreAllMocks()
    document.body.innerHTML = ''
  })

  it('scrolls the window to top on fresh navigation when no container is given', () => {
    renderHook(() => useReadingPosition('ep-1'), { wrapper })
    expect(window.scrollTo).toHaveBeenCalledWith({ top: 0, behavior: 'instant' })
  })

  it('scrolls the container — not the window — on fresh navigation', () => {
    const el = makeContainer()
    renderHook(() => useReadingPosition('ep-1', { current: el }), { wrapper })
    expect(el.scrollTo).toHaveBeenCalledWith({ top: 0, behavior: 'instant' })
    expect(window.scrollTo).not.toHaveBeenCalled()
  })

  it('saves the container scroll position (debounced) to localStorage', () => {
    vi.useFakeTimers()
    const el = makeContainer({ scrollTop: 500 })
    renderHook(() => useReadingPosition('ep-1', { current: el }), { wrapper })

    fireEvent.scroll(el)
    vi.advanceTimersByTime(600)

    const stored = localStorage.getItem('reading-position-ep-1')
    expect(stored).not.toBeNull()
    // 500 scrolled of (2000 - 1000) scrollable
    expect(JSON.parse(stored!).scrollPercent).toBeCloseTo(0.5)
    vi.useRealTimers()
  })

  it('does not listen for window scroll when a container is given', () => {
    vi.useFakeTimers()
    const el = makeContainer({ scrollTop: 500 })
    renderHook(() => useReadingPosition('ep-1', { current: el }), { wrapper })

    fireEvent.scroll(window)
    vi.advanceTimersByTime(600)

    expect(localStorage.getItem('reading-position-ep-1')).toBeNull()
    vi.useRealTimers()
  })
})

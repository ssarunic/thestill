import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { renderHook, render, screen, waitFor, act } from '@testing-library/react'
import { fireEvent } from '@testing-library/react'
import { MemoryRouter, Routes, Route, useNavigate } from 'react-router-dom'
import { useReadingPosition } from './useReadingPosition'

// Spec #52 — the hook must work against an overlay's own scroll container,
// not just the window (standalone-page default, unchanged).

// Each test gets its own history-entry key: the hook remembers entries it
// has mounted on (module state, like useScrollRestoration), and MemoryRouter
// would otherwise hand every test the same ``default`` key.
let entrySeq = 0
function freshEntry(pathname: string) {
  entrySeq += 1
  return { pathname, key: `test-entry-${entrySeq}` }
}

function wrapper({ children }: { children: React.ReactNode }) {
  return <MemoryRouter initialEntries={[freshEntry('/podcasts/p/episodes/e')]}>{children}</MemoryRouter>
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

  it('leaves window scrolling to the layout hook when no container is given', () => {
    renderHook(() => useReadingPosition('ep-1'), { wrapper })
    expect(window.scrollTo).not.toHaveBeenCalled()
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

// A reader page that navigates away and back through the router, the way a
// People / entity link and the browser's Back button do.
// The overlay reader scrolls its own container; the hook owns that
// container's top-on-fresh / restore-on-return behaviour.
const containerRef: { current: HTMLDivElement | null } = { current: null }

function ReaderPage() {
  useReadingPosition('ep-back', containerRef)
  const navigate = useNavigate()
  return (
    <button type="button" onClick={() => navigate('/entities/person/x')}>
      leave
    </button>
  )
}

function EntityPage() {
  const navigate = useNavigate()
  return (
    <button type="button" onClick={() => navigate(-1)}>
      back
    </button>
  )
}

describe('useReadingPosition on in-app Back (spec #76 People links)', () => {
  beforeEach(() => {
    localStorage.clear()
    vi.spyOn(window, 'scrollTo').mockImplementation(() => {})
  })
  afterEach(() => vi.restoreAllMocks())

  it('restores the container position when the router pops back to the reader entry', async () => {
    const el = makeContainer({ scrollHeight: 3000, clientHeight: 1000 })
    containerRef.current = el
    render(
      <MemoryRouter initialEntries={[freshEntry('/podcasts/p/episodes/e')]}>
        <Routes>
          <Route path="/podcasts/:p/episodes/:e" element={<ReaderPage />} />
          <Route path="/entities/:type/:id" element={<EntityPage />} />
        </Routes>
      </MemoryRouter>,
    )
    // First visit: top.
    expect(el.scrollTo).toHaveBeenLastCalledWith({ top: 0, behavior: 'instant' })

    // Scroll to 40 % and leave inside the debounce window — the position is
    // flushed on unmount, not lost.
    el.scrollTop = 800
    fireEvent.scroll(el)
    await act(async () => {
      screen.getByRole('button', { name: 'leave' }).click()
    })
    expect(JSON.parse(localStorage.getItem('reading-position-ep-back')!).scrollPercent).toBeCloseTo(0.4)

    await act(async () => {
      screen.getByRole('button', { name: 'back' }).click()
    })
    await waitFor(() => {
      expect(el.scrollTo).toHaveBeenLastCalledWith({ top: 800, behavior: 'instant' })
    })
    expect(window.scrollTo).not.toHaveBeenCalled()
  })
})

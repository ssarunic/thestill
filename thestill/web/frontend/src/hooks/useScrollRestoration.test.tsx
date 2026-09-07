import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, act, waitFor } from '@testing-library/react'
import { MemoryRouter, Routes, Route, useNavigate, Outlet } from 'react-router-dom'
import { useScrollRestoration } from './useScrollRestoration'

// The hook lives in Layout: one instance, every route beneath it.
function Shell() {
  useScrollRestoration()
  return <Outlet />
}

function Page({ name }: { name: string }) {
  const navigate = useNavigate()
  return (
    <div>
      <h1>{name}</h1>
      <button type="button" onClick={() => navigate('/b')}>go b</button>
      <button type="button" onClick={() => navigate('/a?view=transcript')}>same page search</button>
      <button
        type="button"
        onClick={() => navigate('/podcasts/x/episodes/y', { state: { backgroundLocation: { pathname: '/a' } } })}
      >
        open overlay
      </button>
      <button type="button" onClick={() => navigate(-1)}>back</button>
    </div>
  )
}

let seq = 0
function renderApp() {
  seq += 1
  return render(
    <MemoryRouter initialEntries={[{ pathname: '/a', key: `entry-${seq}` }]}>
      <Routes>
        <Route element={<Shell />}>
          <Route path="/a" element={<Page name="A" />} />
          <Route path="/b" element={<Page name="B" />} />
          <Route path="/podcasts/:p/episodes/:e" element={<Page name="Reader" />} />
        </Route>
      </Routes>
    </MemoryRouter>,
  )
}

function setScrollY(value: number) {
  Object.defineProperty(window, 'scrollY', { value, configurable: true })
}

describe('useScrollRestoration in Layout', () => {
  beforeEach(() => {
    setScrollY(0)
    vi.spyOn(window, 'scrollTo').mockImplementation((...args: unknown[]) => {
      const opts = args[0]
      if (typeof opts === 'object' && opts && 'top' in opts) setScrollY((opts as { top: number }).top)
    })
  })
  afterEach(() => vi.restoreAllMocks())

  it('starts a new page at the top and restores the previous one on Back', async () => {
    renderApp()
    expect(window.scrollTo).not.toHaveBeenCalled()

    setScrollY(640)
    window.dispatchEvent(new Event('scroll'))
    await act(async () => screen.getByRole('button', { name: 'go b' }).click())
    expect(screen.getByRole('heading', { name: 'B' })).toBeInTheDocument()
    expect(window.scrollTo).toHaveBeenLastCalledWith({ top: 0, behavior: 'instant' })

    await act(async () => screen.getByRole('button', { name: 'back' }).click())
    expect(screen.getByRole('heading', { name: 'A' })).toBeInTheDocument()
    await waitFor(() => expect(window.scrollTo).toHaveBeenLastCalledWith({ top: 640, behavior: 'instant' }))
  })

  it('keeps the offset on a same-page search-param push (tabs, filters, citation jumps)', async () => {
    renderApp()
    setScrollY(300)
    window.dispatchEvent(new Event('scroll'))
    await act(async () => screen.getByRole('button', { name: 'same page search' }).click())
    expect(window.scrollTo).not.toHaveBeenCalled()
  })

  it('does not move the page beneath the reader overlay (spec #52)', async () => {
    renderApp()
    setScrollY(500)
    window.dispatchEvent(new Event('scroll'))
    await act(async () => screen.getByRole('button', { name: 'open overlay' }).click())
    expect(screen.getByRole('heading', { name: 'Reader' })).toBeInTheDocument()
    expect(window.scrollTo).not.toHaveBeenCalled()
  })
})

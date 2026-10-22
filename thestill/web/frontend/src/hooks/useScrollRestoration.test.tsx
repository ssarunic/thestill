import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, act, waitFor, within } from '@testing-library/react'
import { MemoryRouter, Routes, Route, useNavigate, useLocation, useNavigationType, Outlet } from 'react-router-dom'
import { useScrollRestoration } from './useScrollRestoration'
import { useBackgroundLocation } from './useBackgroundLocation'

// Mirrors App: the hook sits above the page <Routes>, which always get a
// ``location`` (the background one while the reader overlay is open), and
// the overlay renders in its own pass.
function ScrollRestoration() {
  useScrollRestoration()
  return null
}

function AppShell() {
  const location = useLocation()
  const backgroundLocation = useBackgroundLocation()
  return (
    <>
      <ScrollRestoration />
      <Routes location={backgroundLocation ?? location}>
        <Route element={<Outlet />}>
          <Route path="/a" element={<Page name="A" />} />
          <Route path="/b" element={<Page name="B" />} />
          <Route path="/entities/:type/:id" element={<Page name="Entity" />} />
          <Route path="/podcasts/:p/episodes/:e" element={<Page name="Episode" />} />
        </Route>
      </Routes>
      {backgroundLocation && (
        <Routes>
          <Route path="/podcasts/:p/episodes/:e" element={<Page name="Reader" />} />
          <Route path="*" element={null} />
        </Routes>
      )}
    </>
  )
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
      <button type="button" onClick={() => navigate('/entities/company/acme')}>open entity</button>
      <button type="button" onClick={() => navigate(-1)}>back</button>
    </div>
  )
}

let seq = 0
function renderApp() {
  seq += 1
  return render(
    <MemoryRouter initialEntries={[{ pathname: '/a', key: `entry-${seq}` }]}>
      <AppShell />
    </MemoryRouter>,
  )
}

function setScrollY(value: number) {
  Object.defineProperty(window, 'scrollY', { value, configurable: true })
}

describe('useScrollRestoration above the page routes', () => {
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

  it('starts the page a link in the reader overlay opens at the top', async () => {
    renderApp()
    setScrollY(500)
    window.dispatchEvent(new Event('scroll'))
    await act(async () => screen.getByRole('button', { name: 'open overlay' }).click())
    const reader = screen.getByRole('heading', { name: 'Reader' }).parentElement!
    await act(async () => within(reader).getByRole('button', { name: 'open entity' }).click())
    expect(screen.getByRole('heading', { name: 'Entity' })).toBeInTheDocument()
    expect(window.scrollTo).toHaveBeenLastCalledWith({ top: 0, behavior: 'instant' })
  })

  // Why the hook cannot live in Layout: React Router reports every
  // navigation beneath a <Routes location> as POP, PUSH included.
  it('relies on <Routes location> hiding PUSH from its subtree', async () => {
    const seen: string[] = []
    function Probe() {
      seen.push(useNavigationType())
      return <Page name="Probe" />
    }
    function Pinned() {
      const location = useLocation()
      return (
        <Routes location={location}>
          <Route path="*" element={<Probe />} />
        </Routes>
      )
    }
    render(
      <MemoryRouter initialEntries={['/a']}>
        <Pinned />
      </MemoryRouter>,
    )
    await act(async () => screen.getByRole('button', { name: 'go b' }).click())
    expect(seen.at(-1)).toBe('POP')
  })
})

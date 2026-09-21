import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import ErrorBoundary from './ErrorBoundary'

function Thrower({ message }: { message: string }): never {
  throw new Error(message)
}

describe('ErrorBoundary', () => {
  const reload = vi.fn()

  beforeEach(() => {
    sessionStorage.clear()
    reload.mockClear()
    vi.spyOn(console, 'error').mockImplementation(() => {})
    Object.defineProperty(window, 'location', {
      configurable: true,
      value: { ...window.location, reload },
    })
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('renders children when nothing throws', () => {
    render(<ErrorBoundary><p>fine</p></ErrorBoundary>)
    expect(screen.getByText('fine')).toBeInTheDocument()
  })

  it('shows a fallback instead of a blank screen on a render error', () => {
    render(<ErrorBoundary><Thrower message="boom" /></ErrorBoundary>)
    expect(screen.getByRole('alert')).toHaveTextContent('Something went wrong')
    expect(reload).not.toHaveBeenCalled()
  })

  it('reloads once when a lazy chunk from an old deploy is gone', () => {
    const message = 'Failed to fetch dynamically imported module: /assets/EpisodeDetail-old.js'
    render(<ErrorBoundary><Thrower message={message} /></ErrorBoundary>)
    expect(reload).toHaveBeenCalledTimes(1)
  })

  it('does not reload again inside the guard window', () => {
    sessionStorage.setItem('thestill:chunk-reload-at', String(Date.now()))
    const message = 'Failed to fetch dynamically imported module: /assets/EpisodeDetail-old.js'
    render(<ErrorBoundary><Thrower message={message} /></ErrorBoundary>)
    expect(reload).not.toHaveBeenCalled()
    expect(screen.getByRole('alert')).toHaveTextContent('Thestill has been updated')
  })
})

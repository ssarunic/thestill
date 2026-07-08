import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import EpisodeReaderOverlay from './EpisodeReaderOverlay'

// The reader's data/rendering is covered by EpisodeReader tests; here we
// only exercise the overlay chrome (close affordances, focus, scroll lock).
vi.mock('./EpisodeReader', () => ({
  default: () => (
    <div>
      READER_CONTENT
      <button type="button">inner action</button>
    </div>
  ),
}))

const navigateMock = vi.fn()
vi.mock('react-router-dom', async (importOriginal) => {
  const actual = await importOriginal<typeof import('react-router-dom')>()
  return { ...actual, useNavigate: () => navigateMock }
})

function renderOverlay() {
  return render(
    <MemoryRouter
      initialEntries={['/inbox', '/podcasts/pod/episodes/ep']}
      initialIndex={1}
    >
      <EpisodeReaderOverlay />
    </MemoryRouter>,
  )
}

describe('EpisodeReaderOverlay (spec #52)', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    document.body.style.overflow = ''
  })

  it('renders an accessible modal dialog containing the reader', () => {
    renderOverlay()
    const dialog = screen.getByRole('dialog')
    expect(dialog).toHaveAttribute('aria-modal', 'true')
    expect(screen.getByText('READER_CONTENT')).toBeInTheDocument()
  })

  it('moves focus into the panel on open', () => {
    renderOverlay()
    expect(document.activeElement).toBe(screen.getByRole('dialog'))
  })

  it('closes via history.back() when Escape is pressed', () => {
    renderOverlay()
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(navigateMock).toHaveBeenCalledWith(-1)
  })

  it('ignores Escape when another layered surface owns focus (e.g. ⌘K command bar)', () => {
    renderOverlay()

    // Simulate the command bar's autofocused input outside the panel.
    const outsideInput = document.createElement('input')
    document.body.appendChild(outsideInput)
    outsideInput.focus()

    fireEvent.keyDown(document, { key: 'Escape' })
    expect(navigateMock).not.toHaveBeenCalled()
    outsideInput.remove()
  })

  it('ignores Escape when another handler already claimed the event', () => {
    renderOverlay()
    const event = new KeyboardEvent('keydown', {
      key: 'Escape',
      cancelable: true,
      bubbles: true,
    })
    event.preventDefault()
    document.dispatchEvent(event)
    expect(navigateMock).not.toHaveBeenCalled()
  })

  it('closes via history.back() when the scrim is clicked', () => {
    const { container } = renderOverlay()
    const scrim = container.querySelector('[aria-hidden="true"]')
    expect(scrim).not.toBeNull()
    fireEvent.click(scrim!)
    expect(navigateMock).toHaveBeenCalledWith(-1)
  })

  it('closes via history.back() when the ← Inbox button is clicked', async () => {
    const user = userEvent.setup()
    renderOverlay()
    await user.click(screen.getByRole('button', { name: /inbox/i }))
    expect(navigateMock).toHaveBeenCalledWith(-1)
  })

  it('locks body scroll while open and restores it on close', () => {
    const { unmount } = renderOverlay()
    expect(document.body.style.overflow).toBe('hidden')
    unmount()
    expect(document.body.style.overflow).toBe('')
  })

  it('restores focus to the originating element on close', () => {
    const origin = document.createElement('button')
    document.body.appendChild(origin)
    origin.focus()

    const { unmount } = renderOverlay()
    expect(document.activeElement).not.toBe(origin)
    unmount()
    expect(document.activeElement).toBe(origin)

    origin.remove()
  })
})

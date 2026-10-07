import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { InboxActionButton } from './InboxStatus'
import type { InboxEntry } from '../api/types'

// Spec #88 "Send to my inbox". Mocks the API client, not the hooks, so the
// real query / mutation / invalidation wiring runs.
vi.mock('../api/client', () => ({
  getInboxEntry: vi.fn(),
  sendToInbox: vi.fn(),
  setInboxState: vi.fn(),
}))

import { getInboxEntry, sendToInbox, setInboxState } from '../api/client'

const mockGetEntry = getInboxEntry as ReturnType<typeof vi.fn>
const mockSend = sendToInbox as ReturnType<typeof vi.fn>
const mockSetState = setInboxState as ReturnType<typeof vi.fn>

function entry(state: InboxEntry['state'], source: InboxEntry['source'] = 'follow_new'): InboxEntry {
  return {
    id: 'i-1',
    user_id: 'u-1',
    episode_id: 'ep-1',
    source,
    state,
    delivered_at: '2026-05-11T09:12:00Z',
    state_changed_at: null,
  }
}

function renderButton() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <InboxActionButton episodeId="ep-1" />
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

describe('InboxActionButton (spec #88)', () => {
  beforeEach(() => vi.clearAllMocks())

  it('offers Send to my inbox when the episode was never delivered', async () => {
    mockGetEntry.mockResolvedValue({ status: 'ok', timestamp: '', entry: null })
    renderButton()

    expect(await screen.findByRole('button', { name: 'Send to my inbox' })).toBeInTheDocument()
    expect(mockGetEntry).toHaveBeenCalledWith('ep-1')
  })

  it('sends, then flips to the in-inbox state from the refetched entry', async () => {
    mockGetEntry.mockResolvedValueOnce({ status: 'ok', timestamp: '', entry: null })
    mockGetEntry.mockResolvedValue({ status: 'ok', timestamp: '', entry: entry('unread', 'ad_hoc') })
    mockSend.mockResolvedValue({ status: 'ok', timestamp: '', entry: entry('unread', 'ad_hoc'), created: true })
    const user = userEvent.setup()
    renderButton()

    await user.click(await screen.findByRole('button', { name: 'Send to my inbox' }))

    expect(mockSend).toHaveBeenCalledWith('ep-1')
    expect(await screen.findByText('In your inbox')).toBeInTheDocument()
    expect(screen.getByText(/You haven't read it yet\./)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Send to my inbox' })).toBeNull()
  })

  it('shows the error inline when the send fails and keeps the button', async () => {
    mockGetEntry.mockResolvedValue({ status: 'ok', timestamp: '', entry: null })
    mockSend.mockRejectedValue(new Error('Episode not found'))
    const user = userEvent.setup()
    renderButton()

    await user.click(await screen.findByRole('button', { name: 'Send to my inbox' }))

    expect(await screen.findByText('Episode not found')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Send to my inbox' })).toBeInTheDocument()
  })

  it.each([
    ['unread', "You haven't read it yet.", 'Save for later'],
    ['read', "You've read it.", 'Save for later'],
    ['dismissed', "You dismissed it, so it's hidden from the main list.", 'Restore to inbox'],
  ] as const)('an existing %s row shows its state and one in-place action', async (state, sentence, action) => {
    mockGetEntry.mockResolvedValue({ status: 'ok', timestamp: '', entry: entry(state) })
    const user = userEvent.setup()
    renderButton()

    // Delivered: a filled icon; the state and its action open as a popover.
    await user.click(await screen.findByRole('button', { name: 'In your inbox' }))
    expect(screen.getByRole('dialog', { name: 'Inbox status' })).toBeInTheDocument()
    expect(screen.getByText('In your inbox')).toBeInTheDocument()
    expect(screen.getByText(new RegExp(sentence.replace(/[.?]/g, '\\$&')))).toBeInTheDocument()
    expect(screen.getByRole('button', { name: action })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Send to my inbox' })).toBeNull()
  })

  it('a saved row links to the Saved view and offers Remove from saved', async () => {
    mockGetEntry.mockResolvedValueOnce({ status: 'ok', timestamp: '', entry: entry('saved') })
    mockGetEntry.mockResolvedValue({ status: 'ok', timestamp: '', entry: entry('read') })
    mockSetState.mockResolvedValue({ status: 'ok', timestamp: '', entry: entry('read') })
    const user = userEvent.setup()
    renderButton()

    await user.click(await screen.findByRole('button', { name: 'In your inbox' }))
    expect(screen.getByRole('link', { name: 'View saved' })).toHaveAttribute('href', '/inbox?view=saved')
    await user.click(screen.getByRole('button', { name: 'Remove from saved' }))

    // Unsaving lands on read in place; the row stays in the inbox.
    expect(mockSetState).toHaveBeenCalledWith('ep-1', 'read')
    expect(mockSend).not.toHaveBeenCalled()
    await waitFor(() => expect(screen.getByText(/You've read it\./)).toBeInTheDocument())
    expect(screen.getByRole('button', { name: 'Save for later' })).toBeInTheDocument()
  })

  it('Restore to inbox changes state in place and never resends', async () => {
    mockGetEntry.mockResolvedValueOnce({ status: 'ok', timestamp: '', entry: entry('dismissed') })
    mockGetEntry.mockResolvedValue({ status: 'ok', timestamp: '', entry: entry('unread') })
    mockSetState.mockResolvedValue({ status: 'ok', timestamp: '', entry: entry('unread') })
    const user = userEvent.setup()
    renderButton()

    await user.click(await screen.findByRole('button', { name: 'In your inbox' }))
    await user.click(screen.getByRole('button', { name: 'Restore to inbox' }))

    expect(mockSetState).toHaveBeenCalledWith('ep-1', 'unread')
    expect(mockSend).not.toHaveBeenCalled()
    await waitFor(() => expect(screen.getByText(/You haven't read it yet\./)).toBeInTheDocument())
  })

  it('renders nothing when the lookup fails rather than guessing "not in inbox"', async () => {
    mockGetEntry.mockRejectedValue(new Error('API error: 500'))
    const { container } = renderButton()

    await waitFor(() => expect(mockGetEntry).toHaveBeenCalled())
    await waitFor(() => expect(container).toBeEmptyDOMElement())
    expect(screen.queryByRole('button', { name: 'Send to my inbox' })).toBeNull()
  })

  it('the popover closes on Escape and on a tap outside', async () => {
    mockGetEntry.mockResolvedValue({ status: 'ok', timestamp: '', entry: entry('unread') })
    const user = userEvent.setup()
    renderButton()

    const icon = await screen.findByRole('button', { name: 'In your inbox' })
    await user.click(icon)
    expect(icon).toHaveAttribute('aria-expanded', 'true')
    await user.keyboard('{Escape}')
    expect(screen.queryByRole('dialog')).toBeNull()

    await user.click(icon)
    expect(screen.getByRole('dialog')).toBeInTheDocument()
    await user.click(document.body)
    expect(screen.queryByRole('dialog')).toBeNull()
  })
})

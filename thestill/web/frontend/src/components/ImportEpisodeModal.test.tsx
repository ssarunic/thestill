import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { BrowserRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import ImportEpisodeModal from './ImportEpisodeModal'
import type { ImportResponse } from '../api/types'

vi.mock('../api/client', () => ({
  importEpisode: vi.fn(),
  setInboxState: vi.fn(),
}))

import { importEpisode, setInboxState } from '../api/client'

const mockImportEpisode = importEpisode as ReturnType<typeof vi.fn>
const mockSetInboxState = setInboxState as ReturnType<typeof vi.fn>

function createWrapper() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  return function Wrapper({ children }: { children: React.ReactNode }) {
    return (
      <QueryClientProvider client={queryClient}>
        <BrowserRouter>{children}</BrowserRouter>
      </QueryClientProvider>
    )
  }
}

function bareAudioResponse(overrides?: Partial<ImportResponse['import']>): ImportResponse {
  return {
    status: 'ok',
    timestamp: '2026-05-08T00:00:00Z',
    import: {
      episode_id: 'ep-1',
      canonical_id: 'audio:abc',
      title: 'Some Audio File',
      kind: 'bare_audio',
      source_handle: 'cdn.example.com',
      outcome: 'new_episode',
      inbox_created: true,
      episode_slug: 'some-audio-file',
      podcast_slug: 'audio-imports',
      episode_state: 'discovered',
      episode_failed: false,
      inbox_entry: {
        id: 'i-1',
        user_id: 'u-1',
        episode_id: 'ep-1',
        source: 'import',
        state: 'unread',
        delivered_at: '2026-05-08T00:00:00Z',
        state_changed_at: null,
      },
      parent: null,
      ...overrides,
    },
  }
}

function youtubeResponse(): ImportResponse {
  return bareAudioResponse({
    canonical_id: 'youtube:dQw4w9WgXcQ',
    kind: 'youtube',
    title: 'Never Gonna Give You Up',
    source_handle: 'Rick Astley',
    parent: { id: 'p-1', title: 'Rick Astley', slug: 'rick-astley' },
  })
}

describe('ImportEpisodeModal', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('does not render when closed', () => {
    render(<ImportEpisodeModal isOpen={false} onClose={vi.fn()} />, {
      wrapper: createWrapper(),
    })
    expect(screen.queryByRole('heading', { name: /import episode/i })).toBeNull()
  })

  it('disables Import until URL is typed', () => {
    render(<ImportEpisodeModal isOpen={true} onClose={vi.fn()} />, {
      wrapper: createWrapper(),
    })
    expect(screen.getByRole('button', { name: 'Import' })).toBeDisabled()
  })

  it('sends Spotify episode links to the API and shows the follow CTA for the matched show', async () => {
    mockImportEpisode.mockResolvedValue(
      bareAudioResponse({
        canonical_id: 'spotify:4rOoJ6Egrf8K2IrywzwOMk',
        kind: 'spotify_episode',
        title: 'Mark Zuckerberg on Muse',
        source_handle: 'Sources with Alex Heath',
        parent: { id: 'p-2', title: 'Sources with Alex Heath', slug: 'sources-with-alex-heath' },
      }),
    )
    const user = userEvent.setup()
    render(<ImportEpisodeModal isOpen={true} onClose={vi.fn()} />, {
      wrapper: createWrapper(),
    })
    await user.type(screen.getByRole('textbox'), 'https://open.spotify.com/episode/4rOoJ6Egrf8K2IrywzwOMk')
    await user.click(screen.getByRole('button', { name: 'Import' }))

    await waitFor(() => {
      expect(screen.getByText('Mark Zuckerberg on Muse')).toBeInTheDocument()
    })
    expect(mockImportEpisode).toHaveBeenCalledWith({
      url: 'https://open.spotify.com/episode/4rOoJ6Egrf8K2IrywzwOMk',
    })
    expect(screen.getByRole('link', { name: 'View channel' })).toHaveAttribute(
      'href',
      '/podcasts/sources-with-alex-heath',
    )
  })

  it('surfaces the backend message when a Spotify exclusive cannot be matched', async () => {
    mockImportEpisode.mockRejectedValue(
      new Error('Could not find “Exclusive Show” in the Apple Podcasts directory.'),
    )
    const user = userEvent.setup()
    render(<ImportEpisodeModal isOpen={true} onClose={vi.fn()} />, {
      wrapper: createWrapper(),
    })
    await user.type(screen.getByRole('textbox'), 'https://open.spotify.com/episode/4rOoJ6Egrf8K2IrywzwOMk')
    await user.click(screen.getByRole('button', { name: 'Import' }))
    expect(await screen.findByText(/Could not find “Exclusive Show”/)).toBeInTheDocument()
  })

  it('shows the bare-audio success state with no follow CTA', async () => {
    mockImportEpisode.mockResolvedValue(bareAudioResponse())
    const user = userEvent.setup()
    render(<ImportEpisodeModal isOpen={true} onClose={vi.fn()} />, {
      wrapper: createWrapper(),
    })

    await user.type(screen.getByRole('textbox'), 'https://example.com/foo.mp3')
    await user.click(screen.getByRole('button', { name: 'Import' }))

    await waitFor(() => {
      expect(screen.getByText(/Importing — this may take a few minutes/)).toBeInTheDocument()
    })
    expect(screen.getByText('Some Audio File')).toBeInTheDocument()
    // No parent → no "View channel" CTA.
    expect(screen.queryByText(/View channel/)).toBeNull()
    expect(mockImportEpisode).toHaveBeenCalledWith({ url: 'https://example.com/foo.mp3' })
  })

  it('shows the YouTube success state with a follow-channel CTA', async () => {
    mockImportEpisode.mockResolvedValue(youtubeResponse())
    const user = userEvent.setup()
    render(<ImportEpisodeModal isOpen={true} onClose={vi.fn()} />, {
      wrapper: createWrapper(),
    })

    await user.type(
      screen.getByRole('textbox'),
      'https://www.youtube.com/watch?v=dQw4w9WgXcQ',
    )
    await user.click(screen.getByRole('button', { name: 'Import' }))

    await waitFor(() => {
      expect(screen.getByText(/Importing — this may take a few minutes/)).toBeInTheDocument()
    })
    expect(screen.getByText(/This episode is from/)).toBeInTheDocument()
    const cta = screen.getByText('View channel') as HTMLAnchorElement
    expect(cta.closest('a')?.getAttribute('href')).toBe('/podcasts/rick-astley')
  })

  async function importAndWait(response: ImportResponse) {
    mockImportEpisode.mockResolvedValue(response)
    const onClose = vi.fn()
    const user = userEvent.setup()
    render(<ImportEpisodeModal isOpen={true} onClose={onClose} />, {
      wrapper: createWrapper(),
    })
    await user.type(screen.getByRole('textbox'), 'https://example.com/foo.mp3')
    await user.click(screen.getByRole('button', { name: 'Import' }))
    await screen.findByText('Some Audio File')
    return { user, onClose }
  }

  // Spec #88 — one view per real outcome.
  describe('outcomes (spec #88)', () => {
    it('added_existing + summarised offers Read now straight into the episode', async () => {
      await importAndWait(
        bareAudioResponse({ outcome: 'added_existing', episode_state: 'summarized' }),
      )
      expect(screen.getByText('Added to your inbox')).toBeInTheDocument()
      expect(screen.getByText(/already transcribed and summarised/)).toBeInTheDocument()
      expect(screen.getByRole('link', { name: 'Read now' })).toHaveAttribute(
        'href',
        '/podcasts/audio-imports/episodes/some-audio-file',
      )
      expect(screen.queryByText(/Already in your inbox/i)).toBeNull()
    })

    it('added_existing + in progress shows the pipeline stage and Go to inbox', async () => {
      await importAndWait(
        bareAudioResponse({ outcome: 'added_existing', episode_state: 'downloaded' }),
      )
      expect(screen.getByText('Added to your inbox')).toBeInTheDocument()
      expect(screen.getByText('Transcribing…')).toBeInTheDocument()
      expect(screen.getByRole('link', { name: 'Go to inbox' })).toHaveAttribute('href', '/inbox')
    })

    it('added_existing + failed points at the episode page for retry', async () => {
      await importAndWait(
        bareAudioResponse({ outcome: 'added_existing', episode_state: 'discovered', episode_failed: true }),
      )
      expect(screen.getByText(/Processing failed earlier/)).toBeInTheDocument()
      expect(screen.getByRole('link', { name: 'Open episode' })).toBeInTheDocument()
    })

    it('new_episode with a parent still offers the follow CTA; other outcomes do not', async () => {
      await importAndWait(
        bareAudioResponse({
          outcome: 'added_existing',
          episode_state: 'summarized',
          parent: { id: 'p-1', title: 'Rick Astley', slug: 'rick-astley' },
        }),
      )
      expect(screen.queryByText('View channel')).toBeNull()
    })

    function alreadyInInbox(state: 'unread' | 'read' | 'saved' | 'dismissed') {
      return bareAudioResponse({
        outcome: 'already_in_inbox',
        inbox_created: false,
        episode_state: 'summarized',
        inbox_entry: {
          id: 'i-1',
          user_id: 'u-1',
          episode_id: 'ep-1',
          source: 'follow_new',
          state,
          delivered_at: '2026-05-11T09:12:00Z',
          state_changed_at: null,
        },
      })
    }

    it('already_in_inbox names the delivery date and opens the episode', async () => {
      await importAndWait(alreadyInInbox('read'))
      expect(screen.getByText(/Good news — this is already in your inbox/)).toBeInTheDocument()
      // Locale-formatted: "11 May" or "May 11".
      expect(screen.getByText(/Delivered (11 May|May 11)\./)).toBeInTheDocument()
      expect(screen.getByText(/You've read it\./)).toBeInTheDocument()
      expect(screen.getByRole('link', { name: 'Open episode' })).toHaveAttribute(
        'href',
        '/podcasts/audio-imports/episodes/some-audio-file',
      )
    })

    it.each([
      ['unread', /You haven't read it yet\./, 'Save for later'],
      ['read', /You've read it\./, 'Save for later'],
      ['dismissed', /You dismissed it/, 'Restore to inbox'],
    ] as const)('already_in_inbox (%s) offers one in-place action', async (state, sentence, action) => {
      await importAndWait(alreadyInInbox(state))
      expect(screen.getByText(sentence)).toBeInTheDocument()
      expect(screen.getByRole('button', { name: action })).toBeInTheDocument()
    })

    it('already_in_inbox (saved) has no action, only a link to the Saved view', async () => {
      await importAndWait(alreadyInInbox('saved'))
      expect(screen.getByText(/in your saved items/)).toBeInTheDocument()
      expect(screen.queryByRole('button', { name: /Save for later|Restore to inbox/ })).toBeNull()
      expect(screen.getByRole('link', { name: 'View saved' })).toHaveAttribute('href', '/inbox?view=saved')
    })

    it('Save for later sets state in place, then closes onto the Saved view', async () => {
      mockSetInboxState.mockResolvedValue({ status: 'ok', timestamp: '', entry: {} })
      const { user, onClose } = await importAndWait(alreadyInInbox('read'))

      await user.click(screen.getByRole('button', { name: 'Save for later' }))

      await waitFor(() => expect(onClose).toHaveBeenCalled())
      expect(mockSetInboxState).toHaveBeenCalledWith('ep-1', 'saved')
      expect(window.location.pathname + window.location.search).toBe('/inbox?view=saved')
    })

    it('Restore to inbox moves a dismissed row back to unread without navigating', async () => {
      window.history.replaceState(null, '', '/inbox')
      mockSetInboxState.mockResolvedValue({ status: 'ok', timestamp: '', entry: {} })
      const { user, onClose } = await importAndWait(alreadyInInbox('dismissed'))

      await user.click(screen.getByRole('button', { name: 'Restore to inbox' }))

      await waitFor(() => expect(onClose).toHaveBeenCalled())
      expect(mockSetInboxState).toHaveBeenCalledWith('ep-1', 'unread')
      expect(window.location.pathname + window.location.search).toBe('/inbox')
    })

    it('a failed state change stays open and shows the error', async () => {
      mockSetInboxState.mockRejectedValue(new Error('Inbox entry not found'))
      const { user, onClose } = await importAndWait(alreadyInInbox('read'))

      await user.click(screen.getByRole('button', { name: 'Save for later' }))

      expect(await screen.findByText('Inbox entry not found')).toBeInTheDocument()
      expect(onClose).not.toHaveBeenCalled()
    })
  })

  it('renders the API error message inline', async () => {
    mockImportEpisode.mockRejectedValue(new Error('No resolver matched URL'))
    const user = userEvent.setup()
    render(<ImportEpisodeModal isOpen={true} onClose={vi.fn()} />, {
      wrapper: createWrapper(),
    })

    await user.type(screen.getByRole('textbox'), 'https://vimeo.com/abc')
    await user.click(screen.getByRole('button', { name: 'Import' }))

    expect(await screen.findByText(/No resolver matched URL/)).toBeInTheDocument()
    // Form is back to interactive — Import button reads "Import" again.
    expect(screen.getByRole('button', { name: 'Import' })).toBeInTheDocument()
  })
})

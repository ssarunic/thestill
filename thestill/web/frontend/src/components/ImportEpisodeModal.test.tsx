import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { BrowserRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import ImportEpisodeModal from './ImportEpisodeModal'
import type { ImportResponse } from '../api/types'

vi.mock('../api/client', () => ({
  importEpisode: vi.fn(),
}))

import { importEpisode } from '../api/client'

const mockImportEpisode = importEpisode as ReturnType<typeof vi.fn>

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
      deduplicated: false,
      inbox_created: true,
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

  it('rejects Spotify links client-side without calling the API', async () => {
    const user = userEvent.setup()
    render(<ImportEpisodeModal isOpen={true} onClose={vi.fn()} />, {
      wrapper: createWrapper(),
    })
    await user.type(screen.getByRole('textbox'), 'https://open.spotify.com/episode/abc')
    await user.click(screen.getByRole('button', { name: 'Import' }))
    expect(await screen.findByText(/Spotify links are not supported/)).toBeInTheDocument()
    expect(mockImportEpisode).not.toHaveBeenCalled()
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

  it("shows 'Already in your inbox' when the import is a dedup hit", async () => {
    mockImportEpisode.mockResolvedValue(
      bareAudioResponse({ deduplicated: true, inbox_created: false }),
    )
    const user = userEvent.setup()
    render(<ImportEpisodeModal isOpen={true} onClose={vi.fn()} />, {
      wrapper: createWrapper(),
    })

    await user.type(screen.getByRole('textbox'), 'https://example.com/foo.mp3')
    await user.click(screen.getByRole('button', { name: 'Import' }))

    expect(await screen.findByText(/Already in your inbox/)).toBeInTheDocument()
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

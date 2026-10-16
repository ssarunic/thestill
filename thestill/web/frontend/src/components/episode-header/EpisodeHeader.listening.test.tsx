import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import EpisodeHeader from './EpisodeHeader'
import { ToastProvider } from '../Toast'
import type { EpisodeDetail, ListeningSummary } from '../../api/types'

// Spec #90: the hero's primary action, eyebrow and ⋯ More menu.
const episode = {
  id: 'ep-1',
  title: 'Sample Episode',
  podcast_title: 'Sample Pod',
  duration: 3600,
  pub_date: '2026-09-21T12:00:00Z',
  image_url: null,
  podcast_image_url: null,
  description: '',
} as unknown as EpisodeDetail

const progress = (position: number, overrides: Partial<ListeningSummary> = {}): ListeningSummary => ({
  position_seconds: position,
  duration_seconds: 3600,
  completed_at: null,
  updated_at: '2026-10-16T09:00:00Z',
  ...overrides,
})

function renderHeader(
  listening: ListeningSummary | null,
  { isCurrent = false, onPlayFromStart = vi.fn(), onMarkPlayed = vi.fn().mockResolvedValue(undefined) } = {},
) {
  render(
    <ToastProvider>
      <MemoryRouter>
        <EpisodeHeader
          episode={episode}
          podcastSlug="sample-pod"
          playback={{ isCurrent, isPlaying: false, isLoading: false, onToggle: vi.fn() }}
          showWatchVideo={false}
          onWatchVideo={vi.fn()}
          shareUrl="https://thestill.test/e"
          listening={listening}
          onPlayFromStart={onPlayFromStart}
          onMarkPlayed={onMarkPlayed}
        />
      </MemoryRouter>
    </ToastProvider>,
  )
  return { onPlayFromStart, onMarkPlayed }
}

const openMenu = () => userEvent.setup().click(screen.getByRole('button', { name: 'More actions' }))

describe('EpisodeHeader listening progress (spec #90)', () => {
  it('never played: the primary action carries the duration', () => {
    renderHeader(null)
    expect(screen.getByRole('button', { name: 'Play episode, 60 min' })).toHaveTextContent('60 min')
    expect(screen.queryByRole('progressbar')).toBeNull()
  })

  it('part-listened: the primary action carries the time left, the eyebrow a meter', () => {
    renderHeader(progress(1680))
    expect(screen.getByRole('button', { name: 'Resume episode, 32 min left' })).toHaveTextContent('32 min left')
    expect(screen.getByRole('progressbar', { name: '32 min left' })).toHaveAttribute('aria-valuenow', '47')
  })

  it('played: reads Played in the eyebrow and plays like a fresh episode', () => {
    renderHeader(progress(0, { completed_at: '2026-10-15T09:00:00Z' }))
    expect(screen.getByText('Played')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Play episode, 60 min' })).toBeInTheDocument()
  })

  it('the More menu offers Play from beginning only with a position', async () => {
    const { onPlayFromStart } = renderHeader(progress(1680))
    await openMenu()
    await userEvent.setup().click(screen.getByRole('menuitem', { name: 'Play from beginning' }))
    expect(onPlayFromStart).toHaveBeenCalled()
    expect(screen.queryByRole('menu')).toBeNull()
  })

  it('Mark as played / unplayed toggles on the completion', async () => {
    const { onMarkPlayed } = renderHeader(progress(1680))
    await openMenu()
    await userEvent.setup().click(screen.getByRole('menuitem', { name: 'Mark as played' }))
    expect(onMarkPlayed).toHaveBeenCalledWith(true)
  })

  it('offers Mark as unplayed on a played episode', async () => {
    const { onMarkPlayed } = renderHeader(progress(0, { completed_at: '2026-10-15T09:00:00Z' }))
    await openMenu()
    expect(screen.queryByRole('menuitem', { name: 'Play from beginning' })).toBeNull()
    await userEvent.setup().click(screen.getByRole('menuitem', { name: 'Mark as unplayed' }))
    expect(onMarkPlayed).toHaveBeenCalledWith(false)
  })

  it('a failed mark keeps the menu open with an error', async () => {
    renderHeader(progress(1680), { onMarkPlayed: vi.fn().mockRejectedValue(new Error('offline')) })
    await openMenu()
    await userEvent.setup().click(screen.getByRole('menuitem', { name: 'Mark as played' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Could not update')
    expect(screen.getByRole('menu')).toBeInTheDocument()
  })
})

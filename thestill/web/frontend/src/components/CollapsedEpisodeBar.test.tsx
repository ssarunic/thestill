import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import CollapsedEpisodeBar from './CollapsedEpisodeBar'

describe('CollapsedEpisodeBar (spec #76 §3.7)', () => {
  it('shows title and artwork and toggles playback from a 44 px control', async () => {
    const onTogglePlay = vi.fn()
    render(
      <CollapsedEpisodeBar
        state={{ title: 'Why Nobody Trusts the News', artworkUrl: 'https://example.com/a.jpg', isPlaying: false, isLoading: false, onTogglePlay }}
      />,
    )
    expect(screen.getByText('Why Nobody Trusts the News')).toBeInTheDocument()
    expect(document.querySelector('img')).toHaveAttribute('src', 'https://example.com/a.jpg')
    const play = screen.getByRole('button', { name: 'Play' })
    expect(play.className).toContain('w-11')
    await userEvent.setup().click(play)
    expect(onTogglePlay).toHaveBeenCalled()
  })

  it('names the control Pause while playing', () => {
    render(<CollapsedEpisodeBar state={{ title: 'T', artworkUrl: null, isPlaying: true, isLoading: false, onTogglePlay: () => {} }} />)
    expect(screen.getByRole('button', { name: 'Pause' })).toBeInTheDocument()
  })
})

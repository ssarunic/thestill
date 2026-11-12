import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import NowPlayingScrubber from './NowPlayingScrubber'

describe('the Ad tag in the player (spec #116 §6.1)', () => {
  it('sits beside the time while an ad of the listener’s own plays, and not otherwise', () => {
    const { rerender } = render(<NowPlayingScrubber currentTime={20} duration={60} onSeek={() => {}} />)
    expect(screen.queryByTestId('player-ad-tag')).toBeNull()
    rerender(<NowPlayingScrubber currentTime={20} duration={60} onSeek={() => {}} adPlaying />)
    expect(screen.getByTestId('player-ad-tag')).toHaveTextContent('Ad')
  })
})

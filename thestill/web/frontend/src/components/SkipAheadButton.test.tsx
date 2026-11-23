import { fireEvent, render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import SkipAheadButton from './SkipAheadButton'

const player = { skipAheadAvailable: false, skipAhead: vi.fn<() => number | null>() }
vi.mock('../contexts/PlayerContext', () => ({ usePlayer: () => player }))
const track = vi.fn()
vi.mock('../analytics/client', () => ({ track: (...args: unknown[]) => track(...args) }))

describe('Skip ahead (spec #116 §6.2)', () => {
  beforeEach(() => {
    player.skipAheadAvailable = false
    player.skipAhead.mockReset()
    track.mockReset()
  })

  it('is not there outside an ad', () => {
    render(<SkipAheadButton />)
    expect(screen.queryByTestId('skip-ahead')).toBeNull()
  })

  it('skips the ad and counts the tap', () => {
    player.skipAheadAvailable = true
    player.skipAhead.mockReturnValue(42.4)
    render(<SkipAheadButton />)
    fireEvent.click(screen.getByRole('button', { name: 'Skip ahead' }))
    expect(player.skipAhead).toHaveBeenCalledOnce()
    expect(track).toHaveBeenCalledWith('skip_ahead', { seconds: 42 })
  })
})

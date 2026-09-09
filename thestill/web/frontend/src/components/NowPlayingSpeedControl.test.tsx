import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import NowPlayingSpeedControl from './NowPlayingSpeedControl'
import { nextRate } from '../utils/playbackRate'

describe('NowPlayingSpeedControl (spec #72 §5)', () => {
  it('shows the current rate and steps to the next on tap', async () => {
    const onChange = vi.fn()
    render(<NowPlayingSpeedControl rate={1.5} availableRates={null} onChange={onChange} />)
    await userEvent.click(screen.getByRole('button', { name: 'Speed 1.5×' }))
    expect(onChange).toHaveBeenCalledWith(2)
  })

  it('wraps from the last option to the first', () => {
    expect(nextRate(2, null)).toBe(0.8)
  })

  it('skips options the engine does not accept', () => {
    // YouTube reports 0.5/1/1.25/1.5/2: 0.8 and 1.2 are out, so 1 → 1.5.
    expect(nextRate(1, [0.5, 1, 1.25, 1.5, 2])).toBe(1.5)
    // A clamped rate that is not an option advances to the option above it.
    expect(nextRate(1.25, [0.5, 1, 1.25, 1.5, 2])).toBe(1.5)
  })
})

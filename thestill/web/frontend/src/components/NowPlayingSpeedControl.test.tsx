import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import NowPlayingSpeedControl from './NowPlayingSpeedControl'

describe('NowPlayingSpeedControl (spec #72 §5)', () => {
  it('marks the current rate and reports a new one', async () => {
    const onChange = vi.fn()
    render(<NowPlayingSpeedControl rate={1.5} availableRates={null} onChange={onChange} />)
    expect(screen.getByRole('radio', { name: '1.5×' })).toHaveAttribute('aria-checked', 'true')
    expect(screen.getByRole('radio', { name: '1×' })).toHaveAttribute('aria-checked', 'false')
    await userEvent.click(screen.getByRole('radio', { name: '2×' }))
    expect(onChange).toHaveBeenCalledWith(2)
  })

  it('disables options the engine does not accept, keeping the control shape', () => {
    render(<NowPlayingSpeedControl rate={1} availableRates={[0.5, 1, 1.25, 1.5, 2]} onChange={() => {}} />)
    expect(screen.getAllByRole('radio')).toHaveLength(5)
    expect(screen.getByRole('radio', { name: '0.8×' })).toBeDisabled()
    expect(screen.getByRole('radio', { name: '1.2×' })).toBeDisabled()
    expect(screen.getByRole('radio', { name: '1.5×' })).toBeEnabled()
  })
})

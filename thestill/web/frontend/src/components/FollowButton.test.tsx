import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import FollowButton from './FollowButton'

describe('FollowButton', () => {
  it('keeps its accessible name when icon-only on phones', () => {
    render(<FollowButton status="idle" onClick={vi.fn()} iconOnlyMobile />)
    const button = screen.getByRole('button', { name: 'Follow' })
    // sr-only, not hidden: the label stays in the accessibility tree.
    expect(screen.getByText('Follow').className).toContain('sr-only')
    expect(button.className).toContain('min-h-[44px]')
  })

  it('renders done as a disabled success state', () => {
    render(<FollowButton status="done" onClick={vi.fn()} />)
    const button = screen.getByRole('button', { name: 'Following ✓' })
    expect(button).toBeDisabled()
    expect(button.className).toContain('bg-green-100')
  })

  it('renders pending as loading and error as a retry', async () => {
    const user = userEvent.setup()
    const onClick = vi.fn()
    const { rerender } = render(<FollowButton status="pending" onClick={onClick} />)
    expect(screen.getByRole('button', { name: 'Following…' })).toBeDisabled()

    rerender(<FollowButton status="error" onClick={onClick} />)
    await user.click(screen.getByRole('button', { name: 'Retry' }))
    expect(onClick).toHaveBeenCalledTimes(1)
  })

  it('accepts the add-podcast modal vocabulary', () => {
    render(
      <FollowButton
        status="done"
        onClick={vi.fn()}
        labels={{ idle: 'Add', pending: 'Adding…', done: 'Added ✓' }}
      />,
    )
    expect(screen.getByRole('button', { name: 'Added ✓' })).toBeDisabled()
  })
})

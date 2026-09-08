import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import ConfirmDialog from './ConfirmDialog'

describe('ConfirmDialog', () => {
  it('renders nothing when closed', () => {
    const { container } = render(
      <ConfirmDialog isOpen={false} title="T" message="M" confirmLabel="Go" onConfirm={() => {}} onCancel={() => {}} />,
    )
    expect(container.innerHTML).toBe('')
  })

  it('confirm and cancel fire their callbacks; backdrop click cancels', async () => {
    const onConfirm = vi.fn()
    const onCancel = vi.fn()
    render(
      <ConfirmDialog isOpen title="Revoke?" message="Sure?" confirmLabel="Revoke" confirmVariant="danger" onConfirm={onConfirm} onCancel={onCancel} />,
    )
    expect(screen.getByRole('dialog', { name: 'Revoke?' })).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Revoke' }))
    expect(onConfirm).toHaveBeenCalledTimes(1)
    await userEvent.click(screen.getByRole('button', { name: 'Cancel' }))
    expect(onCancel).toHaveBeenCalledTimes(1)
    await userEvent.click(screen.getByRole('presentation'))
    expect(onCancel).toHaveBeenCalledTimes(2)
  })

  it('ignores cancel while busy', async () => {
    const onCancel = vi.fn()
    render(<ConfirmDialog isOpen busy title="T" message="M" confirmLabel="Go" onConfirm={() => {}} onCancel={onCancel} />)
    await userEvent.click(screen.getByRole('presentation'))
    expect(onCancel).not.toHaveBeenCalled()
  })
})

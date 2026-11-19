import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import ProductTipsSettings from './ProductTipsSettings'

const auth = vi.hoisted(() => ({
  user: { id: 'u-1', product_tips_email_enabled: true } as { id: string; product_tips_email_enabled?: boolean } | null,
  isMultiUser: true,
  productTipsEmailAvailable: true,
  updateProductTipsEmail: vi.fn(() => Promise.resolve()),
}))
vi.mock('../contexts/AuthContext', () => ({ useAuth: () => auth }))

describe('ProductTipsSettings (spec #118)', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    auth.user = { id: 'u-1', product_tips_email_enabled: true }
    auth.isMultiUser = true
    auth.productTipsEmailAvailable = true
  })

  it('switches tips off on the server', async () => {
    render(<ProductTipsSettings />)
    const box = screen.getByRole('checkbox', { name: /welcome email/i })
    expect(box).toBeChecked()
    await userEvent.setup().click(box)
    expect(auth.updateProductTipsEmail).toHaveBeenCalledWith(false)
  })

  it('reads a missing flag as off (accounts from before the setting)', () => {
    auth.user = { id: 'u-1' }
    render(<ProductTipsSettings />)
    expect(screen.getByRole('checkbox')).not.toBeChecked()
  })

  it('is hidden in single-user mode and when tips email is unavailable', () => {
    auth.isMultiUser = false
    const { unmount } = render(<ProductTipsSettings />)
    expect(screen.queryByRole('checkbox')).not.toBeInTheDocument()
    unmount()
    auth.isMultiUser = true
    auth.productTipsEmailAvailable = false
    render(<ProductTipsSettings />)
    expect(screen.queryByRole('checkbox')).not.toBeInTheDocument()
  })
})

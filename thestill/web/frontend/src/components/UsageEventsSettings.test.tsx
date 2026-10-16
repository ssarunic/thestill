import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import UsageEventsSettings from './UsageEventsSettings'

const auth = vi.hoisted(() => ({
  user: { id: 'u-1', usage_analytics_enabled: true } as { id: string; usage_analytics_enabled?: boolean } | null,
  isMultiUser: true,
  usageAnalyticsAvailable: true,
  updateUsageAnalytics: vi.fn(() => Promise.resolve()),
}))
vi.mock('../contexts/AuthContext', () => ({ useAuth: () => auth }))

describe('UsageEventsSettings (spec #95)', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    auth.user = { id: 'u-1', usage_analytics_enabled: true }
    auth.isMultiUser = true
    auth.usageAnalyticsAvailable = true
  })

  it('switches usage events off on the server', async () => {
    render(<UsageEventsSettings />)
    const box = screen.getByRole('checkbox')
    expect(box).toBeChecked()
    await userEvent.setup().click(box)
    expect(auth.updateUsageAnalytics).toHaveBeenCalledWith(false)
  })

  it('is hidden in single-user mode and when usage events are off', () => {
    auth.isMultiUser = false
    const { unmount } = render(<UsageEventsSettings />)
    expect(screen.queryByRole('checkbox')).not.toBeInTheDocument()
    unmount()
    auth.isMultiUser = true
    auth.usageAnalyticsAvailable = false
    render(<UsageEventsSettings />)
    expect(screen.queryByRole('checkbox')).not.toBeInTheDocument()
  })
})

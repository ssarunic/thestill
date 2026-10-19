import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { ToastProvider } from '../../components/Toast'
import AdminUsers from './AdminUsers'
import AdminUserDetail from './AdminUserDetail'
import AdminSettings from './AdminSettings'
import type { AdminSetting, AdminUserDetail as Detail, AdminUserRow } from '../../api/types'

// Spec #104 Phase 4 — mocks the admin API module so the real hooks run.
vi.mock('../../api/admin', () => ({
  getAdminUsers: vi.fn(),
  getAdminUser: vi.fn(),
  getAdminSettings: vi.fn(),
  setUserStatus: vi.fn(),
  putAdminSetting: vi.fn(),
  logoutEverywhere: vi.fn(),
  removeIntroducedFeeds: vi.fn(),
  setQuotaOverrides: vi.fn(),
  setAdminRole: vi.fn(),
  resetAdminSetting: vi.fn(),
  sendTestAlert: vi.fn(),
  getAdminAudit: vi.fn(),
}))
const auth = { isMultiUser: true, user: { id: 'admin-1' } as { id: string } }
vi.mock('../../contexts/AuthContext', () => ({ useAuth: () => auth }))

import * as api from '../../api/admin'
const mocked = api as unknown as Record<string, ReturnType<typeof vi.fn>>

const user = (over: Partial<AdminUserRow> = {}): AdminUserRow => ({
  id: 'u-1',
  email: 'spam@example.com',
  name: 'Spam',
  picture: null,
  created_at: '2026-10-01T00:00:00+00:00',
  last_login_at: null,
  signup_source: 'direct',
  is_admin: false,
  status: 'active',
  status_reason: null,
  status_changed_at: null,
  status_changed_by: null,
  follows: 3,
  suspended_follows: 0,
  feeds_brought_in_24h: 9,
  feeds_brought_in_total: 12,
  follows_24h: 9,
  imports_24h: 0,
  translations_24h: 0,
  quota_hits_24h: 4,
  ...over,
})

const detail = (over: Partial<Detail['user']> = {}): Detail => ({
  user: { ...user(), ...over },
  usage_24h: { feed_introduced: 9 },
  usage_7d: { feed_introduced: 12 },
  daily_7d: { feed_introduced: [0, 0, 1, 2, 0, 0, 9] },
  follows: [],
  quota_limits: { imports_per_day: 5 },
  quota_overrides: {},
  history: [],
})

function Where() {
  const location = useLocation()
  return <div data-testid="where">{location.pathname + location.search}</div>
}

function renderAt(url: string, element: React.ReactNode, path: string) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <ToastProvider>
        <MemoryRouter initialEntries={[url]}>
          <Routes>
            <Route path={path} element={<>{element}<Where /></>} />
          </Routes>
        </MemoryRouter>
      </ToastProvider>
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  for (const fn of Object.values(mocked)) fn.mockReset()
  auth.isMultiUser = true
})

describe('AdminUsers', () => {
  it('lists users and keeps filters in the URL', async () => {
    mocked.getAdminUsers.mockResolvedValue({ users: [user()], total: 1, offset: 0, limit: 50, has_more: false })
    renderAt('/admin/users?status=banned', <AdminUsers />, '/admin/users')
    expect(await screen.findByRole('link', { name: 'spam@example.com' })).toHaveAttribute('href', '/admin/users/u-1')
    expect(mocked.getAdminUsers).toHaveBeenCalledWith(expect.objectContaining({ status: 'banned', sort: 'attention' }))

    await userEvent.selectOptions(screen.getByLabelText('Sort'), 'follows')
    expect(screen.getByTestId('where')).toHaveTextContent('/admin/users?status=banned&sort=follows')
  })

  it('says so in single-user mode and fetches nothing', () => {
    auth.isMultiUser = false
    renderAt('/admin/users', <AdminUsers />, '/admin/users')
    expect(screen.getByText('Multi-user mode only')).toBeInTheDocument()
    expect(mocked.getAdminUsers).not.toHaveBeenCalled()
  })
})

describe('AdminUserDetail', () => {
  it('bans only with a reason, and can also remove the feeds they brought in', async () => {
    mocked.getAdminUser.mockResolvedValue(detail())
    mocked.setUserStatus.mockResolvedValue({ user: user({ status: 'banned' }) })
    renderAt('/admin/users/u-1', <AdminUserDetail />, '/admin/users/:userId')

    await userEvent.click(await screen.findByRole('button', { name: 'Ban' }))
    const dialog = screen.getByRole('dialog')
    await userEvent.click(within(dialog).getByRole('button', { name: 'Ban' }))
    expect(mocked.setUserStatus).not.toHaveBeenCalled()
    expect(within(dialog).getByText('A reason is required.')).toBeInTheDocument()

    await userEvent.type(within(dialog).getByRole('textbox'), 'scripted follows')
    await userEvent.click(within(dialog).getByRole('checkbox'))
    await userEvent.click(within(dialog).getByRole('button', { name: 'Ban' }))
    await waitFor(() =>
      expect(mocked.setUserStatus).toHaveBeenCalledWith('u-1', {
        status: 'banned',
        reason: 'scripted follows',
        remove_introduced_feeds: true,
      }),
    )
  })

  it('can ban a suspended account, and offers only Reinstate once banned', async () => {
    mocked.getAdminUser.mockResolvedValue(detail({ status: 'suspended' }))
    const { unmount } = renderAt('/admin/users/u-1', <AdminUserDetail />, '/admin/users/:userId')
    expect(await screen.findByRole('button', { name: 'Reinstate' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Ban' })).toBeEnabled()
    expect(screen.queryByRole('button', { name: 'Suspend' })).not.toBeInTheDocument()
    unmount()

    mocked.getAdminUser.mockResolvedValue(detail({ status: 'banned' }))
    renderAt('/admin/users/u-2', <AdminUserDetail />, '/admin/users/:userId')
    expect(await screen.findByRole('button', { name: 'Reinstate' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Ban' })).not.toBeInTheDocument()
  })

  it("won't let an admin disable or demote themselves", async () => {
    auth.user = { id: 'u-1' }
    mocked.getAdminUser.mockResolvedValue(detail({ is_admin: true }))
    renderAt('/admin/users/u-1', <AdminUserDetail />, '/admin/users/:userId')
    expect(await screen.findByText("You can't change your own status or admin role.")).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Suspend' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Ban' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Remove admin role' })).toBeDisabled()
    auth.user = { id: 'admin-1' }
  })
})

const setting = (key: string, value: unknown, over: Partial<AdminSetting> = {}): AdminSetting => ({
  key,
  value,
  source: 'env',
  default: value,
  is_default: true,
  description: '',
  updated_at: null,
  updated_by: null,
  ...over,
})

describe('AdminSettings', () => {
  it('asks before turning on read-only mode', async () => {
    mocked.getAdminSettings.mockResolvedValue({
      settings: [
        setting('signup_mode', 'open'),
        setting('signup_allowlist', []),
        setting('new_feeds_enabled', true),
        setting('imports_enabled', true),
        setting('translations_enabled', true),
        setting('narration_enabled', false),
        setting('read_only', false),
        setting('quota_overrides', {}),
        setting('alerts', {}),
      ],
      non_default: {},
      alert_channels: ['log'],
    })
    mocked.putAdminSetting.mockResolvedValue({ setting: setting('read_only', true) })
    renderAt('/admin/settings', <AdminSettings />, '/admin/settings')

    await userEvent.click(await screen.findByLabelText('Read-only mode (panic)'))
    expect(mocked.putAdminSetting).not.toHaveBeenCalled()
    await userEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Turn on read-only' }))
    await waitFor(() => expect(mocked.putAdminSetting).toHaveBeenCalledWith('read_only', true))

    await userEvent.click(screen.getByLabelText('closed'))
    await waitFor(() => expect(mocked.putAdminSetting).toHaveBeenCalledWith('signup_mode', 'closed'))
  })
})

describe('admin console review fixes', () => {
  it('a new search goes back to page one', async () => {
    mocked.getAdminUsers.mockResolvedValue({ users: [user()], total: 120, offset: 50, limit: 50, has_more: true })
    renderAt('/admin/users?page=2', <AdminUsers />, '/admin/users')
    await screen.findByRole('link', { name: 'spam@example.com' })
    await userEvent.type(screen.getByLabelText('Search users'), 'spam')
    await waitFor(() => expect(screen.getByTestId('where')).toHaveTextContent('/admin/users?q=spam'))
    expect(screen.getByTestId('where')).not.toHaveTextContent('page=')
  })

  it('keeps the typed reason when the ban is refused', async () => {
    mocked.getAdminUser.mockResolvedValue(detail())
    mocked.setUserStatus.mockRejectedValue(new Error('This is the last active admin.'))
    renderAt('/admin/users/u-1', <AdminUserDetail />, '/admin/users/:userId')
    await userEvent.click(await screen.findByRole('button', { name: 'Ban' }))
    const dialog = screen.getByRole('dialog')
    await userEvent.type(within(dialog).getByRole('textbox'), 'abuse')
    await userEvent.click(within(dialog).getByRole('button', { name: 'Ban' }))
    expect(await screen.findByText('This is the last active admin.')).toBeInTheDocument()
    expect(within(screen.getByRole('dialog')).getByRole('textbox')).toHaveValue('abuse')
  })

  it('offers every quota the server reports, with readable names', async () => {
    mocked.getAdminUser.mockResolvedValue({ ...detail(), quota_limits: { imports_per_day: 5, brand_new_quota: 7 } })
    renderAt('/admin/users/u-1', <AdminUserDetail />, '/admin/users/:userId')
    expect(await screen.findByPlaceholderText('default 5')).toBeInTheDocument()
    expect(screen.getByText('Imports / day')).toBeInTheDocument()
    expect(screen.getByText('brand_new_quota')).toBeInTheDocument()
  })
})

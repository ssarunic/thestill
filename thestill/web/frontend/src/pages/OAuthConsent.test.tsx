import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import OAuthConsent from './OAuthConsent'
import type { OAuthPendingResponse } from '../api/types'

// Spec #109 — the consent screen: shows who is asking and where the reader
// goes back to, offers only the scopes the app asked for, and follows the
// server's redirect after Approve or Deny.

vi.mock('../api/client', () => ({
  getOAuthPending: vi.fn(),
  approveOAuthPending: vi.fn(),
  denyOAuthPending: vi.fn(),
}))

const authState = { isAdmin: false }
vi.mock('../contexts/AuthContext', () => ({
  useAuth: () => authState,
}))

import { getOAuthPending, approveOAuthPending, denyOAuthPending } from '../api/client'

const mockPending = getOAuthPending as ReturnType<typeof vi.fn>
const mockApprove = approveOAuthPending as ReturnType<typeof vi.fn>
const mockDeny = denyOAuthPending as ReturnType<typeof vi.fn>
const assign = vi.fn()

function pending(overrides: Partial<OAuthPendingResponse> = {}): OAuthPendingResponse {
  return {
    status: 'ok',
    timestamp: '',
    client_name: 'Claude',
    redirect_host: 'claude.ai',
    loopback_only: false,
    requested_scopes: ['read', 'follows'],
    offline_access: true,
    ...overrides,
  }
}

function renderPage(hash = '#request=abc') {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[`/oauth/consent${hash}`]}>
        <OAuthConsent />
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

const realLocation = window.location

beforeEach(() => {
  vi.clearAllMocks()
  authState.isAdmin = false
  Object.defineProperty(window, 'location', { value: { ...realLocation, assign }, writable: true })
})

afterEach(() => {
  Object.defineProperty(window, 'location', { value: realLocation, writable: true })
})

describe('OAuthConsent', () => {
  it('shows the app, the redirect host and only the requested scopes', async () => {
    mockPending.mockResolvedValue(pending({ requested_scopes: ['read'] }))
    renderPage()
    expect(await screen.findByText('Claude wants to connect to your thestill account')).toBeInTheDocument()
    expect(screen.getByTestId('redirect-host')).toHaveTextContent('claude.ai')
    expect(screen.getByLabelText('Read')).toBeChecked()
    expect(screen.queryByLabelText('Follows')).not.toBeInTheDocument()
    expect(screen.queryByLabelText('Pipeline')).not.toBeInTheDocument()
    expect(screen.getByText(/Stays connected until you disconnect/)).toBeInTheDocument()
    expect(mockPending).toHaveBeenCalledWith('abc')
  })

  it('warns when the app is a program on this computer', async () => {
    mockPending.mockResolvedValue(pending({ client_name: 'Claude Code', redirect_host: 'localhost', loopback_only: true }))
    renderPage()
    expect(await screen.findByRole('note')).toHaveTextContent(/program on this computer/)
  })

  it('approves with the ticked scopes and follows the redirect', async () => {
    mockPending.mockResolvedValue(pending())
    mockApprove.mockResolvedValue({ status: 'ok', timestamp: '', redirect: 'https://claude.ai/api/mcp/auth_callback?code=x' })
    renderPage()
    // Follows is offered (the app asked) but off by default.
    const follows = await screen.findByLabelText('Follows')
    expect(follows).not.toBeChecked()
    await userEvent.click(follows)
    await userEvent.click(screen.getByRole('button', { name: 'Approve' }))
    await waitFor(() => expect(mockApprove).toHaveBeenCalledWith('abc', ['read', 'follows']))
    expect(assign).toHaveBeenCalledWith('https://claude.ai/api/mcp/auth_callback?code=x')
  })

  it('denies and follows the redirect', async () => {
    mockPending.mockResolvedValue(pending())
    mockDeny.mockResolvedValue({ status: 'ok', timestamp: '', redirect: 'https://claude.ai/cb?error=access_denied' })
    renderPage()
    await userEvent.click(await screen.findByRole('button', { name: 'Deny' }))
    await waitFor(() => expect(assign).toHaveBeenCalledWith('https://claude.ai/cb?error=access_denied'))
    expect(mockApprove).not.toHaveBeenCalled()
  })

  it('explains an expired or answered request', async () => {
    mockPending.mockResolvedValue(null)
    renderPage()
    expect(await screen.findByText('This request has expired')).toBeInTheDocument()
  })

  it('tells a load failure apart from an expired request', async () => {
    mockPending.mockRejectedValue(new Error('API error: 503'))
    renderPage()
    expect(await screen.findByRole('alert')).toHaveTextContent('Couldn’t load this request')
    expect(screen.queryByText('This request has expired')).not.toBeInTheDocument()
  })

  it('offers pipeline to an admin only when the app asked for it', async () => {
    authState.isAdmin = true
    mockPending.mockResolvedValue(pending({ requested_scopes: ['read', 'follows'] }))
    renderPage()
    expect(await screen.findByLabelText('Follows')).toBeInTheDocument()
    expect(screen.queryByLabelText('Pipeline')).not.toBeInTheDocument()
  })

  it('describes each scope to assistive tech', async () => {
    mockPending.mockResolvedValue(pending())
    renderPage()
    expect(await screen.findByLabelText('Follows')).toHaveAccessibleDescription(/Adding a feed runs the pipeline/)
  })

  it('shows a failed approval and lets the reader try again', async () => {
    mockPending.mockResolvedValue(pending({ loopback_only: false, offline_access: false }))
    mockApprove.mockRejectedValue(new Error('Authorization request not found'))
    renderPage()
    expect(await screen.findByText('Claude wants to connect to your thestill account')).toBeInTheDocument()
    expect(screen.queryByRole('note')).not.toBeInTheDocument()
    expect(screen.queryByText(/Stays connected/)).not.toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Approve' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Authorization request not found')
    expect(screen.getByRole('button', { name: 'Approve' })).toBeEnabled()
    expect(assign).not.toHaveBeenCalled()
  })

  it('sends one decision for a double click', async () => {
    mockPending.mockResolvedValue(pending())
    let release: (v: { status: string; timestamp: string; redirect: string }) => void = () => {}
    mockApprove.mockReturnValue(new Promise((resolve) => (release = resolve)))
    renderPage()
    const approve = await screen.findByRole('button', { name: 'Approve' })
    await userEvent.dblClick(approve)
    release({ status: 'ok', timestamp: '', redirect: 'https://claude.ai/cb?code=x' })
    await waitFor(() => expect(assign).toHaveBeenCalledTimes(1))
    expect(mockApprove).toHaveBeenCalledTimes(1)
  })

  it('refuses to follow a script URL', async () => {
    mockPending.mockResolvedValue(pending())
    mockApprove.mockResolvedValue({ status: 'ok', timestamp: '', redirect: 'javascript:alert(1)' })
    renderPage()
    await userEvent.click(await screen.findByRole('button', { name: 'Approve' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('cannot be opened')
    expect(assign).not.toHaveBeenCalled()
  })

  it('explains a missing request id without calling the server', async () => {
    renderPage('')
    expect(await screen.findByText('This request has expired')).toBeInTheDocument()
    expect(mockPending).not.toHaveBeenCalled()
  })
})

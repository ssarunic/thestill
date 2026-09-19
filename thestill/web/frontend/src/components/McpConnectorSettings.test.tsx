import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import McpConnectorSettings from './McpConnectorSettings'
import type { McpTokenInfoResponse, McpTokenMintResponse } from '../api/types'

// Spec #78 Phase 2 — per-user connector card: create with a scope form,
// one-time URL modal, rotate/revoke behind ConfirmDialog, expiring warning,
// and the plaintext never appears outside the mint response.

vi.mock('../api/client', () => ({
  getMcpToken: vi.fn(),
  createOrRotateMcpToken: vi.fn(),
  revokeMcpToken: vi.fn(),
}))

const authState = { isAdmin: false }
vi.mock('../contexts/AuthContext', () => ({
  useAuth: () => authState,
}))

const showToast = vi.fn()
vi.mock('./Toast', () => ({
  useToast: () => ({ showToast }),
}))

import { getMcpToken, createOrRotateMcpToken, revokeMcpToken } from '../api/client'

const mockGet = getMcpToken as ReturnType<typeof vi.fn>
const mockMint = createOrRotateMcpToken as ReturnType<typeof vi.fn>
const mockRevoke = revokeMcpToken as ReturnType<typeof vi.fn>

const TOKEN = 't'.repeat(64)

function info(overrides: Partial<McpTokenInfoResponse>): McpTokenInfoResponse {
  return { status: 'ok', timestamp: '2026-09-08T00:00:00Z', enabled: true, state: 'none', ...overrides }
}

function mint(): McpTokenMintResponse {
  return {
    status: 'ok',
    timestamp: '2026-09-08T00:00:00Z',
    url: `https://pods.example.com/mcp/${TOKEN}`,
    scopes: ['read', 'follows'],
    expires_at: '2026-12-07T00:00:00Z',
  }
}

function renderCard() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <McpConnectorSettings />
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  vi.clearAllMocks()
  authState.isAdmin = false
})

describe('McpConnectorSettings', () => {
  it('explains a disabled server', async () => {
    mockGet.mockResolvedValue(info({ enabled: false }))
    renderCard()
    expect(await screen.findByText(/Remote MCP is disabled/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Create connector URL/ })).not.toBeInTheDocument()
  })

  it('creates with the scope form and shows the URL exactly once', async () => {
    mockGet.mockResolvedValueOnce(info({ state: 'none' })).mockResolvedValue(
      info({ state: 'active', prefix: TOKEN.slice(0, 6), scopes: ['read', 'follows'], created_at: '2026-09-08T00:00:00Z' }),
    )
    mockMint.mockResolvedValue(mint())
    renderCard()
    await userEvent.click(await screen.findByRole('button', { name: /Create connector URL/ }))

    // Non-admin: pipeline is not offered; read is locked on.
    expect(screen.getByRole('checkbox', { name: 'Read' })).toBeDisabled()
    expect(screen.queryByRole('checkbox', { name: 'Pipeline' })).not.toBeInTheDocument()
    await userEvent.click(screen.getByRole('checkbox', { name: 'Follows' }))
    await userEvent.click(screen.getByRole('button', { name: 'Create' }))

    await waitFor(() => expect(mockMint).toHaveBeenCalledWith(['read', 'follows']))
    const url = await screen.findByTestId('mcp-url')
    expect(url.textContent).toContain(TOKEN)
    await userEvent.click(screen.getByRole('button', { name: 'Done' }))

    // After the modal closes the plaintext is gone; only the prefix remains.
    await waitFor(() => expect(screen.getByTestId('mcp-url-masked')).toBeInTheDocument())
    expect(document.body.textContent).not.toContain(TOKEN)
    expect(screen.getByTestId('mcp-url-masked').textContent).toContain(TOKEN.slice(0, 6))
    expect(showToast).toHaveBeenCalledWith('Connector created', 'success')
  })

  it('offers pipeline to admins', async () => {
    authState.isAdmin = true
    mockGet.mockResolvedValue(info({ state: 'none' }))
    renderCard()
    await userEvent.click(await screen.findByRole('button', { name: /Create connector URL/ }))
    expect(screen.getByRole('checkbox', { name: 'Pipeline' })).toBeInTheDocument()
  })

  it('rotate asks for confirmation then mints', async () => {
    mockGet.mockResolvedValue(info({ state: 'active', prefix: 'abcdef', scopes: ['read'] }))
    mockMint.mockResolvedValue(mint())
    renderCard()
    await userEvent.click(await screen.findByRole('button', { name: 'Rotate' }))
    await userEvent.click(screen.getByRole('button', { name: 'Rotate' })) // scope form submit
    expect(screen.getByRole('dialog', { name: /Rotate connector URL/ })).toBeInTheDocument()
    expect(mockMint).not.toHaveBeenCalled()
    await userEvent.click(screen.getAllByRole('button', { name: 'Rotate' }).at(-1)!)
    await waitFor(() => expect(mockMint).toHaveBeenCalledWith(['read']))
    expect(await screen.findByTestId('mcp-url')).toBeInTheDocument()
  })

  it('revoke asks for confirmation then revokes', async () => {
    mockGet.mockResolvedValueOnce(info({ state: 'active', prefix: 'abcdef', scopes: ['read'] })).mockResolvedValue(
      info({ state: 'revoked', revoked_at: '2026-09-08T10:00:00Z' }),
    )
    mockRevoke.mockResolvedValue(undefined)
    renderCard()
    await userEvent.click(await screen.findByRole('button', { name: 'Revoke' }))
    expect(mockRevoke).not.toHaveBeenCalled()
    await userEvent.click(screen.getAllByRole('button', { name: 'Revoke' }).at(-1)!)
    await waitFor(() => expect(mockRevoke).toHaveBeenCalledTimes(1))
    expect(await screen.findByText(/Revoked on/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Create connector URL/ })).toBeInTheDocument()
  })

  it('warns when expiring', async () => {
    mockGet.mockResolvedValue(
      info({ state: 'expiring', prefix: 'abcdef', scopes: ['read'], expires_at: '2026-09-15T00:00:00Z' }),
    )
    renderCard()
    expect(await screen.findByText(/rotate to renew/)).toBeInTheDocument()
  })
})

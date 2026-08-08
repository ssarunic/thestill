import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import McpConnectorSettings from './McpConnectorSettings'
import type { McpStatusResponse } from '../api/types'

// Spec #71 Phase 1 — the connector card is admin-only, masks the
// capability URL by default, and shows enable instructions when the
// server has the feature off.

vi.mock('../api/client', () => ({
  getMcpStatus: vi.fn(),
}))

const authState = { isAdmin: true }
vi.mock('../contexts/AuthContext', () => ({
  useAuth: () => authState,
}))

import { getMcpStatus } from '../api/client'

const mockGetMcpStatus = getMcpStatus as ReturnType<typeof vi.fn>

const SECRET = 's'.repeat(48)
const URL = `https://pods.example.com/mcp/${SECRET}`

function response(mcp: McpStatusResponse['mcp']): McpStatusResponse {
  return { status: 'ok', timestamp: '2026-08-08T00:00:00Z', mcp }
}

beforeEach(() => {
  vi.clearAllMocks()
  authState.isAdmin = true
})

describe('McpConnectorSettings', () => {
  it('renders nothing for non-admins and never calls the API', () => {
    authState.isAdmin = false
    const { container } = render(<McpConnectorSettings />)
    expect(container.innerHTML).toBe('')
    expect(mockGetMcpStatus).not.toHaveBeenCalled()
  })

  it('shows enable instructions when the server has MCP off', async () => {
    mockGetMcpStatus.mockResolvedValue(response({ enabled: false }))
    render(<McpConnectorSettings />)
    await waitFor(() =>
      expect(screen.getByText(/Remote MCP is disabled/)).toBeInTheDocument(),
    )
    expect(screen.getByText(/MCP_HTTP_ENABLED=true/)).toBeInTheDocument()
  })

  it('masks the URL by default and reveals on demand', async () => {
    mockGetMcpStatus.mockResolvedValue(
      response({ enabled: true, url: URL, transport: 'streamable-http' }),
    )
    render(<McpConnectorSettings />)
    const code = await screen.findByTestId('mcp-url')
    expect(code.textContent).not.toContain(SECRET)
    expect(code.textContent).toContain('/mcp/')

    await userEvent.click(screen.getByRole('button', { name: 'Reveal' }))
    expect(screen.getByTestId('mcp-url').textContent).toBe(URL)
  })

  it('copies the full (unmasked) URL to the clipboard', async () => {
    mockGetMcpStatus.mockResolvedValue(
      response({ enabled: true, url: URL, transport: 'streamable-http' }),
    )
    const writeText = vi.fn().mockResolvedValue(undefined)
    Object.assign(navigator, { clipboard: { writeText } })

    render(<McpConnectorSettings />)
    await screen.findByTestId('mcp-url')
    await userEvent.click(screen.getByRole('button', { name: 'Copy' }))
    expect(writeText).toHaveBeenCalledWith(URL)
    await waitFor(() =>
      expect(screen.getByRole('button', { name: 'Copied' })).toBeInTheDocument(),
    )
  })
})

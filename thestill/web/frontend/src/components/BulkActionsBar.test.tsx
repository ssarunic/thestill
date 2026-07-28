import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import BulkActionsBar from './BulkActionsBar'

// Mirrors the server-side require_admin on POST /api/episodes/bulk/process.
let mockIsAdmin = true
vi.mock('../contexts/AuthContext', () => ({
  useAuth: () => ({ isAdmin: mockIsAdmin }),
}))

vi.mock('../hooks/useApi', () => ({
  useBulkProcess: () => ({ mutateAsync: vi.fn(), isPending: false }),
}))

describe('BulkActionsBar admin gating', () => {
  beforeEach(() => {
    mockIsAdmin = true
  })

  it('shows Process All for admins with a selection', () => {
    render(<BulkActionsBar selectedIds={new Set(['ep-1'])} onClearSelection={() => {}} />)
    expect(screen.getByRole('button', { name: /process all/i })).toBeInTheDocument()
  })

  it('renders nothing for non-admins even with a selection', () => {
    mockIsAdmin = false
    const { container } = render(
      <BulkActionsBar selectedIds={new Set(['ep-1'])} onClearSelection={() => {}} />
    )
    expect(container.firstChild).toBeNull()
  })

  it('renders nothing with an empty selection', () => {
    const { container } = render(<BulkActionsBar selectedIds={new Set()} onClearSelection={() => {}} />)
    expect(container.firstChild).toBeNull()
  })
})

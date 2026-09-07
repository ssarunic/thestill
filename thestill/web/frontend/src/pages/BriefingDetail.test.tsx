import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Routes, Route } from 'react-router-dom'
import BriefingDetail from './BriefingDetail'

vi.mock('../hooks/useApi', () => ({
  useBriefing: vi.fn(),
  useBriefingScript: vi.fn(() => ({ data: { markdown: '# Script' }, isLoading: false, error: null })),
  useMarkBriefingListened: vi.fn(),
}))
vi.mock('../components/NarrationView', () => ({
  default: ({ linkIndexFallback }: { linkIndexFallback: React.ReactNode }) => <div>NARRATION{linkIndexFallback}</div>,
}))

import { useBriefing, useMarkBriefingListened } from '../hooks/useApi'
const mockUseBriefing = useBriefing as ReturnType<typeof vi.fn>
const mockMarkListened = useMarkBriefingListened as ReturnType<typeof vi.fn>

function renderPage() {
  return render(
    <MemoryRouter initialEntries={['/briefings/b1']}>
      <Routes>
        <Route path="/briefings/:briefingId" element={<BriefingDetail />} />
      </Routes>
    </MemoryRouter>,
  )
}

describe('BriefingDetail header (spec #76 phase 3)', () => {
  const mutate = vi.fn()
  beforeEach(() => {
    vi.clearAllMocks()
    mockMarkListened.mockReturnValue({ mutate, isPending: false })
    mockUseBriefing.mockReturnValue({
      data: { id: 'b1', episode_count: 3, created_at: '2026-09-06T07:00:00Z', listened_at: null, narrations: [] },
      isLoading: false,
      error: null,
    })
  })

  it('renders the eyebrow, title and the Mark listened primary in the action row', async () => {
    renderPage()
    expect(screen.getByRole('heading', { name: "Today's briefing" })).toBeInTheDocument()
    const eyebrow = screen.getByText('3 episodes').closest('p')!
    expect(eyebrow).toHaveTextContent(/Generated/)
    expect(eyebrow).not.toHaveTextContent(/Listened/)
    const button = screen.getByRole('button', { name: 'Mark listened' })
    expect(button.className).toContain('rounded-full')
    await userEvent.setup().click(button)
    expect(mutate).toHaveBeenCalledWith('b1')
    expect(screen.getByText('NARRATION')).toBeInTheDocument()
  })

  it('disables the action and notes the listen time once listened', () => {
    mockUseBriefing.mockReturnValue({
      data: { id: 'b1', episode_count: 1, created_at: '2026-09-06T07:00:00Z', listened_at: '2026-09-06T09:00:00Z', narrations: [] },
      isLoading: false,
      error: null,
    })
    renderPage()
    expect(screen.getByText('1 episode').closest('p')).toHaveTextContent(/Listened/)
    expect(screen.getByRole('button', { name: 'Marked listened' })).toBeDisabled()
  })
})

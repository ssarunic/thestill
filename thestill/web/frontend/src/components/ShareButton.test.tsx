import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import ShareButton from './ShareButton'

vi.mock('./Toast', () => ({ useToast: () => ({ showToast: vi.fn() }) }))
vi.mock('../analytics/client', () => ({ track: vi.fn() }))
import { track } from '../analytics/client'

describe('ShareButton usage event (spec #95)', () => {
  const writeText = vi.fn(() => Promise.resolve())
  beforeEach(() => {
    vi.clearAllMocks()
    Object.defineProperty(navigator, 'share', { value: undefined, configurable: true })
  })

  // user-event installs its own clipboard on setup; ours goes on after.
  function setup() {
    const user = userEvent.setup()
    Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true })
    return user
  }
  afterEach(() => vi.restoreAllMocks())

  it('reports a share by kind, never the URL', async () => {
    const user = setup()
    render(<ShareButton title="Ep" url="https://thestill.test/podcasts/x/episodes/y?t=30" />)
    await user.click(screen.getByRole('button'))
    expect(writeText).toHaveBeenCalled()
    await waitFor(() => expect(track).toHaveBeenCalledWith('share', { kind: 'episode' }))
  })

  it('reports nothing when copying fails', async () => {
    writeText.mockRejectedValueOnce(new Error('denied'))
    const user = setup()
    render(<ShareButton title="Ep" url="https://thestill.test/x" />)
    await user.click(screen.getByRole('button'))
    expect(writeText).toHaveBeenCalled()
    expect(track).not.toHaveBeenCalled()
  })
})

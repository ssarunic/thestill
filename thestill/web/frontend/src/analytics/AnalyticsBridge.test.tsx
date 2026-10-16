import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { Link, MemoryRouter } from 'react-router-dom'
import AnalyticsBridge from './AnalyticsBridge'

const auth = vi.hoisted(() => ({
  user: { id: 'u-1', is_admin: true } as { id: string; is_admin: boolean } | null,
  analyticsKey: 'phc_test' as string | null,
}))
vi.mock('../contexts/AuthContext', () => ({ useAuth: () => auth }))
vi.mock('./client', () => ({ startAnalytics: vi.fn(), stopAnalytics: vi.fn(), track: vi.fn() }))

import { startAnalytics, stopAnalytics, track } from './client'

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <AnalyticsBridge />
      <Link to="/briefings/abc?from=email">open briefing</Link>
    </MemoryRouter>,
  )
}

describe('AnalyticsBridge (spec #95)', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    auth.user = { id: 'u-1', is_admin: true }
    auth.analyticsKey = 'phc_test'
  })

  it('starts for a signed-in reader with a key and reports route patterns', async () => {
    renderAt('/podcasts/hard-fork/episodes/ep-1?t=30')
    expect(startAnalytics).toHaveBeenCalledWith('phc_test', { id: 'u-1' })
    expect(track).toHaveBeenCalledWith('$pageview', { path_kind: '/podcasts/:podcastSlug/episodes/:episodeSlug' })

    await userEvent.setup().click(document.querySelector('a')!)
    expect(track).toHaveBeenLastCalledWith('$pageview', { path_kind: '/briefings/:briefingId' })
  })

  it('stops and reports nothing without a key (opted out, GPC or off)', () => {
    auth.analyticsKey = null
    renderAt('/inbox')
    expect(startAnalytics).not.toHaveBeenCalled()
    expect(stopAnalytics).toHaveBeenCalled()
    expect(track).not.toHaveBeenCalled()
  })

  it('does nothing for a signed-out visitor even if a key is around', () => {
    auth.user = null
    renderAt('/login')
    expect(startAnalytics).not.toHaveBeenCalled()
    expect(track).not.toHaveBeenCalled()
  })
})

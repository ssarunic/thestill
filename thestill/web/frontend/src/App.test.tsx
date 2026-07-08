import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import App from './App'

// Spec #52 — background-location route split. These tests exercise the
// routing shell only: the pages and the overlay chrome are mocked so we can
// assert *which* trees render, not what's inside them.

vi.mock('./components/ProtectedRoute', () => ({
  default: ({ children }: { children: React.ReactNode }) => <>{children}</>,
}))

vi.mock('./components/Layout', async () => {
  const { Outlet } = await import('react-router-dom')
  return {
    default: () => (
      <div data-testid="layout">
        <Outlet />
      </div>
    ),
  }
})

vi.mock('./pages/Inbox', () => ({
  default: () => <div>INBOX_PAGE</div>,
}))

vi.mock('./pages/EpisodeDetail', () => ({
  default: () => <div>STANDALONE_EPISODE_PAGE</div>,
}))

vi.mock('./components/EpisodeReaderOverlay', () => ({
  default: () => (
    <div role="dialog" aria-modal="true">
      READER_OVERLAY
    </div>
  ),
}))

const inboxLocation = {
  pathname: '/inbox',
  search: '',
  hash: '',
  state: null,
  key: 'inbox-entry',
}

describe('App background-location route split (spec #52)', () => {
  it('renders the standalone episode page for a direct link (no navigation state)', async () => {
    render(
      <MemoryRouter initialEntries={['/podcasts/pod/episodes/ep']}>
        <App />
      </MemoryRouter>,
    )

    expect(await screen.findByText('STANDALONE_EPISODE_PAGE')).toBeInTheDocument()
    expect(screen.queryByRole('dialog')).toBeNull()
    expect(screen.queryByText('INBOX_PAGE')).toBeNull()
  })

  it('renders the inbox underneath and the reader overlay above when state carries a background location', async () => {
    render(
      <MemoryRouter
        initialEntries={[
          { pathname: '/inbox' },
          {
            pathname: '/podcasts/pod/episodes/ep',
            state: { backgroundLocation: inboxLocation },
          },
        ]}
        initialIndex={1}
      >
        <App />
      </MemoryRouter>,
    )

    // Background pass keeps rendering the inbox at its own location…
    expect(await screen.findByText('INBOX_PAGE')).toBeInTheDocument()
    // …the overlay pass renders the reader above it…
    expect(await screen.findByRole('dialog')).toBeInTheDocument()
    // …and the standalone page is not mounted.
    expect(screen.queryByText('STANDALONE_EPISODE_PAGE')).toBeNull()
  })

  it('renders no overlay for a non-episode URL that happens to carry background state', async () => {
    render(
      <MemoryRouter
        initialEntries={[
          {
            pathname: '/podcasts',
            state: { backgroundLocation: inboxLocation },
          },
        ]}
      >
        <App />
      </MemoryRouter>,
    )

    // Background pass renders the inbox (the stale background location);
    // the overlay pass matches nothing.
    expect(await screen.findByText('INBOX_PAGE')).toBeInTheDocument()
    expect(screen.queryByRole('dialog')).toBeNull()
  })
})

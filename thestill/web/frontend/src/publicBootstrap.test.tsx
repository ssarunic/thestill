// Spec #105 — the data block seeds the keys the public hooks read, and the
// anonymous-viewer hint shows the public site from the first render.
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, render, renderHook, screen, waitFor } from '@testing-library/react'
import type { ReactNode } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { AuthProvider, useAuth } from './contexts/AuthContext'
import {
  usePublicEntity,
  usePublicEntityQuotes,
  usePublicEpisode,
  usePublicEpisodeQuotes,
  usePublicPodcast,
  usePublicPodcastEpisodes,
  usePublicRecentSummaries,
  usePublicSummary,
  usePublicTopChart,
  usePublicWhoSaidWhat,
} from './hooks/usePublicApi'
import { BOOTSTRAP_ID, BOOTSTRAP_VERSION, readBootstrap, seedBootstrap, type PublicBootstrap } from './publicBootstrap'

function setBlock(text: string | null) {
  document.getElementById(BOOTSTRAP_ID)?.remove()
  if (text === null) return
  const script = document.createElement('script')
  script.type = 'application/json'
  script.id = BOOTSTRAP_ID
  script.textContent = text
  document.body.appendChild(script)
}

// One entry per query the server writes, shaped as public_prerender.query().
const ENTRIES: PublicBootstrap['queries'] = [
  { query: 'episode', args: ['show', 'ep'], data: { episode: { title: 'Ep' } } },
  { query: 'summary', args: ['show', 'ep'], data: { content: '## Gist' } },
  { query: 'episodeQuotes', args: ['show', 'ep', null, null], data: { quotes: [], next_page: null }, page_param: 1 },
  { query: 'podcastEpisodes', args: ['show'], data: { episodes: [], next_offset: null }, page_param: 0 },
  { query: 'podcast', args: ['show'], data: { podcast: { title: 'Show' } } },
  { query: 'entity', args: ['person', 'ada'], data: { entity: { name: 'Ada' } } },
  { query: 'entityQuotes', args: ['person', 'ada', 'said', null], data: { quotes: [], next_page: null }, page_param: 1 },
  { query: 'recent', args: [6], data: { episodes: [] } },
  { query: 'whoSaidWhat', args: [], data: { entity: null, quotes: [] } },
  { query: 'top', args: [null, null], data: { region: 'gb', top_podcasts: [] } },
]

describe('readBootstrap', () => {
  afterEach(() => setBlock(null))

  it('reads a block of the current version', () => {
    setBlock(JSON.stringify({ v: BOOTSTRAP_VERSION, page: 'episode', viewer: 'anonymous', queries: ENTRIES }))
    expect(readBootstrap()?.queries).toHaveLength(ENTRIES.length)
  })

  it.each([
    ['no block', null],
    ['malformed JSON', '{"v":1,'],
    ['another version', JSON.stringify({ v: BOOTSTRAP_VERSION + 1, page: 'episode', viewer: 'anonymous', queries: [] })],
    ['an unknown page', JSON.stringify({ v: BOOTSTRAP_VERSION, page: 'inbox', viewer: 'anonymous', queries: [] })],
    ['no queries', JSON.stringify({ v: BOOTSTRAP_VERSION, page: 'episode', viewer: 'anonymous' })],
  ])('ignores %s', (_label, text) => {
    setBlock(text)
    expect(readBootstrap()).toBeNull()
  })
})

describe('seedBootstrap', () => {
  const fetchMock = vi.fn(() => Promise.reject(new Error('no network in this test')))
  beforeEach(() => {
    fetchMock.mockClear()
    vi.stubGlobal('fetch', fetchMock)
  })
  afterEach(() => vi.unstubAllGlobals())

  function seeded() {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    const count = seedBootstrap(client, { v: BOOTSTRAP_VERSION, page: 'episode', viewer: 'anonymous', queries: ENTRIES })
    const wrapper = ({ children }: { children: ReactNode }) => <QueryClientProvider client={client}>{children}</QueryClientProvider>
    return { count, wrapper }
  }

  it('seeds the key each public hook reads, so nothing is fetched', () => {
    const { count, wrapper } = seeded()
    expect(count).toBe(ENTRIES.length)
    const hooks = [
      () => usePublicEpisode('show', 'ep'),
      () => usePublicSummary('show', 'ep'),
      () => usePublicEpisodeQuotes('show', 'ep', null, null),
      () => usePublicPodcastEpisodes('show'),
      () => usePublicPodcast('show'),
      () => usePublicEntity('person', 'ada'),
      () => usePublicEntityQuotes('person', 'ada', 'said', null),
      () => usePublicRecentSummaries(6),
      () => usePublicWhoSaidWhat(),
      () => usePublicTopChart(undefined, undefined),
    ]
    hooks.forEach((hook, i) => {
      const { result } = renderHook(hook, { wrapper })
      const expected = ENTRIES[i].page_param === undefined ? ENTRIES[i].data : { pages: [ENTRIES[i].data], pageParams: [ENTRIES[i].page_param] }
      expect(result.current.data, ENTRIES[i].query).toEqual(expected)
      expect(result.current.isSuccess).toBe(true)
    })
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('skips an entry it does not know', () => {
    const client = new QueryClient()
    const count = seedBootstrap(client, {
      v: BOOTSTRAP_VERSION,
      page: 'episode',
      viewer: 'anonymous',
      queries: [{ query: 'toString' as never, args: [], data: {} }, { query: 'nope' as never, args: [], data: {} }],
    })
    expect(count).toBe(0)
    expect(client.getQueryCache().getAll()).toHaveLength(0)
  })
})

describe('AuthProvider initialViewer', () => {
  afterEach(() => vi.unstubAllGlobals())

  function Probe() {
    const { isAnonymous, isAuthenticated, isLoading } = useAuth()
    return <span data-testid="auth">{`${isAnonymous}/${isAuthenticated}/${isLoading}`}</span>
  }

  function status(body: object) {
    let resolve: (value: Response) => void = () => {}
    vi.stubGlobal('fetch', vi.fn(() => new Promise<Response>((r) => (resolve = r))))
    return () => resolve(new Response(JSON.stringify(body), { status: 200 }))
  }

  it('is anonymous on the first render, before the status returns', async () => {
    const respond = status({ multi_user: true, authenticated: false, public_pages: true, user: null })
    render(
      <AuthProvider initialViewer="anonymous">
        <Probe />
      </AuthProvider>,
    )
    expect(screen.getByTestId('auth').textContent).toBe('true/false/false')
    await act(async () => respond())
    expect(screen.getByTestId('auth').textContent).toBe('true/false/false')
  })

  it('switches to the app when the status says signed in', async () => {
    const respond = status({
      multi_user: true,
      authenticated: true,
      public_pages: true,
      user: { id: 'u', email: 'a@b.c', name: null, picture: null, created_at: '', last_login_at: null, region: null, region_locked: false, is_admin: false },
    })
    render(
      <AuthProvider initialViewer="anonymous">
        <Probe />
      </AuthProvider>,
    )
    await act(async () => respond())
    await waitFor(() => expect(screen.getByTestId('auth').textContent).toBe('false/true/false'))
  })

  it('without the hint waits for the status as before', () => {
    status({ multi_user: true, authenticated: false, public_pages: true, user: null })
    render(
      <AuthProvider>
        <Probe />
      </AuthProvider>,
    )
    expect(screen.getByTestId('auth').textContent).toBe('false/false/true')
  })
})

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { render } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { Link, MemoryRouter } from 'react-router-dom'
import PublicPageViews from './PublicPageViews'

const fetchMock = vi.fn(() => Promise.resolve(new Response(null, { status: 204 })))

function bodies() {
  return fetchMock.mock.calls.map((call) => JSON.parse((call as unknown as [string, RequestInit])[1].body as string))
}

describe('PublicPageViews (spec #95)', () => {
  beforeEach(() => {
    fetchMock.mockClear()
    vi.stubGlobal('fetch', fetchMock)
    Object.defineProperty(document, 'referrer', { value: 'https://news.ycombinator.com/item?id=1', configurable: true })
  })
  afterEach(() => vi.unstubAllGlobals())

  it('posts each public route change to our own server, referrer only first', async () => {
    render(
      <MemoryRouter initialEntries={['/podcasts/hard-fork']}>
        <PublicPageViews />
        <Link to="/top">top</Link>
        <Link to="/login?next=/top">sign in</Link>
      </MemoryRouter>,
    )
    expect(fetchMock.mock.calls[0][0]).toBe('/api/public/pageview')
    await userEvent.setup().click(document.querySelector('a')!)
    await userEvent.setup().click(document.querySelectorAll('a')[1])
    expect(bodies()).toEqual([
      { path: '/podcasts/hard-fork', referrer: 'https://news.ycombinator.com/item?id=1' },
      { path: '/top', referrer: null },
    ])
  })
})

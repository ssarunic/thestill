import { afterEach, describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import Help from './Help'

vi.mock('./NotFound', () => ({ default: () => <div>NOT_FOUND</div> }))

const MARKDOWN = [
  '# Use thestill in Claude',
  '',
  'Address: `{{connector_address}}`',
  '',
  'See the [Privacy Policy](/privacy) or [Anthropic](https://support.claude.com/x).',
  '',
  'Write to {{contact_email}}. Logs are kept for {{log_retention}}.',
  '',
  '![Claude quoting three speakers](/help/claude-example.png)',
].join('\n')

function stubFetch(files: Record<string, { ok: boolean; body: string }>) {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (path: string) => {
      const file = files[path] ?? { ok: false, body: '' }
      return { ok: file.ok, status: file.ok ? 200 : 404, text: async () => file.body, json: async () => JSON.parse(file.body) }
    }),
  )
}

function renderHelp() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <Help doc="claude" />
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

afterEach(() => vi.unstubAllGlobals())

describe('Help (spec #108)', () => {
  it('renders the guide with this install’s address and operator details', async () => {
    stubFetch({
      '/help/claude.md': { ok: true, body: MARKDOWN },
      '/legal/operator.json': {
        ok: true,
        body: JSON.stringify({ draft: true, contact_email: 'help@example.test', log_retention: '30 days' }),
      },
    })
    renderHelp()
    expect(await screen.findByRole('heading', { name: 'Use thestill in Claude' })).toBeInTheDocument()
    expect(screen.getByText(`${window.location.origin}/mcp`)).toBeInTheDocument()
    expect(document.querySelector('article')?.textContent).toContain('Write to help@example.test. Logs are kept for 30 days.')
    expect(screen.getByRole('img', { name: 'Claude quoting three speakers' })).toHaveAttribute('src', '/help/claude-example.png')
    // A draft legal text does not mark the guide as a draft.
    expect(screen.queryByText(/Draft/)).not.toBeInTheDocument()
  })

  it('keeps same-site links in the app and leaves outside links alone', async () => {
    stubFetch({
      '/help/claude.md': { ok: true, body: MARKDOWN },
      '/legal/operator.json': { ok: true, body: '{}' },
    })
    renderHelp()
    expect(await screen.findByRole('link', { name: 'Privacy Policy' })).toHaveAttribute('href', '/privacy')
    expect(screen.getByRole('link', { name: 'Anthropic' })).toHaveAttribute('href', 'https://support.claude.com/x')
  })

  it('shows the not-found page when the guide is missing', async () => {
    stubFetch({ '/legal/operator.json': { ok: true, body: '{}' } })
    renderHelp()
    expect(await screen.findByText('NOT_FOUND')).toBeInTheDocument()
  })
})

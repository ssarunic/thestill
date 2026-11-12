// Spec #108 / #109 — public help pages, rendered from Markdown files in
// public/help/ so the copy can change without a code change (the same
// approach as Terms and Privacy). The first is the Claude connector guide
// at /claude, which is also the documentation URL the connector's
// directory listing points to.
//
// {{key}} placeholders are filled from public/legal/operator.json (contact
// address, log retention) plus `connector_address`, which is this
// install's own origin: the file names no host.
import { useQuery } from '@tanstack/react-query'
import ReactMarkdown from 'react-markdown'
import { Link } from 'react-router-dom'
import remarkGfm from 'remark-gfm'
import { fillLegalPlaceholders, type LegalOperator } from '../../utils/legalPlaceholders'
import NotFound from './NotFound'

export type HelpDoc = 'claude'

async function fetchOk(path: string): Promise<Response> {
  const response = await fetch(path)
  if (!response.ok) throw new Error(String(response.status))
  return response
}

function connectorAddress(): string {
  return `${window.location.origin}/mcp`
}

export default function Help({ doc }: { doc: HelpDoc }) {
  const { data, isLoading, isError } = useQuery({
    queryKey: ['public', 'help', doc],
    queryFn: async () => {
      const [markdown, operator] = await Promise.all([
        fetchOk(`/help/${doc}.md`).then((r) => r.text()),
        fetchOk('/legal/operator.json').then((r) => r.json() as Promise<LegalOperator>),
      ])
      return fillLegalPlaceholders(markdown, { ...operator, connector_address: connectorAddress() })
    },
    staleTime: 60 * 60_000,
  })
  if (isError) return <NotFound />
  return (
    <div className="ts-container" style={{ paddingTop: 40 }}>
      <article className="ts-legal ts-summary">
        {isLoading ? null : (
          <ReactMarkdown
            remarkPlugins={[remarkGfm]}
            components={{
              // Same-site links stay in the app instead of reloading it.
              a: ({ href, children }) =>
                href?.startsWith('/') ? (
                  <Link to={href} className="ts-link">
                    {children}
                  </Link>
                ) : (
                  <a href={href} className="ts-link" rel="noopener">
                    {children}
                  </a>
                ),
            }}
          >
            {data ?? ''}
          </ReactMarkdown>
        )}
      </article>
    </div>
  )
}

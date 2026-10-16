// Spec #94 — Terms and Privacy, rendered from Markdown files in
// public/legal/ so the copy can change without a code change. Operator
// details (name, address, contact, governing law) live in
// public/legal/operator.json and fill the {{key}} placeholders.
import { useQuery } from '@tanstack/react-query'
import ReactMarkdown from 'react-markdown'
import { Link } from 'react-router-dom'
import remarkGfm from 'remark-gfm'
import { fillLegalPlaceholders, type LegalOperator } from '../../utils/legalPlaceholders'
import NotFound from './NotFound'

async function fetchLegal(path: string): Promise<Response> {
  const response = await fetch(path)
  if (!response.ok) throw new Error(String(response.status))
  return response
}

export default function Legal({ doc }: { doc: 'terms' | 'privacy' }) {
  const { data, isLoading, isError } = useQuery({
    queryKey: ['public', 'legal', doc],
    queryFn: async () => {
      const [markdown, operator] = await Promise.all([
        fetchLegal(`/legal/${doc}.md`).then((r) => r.text()),
        fetchLegal('/legal/operator.json').then((r) => r.json() as Promise<LegalOperator>),
      ])
      return { text: fillLegalPlaceholders(markdown, operator), draft: operator.draft === true }
    },
    staleTime: 60 * 60_000,
  })
  if (isError) return <NotFound />
  return (
    <div className="ts-container" style={{ paddingTop: 40 }}>
      <article className="ts-legal ts-summary">
        {data?.draft && <p className="ts-legal-draft">Draft. This text is not final yet.</p>}
        {isLoading ? null : <ReactMarkdown remarkPlugins={[remarkGfm]}>{data?.text ?? ''}</ReactMarkdown>}
        <p className="ts-meta" style={{ marginTop: 32 }}>
          {doc === 'terms' ? <Link to="/privacy" className="ts-link">Privacy Policy</Link> : <Link to="/terms" className="ts-link">Terms of Service</Link>}
        </p>
      </article>
    </div>
  )
}

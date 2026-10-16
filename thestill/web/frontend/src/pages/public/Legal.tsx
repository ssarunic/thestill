// Spec #94 — Terms and Privacy, rendered from Markdown files in
// public/legal/ so the copy can change without a code change.
import { useQuery } from '@tanstack/react-query'
import ReactMarkdown from 'react-markdown'
import { Link } from 'react-router-dom'
import NotFound from './NotFound'

export default function Legal({ doc }: { doc: 'terms' | 'privacy' }) {
  const { data, isLoading, isError } = useQuery({
    queryKey: ['public', 'legal', doc],
    queryFn: async () => {
      const response = await fetch(`/legal/${doc}.md`)
      if (!response.ok) throw new Error(String(response.status))
      return response.text()
    },
    staleTime: 60 * 60_000,
  })
  if (isError) return <NotFound />
  return (
    <div className="ts-container" style={{ paddingTop: 40 }}>
      <article className="ts-legal ts-summary">
        <p className="ts-legal-draft">Draft. This copy is a placeholder, not legal advice; the final text is being written.</p>
        {isLoading ? null : <ReactMarkdown>{data ?? ''}</ReactMarkdown>}
        <p className="ts-meta" style={{ marginTop: 32 }}>
          {doc === 'terms' ? <Link to="/privacy" className="ts-link">Privacy Policy</Link> : <Link to="/terms" className="ts-link">Terms of Service</Link>}
        </p>
      </article>
    </div>
  )
}

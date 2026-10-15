import { Link } from 'react-router-dom'

/** Spec #94 — one layout for every missing, hidden or not-yet-summarised page. */
export default function NotFound({
  title = "This page isn't here",
  message = 'It may have moved, or the show asked us to take it down.',
}: {
  title?: string
  message?: string
}) {
  return (
    <div className="ts-container">
      <div className="ts-empty">
        <h1 className="ts-page-title">{title}</h1>
        <p className="ts-standfirst" style={{ margin: '12px auto 0' }}>
          {message}
        </p>
        <p style={{ marginTop: 24, display: 'flex', gap: 24, justifyContent: 'center' }}>
          <Link to="/top" className="ts-link">
            Top podcasts
          </Link>
          <Link to="/" className="ts-link">
            Home
          </Link>
        </p>
      </div>
    </div>
  )
}

/** A module-level failure: one muted line and a retry, the rest of the page stays. */
export function ModuleError({ onRetry }: { onRetry: () => void }) {
  return (
    <p className="ts-error-line">
      Couldn&apos;t load this.{' '}
      <button type="button" className="ts-btn ts-btn-text" onClick={onRetry}>
        Retry
      </button>
    </p>
  )
}

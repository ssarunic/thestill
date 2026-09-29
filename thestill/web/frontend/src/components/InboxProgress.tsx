import type { ProgressStatus } from '../utils/inbox'

export function ProgressPill({ status }: { status: ProgressStatus }) {
  const cls =
    status.kind === 'failed'
      ? 'bg-red-100 text-red-700'
      : status.kind === 'ready'
        ? 'bg-green-100 text-green-700'
        : 'bg-amber-100 text-amber-800'
  return (
    <span className={`inline-flex items-center gap-1 text-xs font-medium px-2 py-0.5 rounded ${cls}`}>
      {status.kind === 'processing' && (
        <span
          aria-hidden="true"
          className="inline-block w-2 h-2 rounded-full bg-current animate-pulse"
        />
      )}
      {status.label}
    </span>
  )
}

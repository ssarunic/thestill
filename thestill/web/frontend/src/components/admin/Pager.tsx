// Previous / next paging for the admin lists.
export interface PagerProps {
  page: number
  pageSize: number
  total: number
  hasMore: boolean
  onPage: (page: number) => void
  previousLabel?: string
  nextLabel?: string
}

export default function Pager({ page, pageSize, total, hasMore, onPage, previousLabel = '← Previous', nextLabel = 'Next →' }: PagerProps) {
  if (total <= pageSize) return null
  return (
    <div className="flex items-center gap-3 text-sm">
      <button type="button" disabled={page === 0} onClick={() => onPage(page - 1)} className="text-accent disabled:text-muted">
        {previousLabel}
      </button>
      <span className="text-muted">
        {page * pageSize + 1}–{Math.min(total, (page + 1) * pageSize)} of {total}
      </span>
      <button type="button" disabled={!hasMore} onClick={() => onPage(page + 1)} className="text-accent disabled:text-muted">
        {nextLabel}
      </button>
    </div>
  )
}

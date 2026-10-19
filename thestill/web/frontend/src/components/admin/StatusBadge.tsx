import type { AccountStatus } from '../../api/types'

const STYLES: Record<AccountStatus, string> = {
  active: 'bg-green-50 text-green-800 border-green-200',
  suspended: 'bg-amber-50 text-amber-800 border-amber-200',
  banned: 'bg-red-50 text-red-800 border-red-200',
}

export default function StatusBadge({ status }: { status: AccountStatus }) {
  return (
    <span className={`inline-flex items-center rounded-full border px-2 py-0.5 text-xs font-medium ${STYLES[status]}`}>
      {status}
    </span>
  )
}

import Button, { CheckIcon, PlusIcon } from './Button'
import type { ButtonSize } from './Button'

export type FollowStatus = 'idle' | 'pending' | 'done' | 'error'

interface FollowButtonProps {
  status: FollowStatus
  onClick: () => void
  /** Override any label; defaults are the Follow vocabulary. */
  labels?: Partial<Record<FollowStatus, string>>
  /** Icon-only below ``sm`` (list rows); the label stays for screen readers. */
  iconOnlyMobile?: boolean
  size?: ButtonSize
}

const DEFAULT_LABELS: Record<FollowStatus, string> = {
  idle: 'Follow',
  pending: 'Following…',
  done: 'Following ✓',
  error: 'Retry',
}

/**
 * The one Follow control (spec #73), shared by the Top podcasts chart and the
 * Add-podcast modal. ``done`` is a disabled success state rather than a
 * variant switch the caller has to know about.
 */
export default function FollowButton({
  status,
  onClick,
  labels,
  iconOnlyMobile = false,
  size = 'md',
}: FollowButtonProps) {
  const label = { ...DEFAULT_LABELS, ...labels }[status]
  const isDone = status === 'done'
  return (
    <Button
      onClick={onClick}
      size={size}
      variant={isDone ? 'success' : status === 'error' ? 'danger' : 'tonal'}
      isLoading={status === 'pending'}
      disabled={isDone}
      icon={isDone ? <CheckIcon /> : status === 'idle' ? <PlusIcon /> : undefined}
      iconOnlyMobile={iconOnlyMobile}
      className="shrink-0"
    >
      {label}
    </Button>
  )
}

import { useEffect, useState } from 'react'
import ConfirmDialog from '../ConfirmDialog'
import type { ButtonVariant } from '../Button'

// Spec #104 §4.2 — suspend, ban and reinstate each ask for a reason, which
// goes to the audit log and is shown only to admins.
export interface ReasonDialogProps {
  isOpen: boolean
  title: string
  description: React.ReactNode
  confirmLabel: string
  confirmVariant?: ButtonVariant
  reasonRequired?: boolean
  busy?: boolean
  /** Extra controls (the ban's "also remove feeds" checkbox). */
  children?: React.ReactNode
  onConfirm: (reason: string) => void
  onCancel: () => void
}

export default function ReasonDialog({
  isOpen,
  title,
  description,
  confirmLabel,
  confirmVariant = 'primary',
  reasonRequired = true,
  busy,
  children,
  onConfirm,
  onCancel,
}: ReasonDialogProps) {
  const [reason, setReason] = useState('')
  const missing = reasonRequired && !reason.trim()
  // Cleared when the dialog closes, not on confirm: a refused action
  // (last admin, a network error) leaves the dialog open with the reason.
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    if (!isOpen) setReason('')
  }, [isOpen])
  return (
    <ConfirmDialog
      isOpen={isOpen}
      title={title}
      confirmLabel={confirmLabel}
      confirmVariant={confirmVariant}
      busy={busy}
      onCancel={onCancel}
      onConfirm={() => {
        if (!missing) onConfirm(reason.trim())
      }}
      message={
        <div className="space-y-3">
          <div>{description}</div>
          <label className="block">
            <span className="block text-xs font-medium text-ink mb-1">
              Reason{reasonRequired ? '' : ' (optional)'}
            </span>
            <textarea
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              rows={3}
              className="w-full rounded-md border border-hairline bg-surface p-2 text-sm text-ink"
              placeholder="Kept in the audit log, shown only to admins"
            />
          </label>
          {children}
          {missing && <p className="text-xs text-muted">A reason is required.</p>}
        </div>
      }
    />
  )
}

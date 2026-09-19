import { useEffect, useRef } from 'react'
import Button, { CloseIcon, type ButtonVariant } from './Button'

// Reusable confirm modal (first consumer: the MCP connector card's Rotate
// and Revoke, spec #78 Phase 2). Same backdrop/card shell as
// AddPodcastModal so dialogs read as one family: z-[70] transient layer,
// click-outside and Escape cancel, focus lands on the confirm action.

export interface ConfirmDialogProps {
  isOpen: boolean
  title: string
  message: React.ReactNode
  confirmLabel: string
  confirmVariant?: ButtonVariant
  busy?: boolean
  onConfirm: () => void
  onCancel: () => void
}

export default function ConfirmDialog({
  isOpen,
  title,
  message,
  confirmLabel,
  confirmVariant = 'primary',
  busy = false,
  onConfirm,
  onCancel,
}: ConfirmDialogProps) {
  const confirmRef = useRef<HTMLButtonElement>(null)

  useEffect(() => {
    if (isOpen) confirmRef.current?.focus()
  }, [isOpen])

  if (!isOpen) return null

  return (
    <div
      className="fixed inset-0 z-[70] flex items-center justify-center p-4 bg-black/50"
      onClick={busy ? undefined : onCancel}
      onKeyDown={(e) => {
        if (e.key === 'Escape' && !busy) onCancel()
      }}
      role="presentation"
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="confirm-dialog-title"
        className="bg-white rounded-xl shadow-xl max-w-md w-full p-6"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between mb-3">
          <h2 id="confirm-dialog-title" className="text-lg font-semibold text-gray-900">
            {title}
          </h2>
          <Button variant="ghost" size="sm" icon={<CloseIcon />} onClick={onCancel} aria-label="Close" disabled={busy} />
        </div>
        <div className="text-sm text-gray-600 mb-5">{message}</div>
        <div className="flex justify-end gap-2">
          <Button variant="secondary" size="sm" onClick={onCancel} disabled={busy}>
            Cancel
          </Button>
          <Button ref={confirmRef} variant={confirmVariant} size="sm" onClick={onConfirm} isLoading={busy}>
            {confirmLabel}
          </Button>
        </div>
      </div>
    </div>
  )
}

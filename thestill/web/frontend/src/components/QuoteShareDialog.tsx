import { useEffect, useRef } from 'react'
import Button, { CloseIcon } from './Button'
import { useToast } from './Toast'
import { track } from '../analytics/client'
import type { QuoteShareItem } from '../api/types'

/**
 * Spec #114 — share a quote from the transcript. Shows the quote's card as
 * its link will preview, and copies the short link (or opens the phone's
 * share sheet). Only quotes that are already public reach here, so sharing
 * one makes nothing new public. Same shell as ConfirmDialog: click outside
 * or Escape closes, focus lands on the main action.
 */
export default function QuoteShareDialog({ share, onClose }: { share: QuoteShareItem | null; onClose: () => void }) {
  const { showToast } = useToast()
  const primaryRef = useRef<HTMLButtonElement>(null)
  const canShare = typeof navigator !== 'undefined' && !!navigator.share
  useEffect(() => {
    if (share) primaryRef.current?.focus()
  }, [share])
  if (!share) return null

  const url = `${window.location.origin}/q/${share.id}`
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(url)
      track('share', { kind: 'quote' }) // spec #95 usage event: the kind, never the URL
      showToast('Quote link copied', 'success')
      onClose()
    } catch {
      showToast('Failed to copy link', 'error')
    }
  }
  const shareNative = async () => {
    try {
      await navigator.share({ title: `${share.speaker}`, text: `“${share.text}”`, url })
      track('share', { kind: 'quote' })
      onClose()
    } catch (err) {
      if (err instanceof Error && err.name !== 'AbortError') showToast('Failed to share', 'error')
    }
  }

  return (
    <div
      className="fixed inset-0 z-[70] flex items-center justify-center p-4 bg-black/50"
      onClick={onClose}
      onKeyDown={(e) => {
        if (e.key !== 'Escape') return
        // Escape closes this dialog only: the reader overlay around it
        // (spec #52) closes on an Escape nobody has handled.
        e.preventDefault()
        e.stopPropagation()
        onClose()
      }}
      role="presentation"
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="quote-share-title"
        className="bg-white rounded-xl shadow-xl max-w-lg w-full p-5"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between mb-3">
          <h2 id="quote-share-title" className="text-lg font-semibold text-gray-900">
            Share this quote
          </h2>
          <Button variant="ghost" size="sm" icon={<CloseIcon />} onClick={onClose} aria-label="Close" />
        </div>
        <img
          src={`/q/${share.id}.png`}
          alt={`“${share.text}” — ${share.speaker}`}
          width={1200}
          height={630}
          className="w-full h-auto rounded-lg border border-gray-200 bg-gray-100"
        />
        <p className="mt-3 text-sm text-gray-600">
          The link shows this card wherever you paste it, and opens the episode at this moment.
        </p>
        <div className="mt-4 flex justify-end gap-2">
          {canShare && (
            <Button variant="secondary" size="sm" onClick={shareNative}>
              Share…
            </Button>
          )}
          <Button ref={primaryRef} variant="primary" size="sm" onClick={copy}>
            Copy link
          </Button>
        </div>
      </div>
    </div>
  )
}

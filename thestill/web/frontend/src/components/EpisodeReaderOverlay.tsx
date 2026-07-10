import { useCallback, useEffect, useRef } from 'react'
import { useNavigate } from 'react-router-dom'
import EpisodeReader from './EpisodeReader'

// Elements the focus trap cycles through. Mirrors what a browser considers
// tabbable closely enough for this panel's content.
const FOCUSABLE_SELECTOR =
  'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])'

/**
 * Spec #52 — inbox reader overlay chrome. Rendered by App's overlay route
 * pass while navigation state carries a `backgroundLocation`; the inbox
 * stays mounted underneath. Desktop: right-aligned slide-over panel above a
 * scrim. Mobile (< lg): the panel is full-width, so `← Inbox` is the
 * primary exit.
 *
 * Every close affordance (Esc, scrim, `← Inbox`) is `history.back()` — one
 * pushed entry on open, one pop on close, restoring the inbox's live state.
 */
export default function EpisodeReaderOverlay() {
  const navigate = useNavigate()
  const panelRef = useRef<HTMLDivElement>(null)
  const scrollRef = useRef<HTMLDivElement>(null)

  // Captured at mount: the history index of the entry the reader opened on.
  // Every close affordance pops back to the entry *before* it (the inbox) in
  // one step, skipping any in-reader entries pushed since — e.g. a
  // summary→transcript citation jump (spec #54). Browser Back still steps
  // through those entries individually. Falls back to a single pop if the
  // router does not expose an index.
  const openIdxRef = useRef<number | null>(null)
  useEffect(() => {
    const idx = (window.history.state as { idx?: number } | null)?.idx
    openIdxRef.current = typeof idx === 'number' ? idx : null
  }, [])

  const close = useCallback(() => {
    const openIdx = openIdxRef.current
    const currentIdx = (window.history.state as { idx?: number } | null)?.idx
    if (openIdx != null && typeof currentIdx === 'number') {
      const delta = openIdx - 1 - currentIdx
      navigate(delta < 0 ? delta : -1)
      return
    }
    navigate(-1)
  }, [navigate])

  // Esc closes — unless a surface layered above the overlay (e.g. the ⌘K
  // command bar, which autofocuses its input) owns the keypress. Two guards:
  // `defaultPrevented` respects handlers that claimed the event, and the
  // focus check keeps Esc from tearing down the reader while focus sits in
  // another layer. Focus on the body itself (clicks on non-focusable text)
  // still counts as "in the reader".
  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key !== 'Escape' || e.defaultPrevented) return
      const active = document.activeElement
      if (active && active !== document.body && !panelRef.current?.contains(active)) return
      close()
    }
    document.addEventListener('keydown', onKeyDown)
    return () => document.removeEventListener('keydown', onKeyDown)
  }, [close])

  // Lock body scroll behind the overlay (the panel scrolls its own div).
  useEffect(() => {
    const previous = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => {
      document.body.style.overflow = previous
    }
  }, [])

  // Move focus into the panel on open; restore it to the originating
  // element (the clicked inbox row) on close.
  useEffect(() => {
    const origin = document.activeElement instanceof HTMLElement ? document.activeElement : null
    panelRef.current?.focus()
    return () => origin?.focus()
  }, [])

  // Keep Tab / Shift+Tab cycling inside the panel while it is open.
  const trapFocus = useCallback((e: React.KeyboardEvent) => {
    if (e.key !== 'Tab') return
    const panel = panelRef.current
    if (!panel) return
    const focusable = panel.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR)
    if (focusable.length === 0) return
    const first = focusable[0]
    const last = focusable[focusable.length - 1]
    if (e.shiftKey && (document.activeElement === first || document.activeElement === panel)) {
      e.preventDefault()
      last.focus()
    } else if (!e.shiftKey && document.activeElement === last) {
      e.preventDefault()
      first.focus()
    }
  }, [])

  return (
    <div className="fixed inset-0 z-50">
      {/* Scrim — visible beside the panel on lg+; the panel covers it below. */}
      <div className="absolute inset-0 bg-black/50" onClick={close} aria-hidden="true" />

      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-label="Episode reader"
        tabIndex={-1}
        onKeyDown={trapFocus}
        className="absolute inset-y-0 right-0 flex w-full flex-col bg-gray-50 shadow-xl outline-none lg:max-w-4xl"
      >
        <header className="flex items-center border-b border-gray-200 bg-white px-4 py-3 sm:px-6">
          <button
            type="button"
            onClick={close}
            className="inline-flex items-center gap-2 rounded-lg px-2 py-1.5 text-sm font-medium text-gray-600 hover:bg-gray-100 hover:text-gray-900 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-400 transition-colors"
          >
            <svg className="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M10 19l-7-7m0 0l7-7m-7 7h18" />
            </svg>
            Inbox
          </button>
        </header>

        <div ref={scrollRef} className="flex-1 overflow-y-auto">
          <div className="p-4 md:p-6 lg:p-8">
            <EpisodeReader scrollContainerRef={scrollRef} />
          </div>
        </div>
      </div>
    </div>
  )
}

import { useEffect, useLayoutEffect, useRef, useState, type KeyboardEvent } from 'react'
import type { AnnotatedSegment } from '../../api/types'
import { useDismissablePopover } from '../../hooks/useDismissablePopover'
import { wordIndexAtCaret, type SegmentEditing } from './segmentEditing'

// Spec #110 §5.4 — the two editing controls that live on a transcript row:
// the speaker name as a picker, and the text as an in-place field (Enter
// saves; Enter with the caret inside unchanged text splits there on a word
// boundary; Escape cancels; leaving the field saves). Merging is the handle
// between rows, not a keystroke in the field.

interface SpeakerPickerProps {
  segment: AnnotatedSegment
  editing: SegmentEditing
  color: string
}

export function SpeakerPicker({ segment, editing, color }: SpeakerPickerProps) {
  const { open, setOpen, wrapRef } = useDismissablePopover<HTMLSpanElement>()
  const current = editing.labelsBySegmentId.get(segment.id) ?? null
  const name = segment.speaker ?? 'Unknown'
  return (
    <span ref={wrapRef} className="relative inline-block">
      <button
        type="button"
        onClick={(e) => {
          e.stopPropagation()
          setOpen((o) => !o)
        }}
        disabled={editing.disabled}
        aria-haspopup="true"
        aria-expanded={open}
        title="Change speaker"
        className="max-w-full truncate rounded px-1 -mx-1 text-left font-sans text-sm font-semibold tracking-tight hover:bg-gray-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-400 disabled:opacity-60"
        style={{ color }}
      >
        {name}
      </button>
      {open && (
        <span
          role="group"
          aria-label="Speaker"
          className="absolute left-0 top-full z-30 mt-1 block w-56 overflow-hidden rounded-lg border border-gray-200 bg-white py-1 text-sm font-normal shadow-lg"
          onClick={(e) => e.stopPropagation()}
        >
          {editing.speakers.map((speaker) => (
            <button
              key={speaker.label}
              type="button"
              onClick={() => {
                setOpen(false)
                if (speaker.label !== current) editing.onSetSpeaker(segment, { label: speaker.label })
              }}
              aria-pressed={speaker.label === current}
              className={`flex min-h-[36px] w-full items-center justify-between px-3 text-left text-gray-700 hover:bg-gray-50 focus:bg-gray-50 focus:outline-none ${
                speaker.label === current ? 'bg-primary-50' : ''
              }`}
            >
              <span className="truncate">{speaker.name ?? speaker.label}</span>
              <span className="ml-2 shrink-0 text-xs text-muted">{speaker.label}</span>
            </button>
          ))}
          <button
            type="button"
            onClick={() => {
              setOpen(false)
              editing.onPickSpeakerName(segment)
            }}
            className="flex min-h-[36px] w-full items-center border-t border-hairline px-3 text-left text-gray-700 hover:bg-gray-50 focus:bg-gray-50 focus:outline-none"
          >
            Someone else…
          </button>
        </span>
      )}
    </span>
  )
}

interface EditableTextProps {
  segment: AnnotatedSegment
  editing: SegmentEditing
  /** Rendered when the segment is not the one being edited. */
  children: React.ReactNode
}

export function EditableText({ segment, editing, children }: EditableTextProps) {
  const isOpen = editing.editingSegmentId === segment.id
  const [draft, setDraft] = useState(segment.text)
  const ref = useRef<HTMLTextAreaElement>(null)

  useEffect(() => {
    if (isOpen) {
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setDraft(segment.text)
    }
  }, [isOpen, segment.text])

  useLayoutEffect(() => {
    const el = ref.current
    if (!isOpen || !el) return
    el.style.height = 'auto'
    el.style.height = `${el.scrollHeight}px`
    if (document.activeElement !== el) {
      el.focus()
      el.setSelectionRange(el.value.length, el.value.length)
    }
  }, [isOpen, draft])

  const commit = () => {
    const text = draft.trim()
    if (text && text !== segment.text) editing.onSetText(segment, text)
    editing.onEditSegment(null)
  }

  const onKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    const el = e.currentTarget
    if (e.key === 'Escape') {
      e.preventDefault()
      editing.onEditSegment(null)
      return
    }
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      const text = draft.trim()
      const changed = text && text !== segment.text
      const index = draft === segment.text ? wordIndexAtCaret(draft, el.selectionStart) : null
      if (changed) {
        editing.onSetText(segment, text)
      } else if (index !== null) {
        // Unchanged text with the caret inside it: split here.
        editing.onSplit(segment, index)
      }
      editing.onEditSegment(null)
    }
  }

  if (!isOpen) return <>{children}</>

  return (
    <textarea
      ref={ref}
      value={draft}
      onChange={(e) => setDraft(e.target.value)}
      onKeyDown={onKeyDown}
      onBlur={commit}
      onClick={(e) => e.stopPropagation()}
      aria-label="Segment text"
      rows={1}
      className="block w-full resize-none rounded-md border border-primary-300 bg-white px-2 py-1 text-base leading-[1.7] text-gray-800 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-400"
    />
  )
}

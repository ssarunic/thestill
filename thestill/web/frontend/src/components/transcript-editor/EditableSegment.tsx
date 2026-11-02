import { useEffect, useLayoutEffect, useRef, useState, type KeyboardEvent } from 'react'
import type { AnnotatedSegment, SegmentKind } from '../../api/types'
import { useDismissablePopover } from '../../hooks/useDismissablePopover'
import { SEGMENT_KINDS, wordIndexAtCaret, type SegmentEditing } from './segmentEditing'

// Spec #110 §5 "Segments" — the editing affordances on one transcript row:
// click the speaker name to reassign, edit text inline (Enter splits at the
// caret on a word boundary, Backspace at the start merges with the previous
// segment), a kind picker. The row's layout, colours and timestamp are the
// viewer's; this component only replaces its body in edit mode.

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
        className="rounded px-1 -mx-1 font-sans font-semibold tracking-tight underline decoration-dotted underline-offset-4 hover:bg-gray-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-400 disabled:opacity-60"
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

interface KindPickerProps {
  segment: AnnotatedSegment
  editing: SegmentEditing
}

export function KindPicker({ segment, editing }: KindPickerProps) {
  return (
    <select
      value={segment.kind}
      disabled={editing.disabled}
      aria-label="Segment kind"
      onClick={(e) => e.stopPropagation()}
      onChange={(e) => {
        const kind = e.target.value as SegmentKind
        if (kind !== segment.kind) editing.onSetKind(segment, kind)
      }}
      className="ml-2 rounded border border-gray-200 bg-white px-1 py-0 align-baseline font-mono text-[10px] uppercase tracking-wider text-gray-500 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-400"
    >
      {SEGMENT_KINDS.map(({ kind, label }) => (
        <option key={kind} value={kind}>
          {label}
        </option>
      ))}
    </select>
  )
}

interface EditableTextProps {
  segment: AnnotatedSegment
  editing: SegmentEditing
  /** Rendered when the segment is not the one being edited. */
  children: React.ReactNode
}

/**
 * The segment body: plain text that opens into a textarea on click. Enter
 * saves the text and, with the caret inside it, splits there on a word
 * boundary; Backspace at the very start merges with the previous segment;
 * Escape cancels; leaving the field saves a change.
 */
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
      return
    }
    if (e.key === 'Backspace' && el.selectionStart === 0 && el.selectionEnd === 0 && draft === segment.text) {
      e.preventDefault()
      editing.onEditSegment(null)
      editing.onMergeWithPrevious(segment)
    }
  }

  if (!isOpen) {
    return (
      <span
        role="button"
        tabIndex={editing.disabled ? -1 : 0}
        aria-label="Edit text"
        title="Click to edit text"
        onClick={(e) => {
          e.stopPropagation()
          if (!editing.disabled) editing.onEditSegment(segment.id)
        }}
        onKeyDown={(e) => {
          if ((e.key === 'Enter' || e.key === ' ') && !editing.disabled) {
            e.preventDefault()
            e.stopPropagation()
            editing.onEditSegment(segment.id)
          }
        }}
        className="rounded px-0.5 -mx-0.5 hover:bg-gray-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-400"
      >
        {children}
      </span>
    )
  }

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

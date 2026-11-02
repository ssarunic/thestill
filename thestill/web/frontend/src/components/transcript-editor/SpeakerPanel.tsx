import { useMemo, useState, type ReactNode } from 'react'
import Button, { MoreIcon } from '../Button'
import { useDismissablePopover } from '../../hooks/useDismissablePopover'
import type { AnnotatedSegment, TranscriptSpeaker } from '../../api/types'
import { buildSpeakerColorMap, resolveSpeakerColor } from '../../utils/speakerColors'
import { isUnnamed } from './segmentEditing'

// Spec #110 §5 "Speaker panel" — one row per diarization label with its
// colour, current name, entity chip and segment count. Row menu: Rename or
// link, Merge into…, Save as podcast host. Clicking a row filters the
// transcript to that label's segments, so an admin can listen to a few
// before naming.

interface SpeakerPanelProps {
  speakers: TranscriptSpeaker[]
  /** Segments as the transcript renders them: the colour map must match the viewer's. */
  segments: AnnotatedSegment[]
  selectedLabel: string | null
  onSelect: (label: string | null) => void
  onRename: (speaker: TranscriptSpeaker) => void
  onMerge: (speaker: TranscriptSpeaker, into: TranscriptSpeaker) => void
  onSaveAsHost: (speaker: TranscriptSpeaker) => void
  disabled?: boolean
}

function MenuItem({ onSelect, children }: { onSelect: () => void; children: ReactNode }) {
  return (
    <button
      type="button"
      onClick={onSelect}
      className="flex min-h-[36px] w-full items-center px-3 text-left text-sm text-gray-700 hover:bg-gray-50 focus:bg-gray-50 focus:outline-none"
    >
      {children}
    </button>
  )
}

function RowMenu({
  speaker,
  others,
  onRename,
  onMerge,
  onSaveAsHost,
  disabled,
}: {
  speaker: TranscriptSpeaker
  others: TranscriptSpeaker[]
  onRename: () => void
  onMerge: (into: TranscriptSpeaker) => void
  onSaveAsHost: () => void
  disabled: boolean
}) {
  const { open, setOpen, wrapRef } = useDismissablePopover()
  const [merging, setMerging] = useState(false)
  const select = (action: () => void) => () => {
    setOpen(false)
    setMerging(false)
    action()
  }
  const unnamed = isUnnamed(speaker)
  return (
    <div ref={wrapRef} className="relative">
      <Button
        variant="ghost"
        size="sm"
        icon={<MoreIcon />}
        aria-label={`Actions for ${speaker.name ?? speaker.label}`}
        aria-expanded={open}
        disabled={disabled}
        onClick={(e) => {
          e.stopPropagation()
          setMerging(false)
          setOpen((o) => !o)
        }}
      />
      {open && (
        <div
          role="group"
          aria-label={`Actions for ${speaker.name ?? speaker.label}`}
          className="absolute right-0 top-full z-30 mt-1 w-56 overflow-hidden rounded-lg border border-gray-200 bg-white py-1 shadow-lg"
          onClick={(e) => e.stopPropagation()}
        >
          {merging ? (
            <>
              <p className="px-3 py-1 text-[11px] uppercase tracking-wide text-muted">Merge into</p>
              {others.map((other) => (
                <MenuItem key={other.label} onSelect={select(() => onMerge(other))}>
                  {other.name ?? other.label}
                </MenuItem>
              ))}
              <MenuItem onSelect={() => setMerging(false)}>← Back</MenuItem>
            </>
          ) : (
            <>
              <MenuItem onSelect={select(onRename)}>{unnamed ? 'Name or link…' : 'Rename or link…'}</MenuItem>
              {others.length > 0 && <MenuItem onSelect={() => setMerging(true)}>Merge into…</MenuItem>}
              {!unnamed && <MenuItem onSelect={select(onSaveAsHost)}>Save as podcast host</MenuItem>}
            </>
          )}
        </div>
      )}
    </div>
  )
}

export default function SpeakerPanel({
  speakers,
  segments,
  selectedLabel,
  onSelect,
  onRename,
  onMerge,
  onSaveAsHost,
  disabled = false,
}: SpeakerPanelProps) {
  const colors = useMemo(
    () => buildSpeakerColorMap(segments.filter((s) => s.kind === 'content' || s.kind === 'filler').map((s) => s.speaker)),
    [segments],
  )
  if (speakers.length === 0) return null
  return (
    <section aria-label="Speakers" className="mb-3 rounded-lg border border-hairline bg-surface">
      <div className="flex items-center justify-between px-3 py-2">
        <h3 className="text-xs font-semibold uppercase tracking-wide text-muted">Speakers</h3>
        {selectedLabel && (
          <button type="button" onClick={() => onSelect(null)} className="text-xs text-primary-700 hover:underline">
            Show all segments
          </button>
        )}
      </div>
      <ul className="divide-y divide-hairline">
        {speakers.map((speaker) => {
          const unnamed = isUnnamed(speaker)
          const display = speaker.name ?? speaker.label
          const color = unnamed ? '#4b5563' : resolveSpeakerColor(display, colors)
          const selected = selectedLabel === speaker.label
          return (
            <li key={speaker.label}>
              <div
                role="button"
                tabIndex={0}
                aria-pressed={selected}
                onClick={() => onSelect(selected ? null : speaker.label)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' || e.key === ' ') {
                    e.preventDefault()
                    onSelect(selected ? null : speaker.label)
                  }
                }}
                className={`flex items-center gap-3 px-3 py-2 text-sm transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-400 ${
                  selected ? 'bg-primary-50' : 'hover:bg-gray-50'
                }`}
                data-testid={`speaker-row-${speaker.label}`}
              >
                <span className="h-3 w-3 shrink-0 rounded-full" style={{ backgroundColor: color }} aria-hidden="true" />
                <span className="min-w-0 flex-1">
                  <span className={`block truncate font-medium ${unnamed ? 'italic text-muted' : 'text-ink'}`}>
                    {unnamed ? 'Unnamed speaker' : display}
                  </span>
                  <span className="block truncate text-xs text-muted">
                    {speaker.label} · {speaker.segment_ids.length} segment{speaker.segment_ids.length === 1 ? '' : 's'}
                    {speaker.entity_id && (
                      <>
                        {' · '}
                        <span className="rounded bg-primary-50 px-1 text-primary-800">linked</span>
                      </>
                    )}
                  </span>
                </span>
                <RowMenu
                  speaker={speaker}
                  others={speakers.filter((s) => s.label !== speaker.label)}
                  onRename={() => onRename(speaker)}
                  onMerge={(into) => onMerge(speaker, into)}
                  onSaveAsHost={() => onSaveAsHost(speaker)}
                  disabled={disabled}
                />
              </div>
            </li>
          )
        })}
      </ul>
    </section>
  )
}

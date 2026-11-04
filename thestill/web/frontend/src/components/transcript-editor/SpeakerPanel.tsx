import { useState, type ReactNode } from 'react'
import Button, { MoreIcon, PlayIcon } from '../Button'
import { useDismissablePopover } from '../../hooks/useDismissablePopover'
import type { TranscriptSpeaker } from '../../api/types'
import { isUnnamed } from './segmentEditing'

// Spec #110 §5.3 — one row per diarization label with the instruments for
// judging a voice: play a sample, step through its turns, focus it (dims
// the others, never filters), and the menu to name, merge or save it.

interface SpeakerPanelProps {
  speakers: TranscriptSpeaker[]
  colors: Map<string, string>
  focusLabel: string | null
  onFocus: (label: string | null) => void
  onPlaySample: (speaker: TranscriptSpeaker) => void
  onStep: (speaker: TranscriptSpeaker, delta: -1 | 1) => void
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
  const display = speaker.name ?? speaker.label
  return (
    <div ref={wrapRef} className="relative">
      <Button
        variant="ghost"
        size="sm"
        icon={<MoreIcon />}
        aria-label={`Actions for ${display}`}
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
          aria-label={`Actions for ${display}`}
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
  colors,
  focusLabel,
  onFocus,
  onPlaySample,
  onStep,
  onRename,
  onMerge,
  onSaveAsHost,
  disabled = false,
}: SpeakerPanelProps) {
  if (speakers.length === 0) return null
  const stepClass =
    'inline-flex h-7 w-7 items-center justify-center rounded-md text-gray-500 hover:bg-gray-100 hover:text-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-400 disabled:opacity-40'
  return (
    <section aria-label="Speakers">
      <div className="flex items-center justify-between px-3 py-2">
        <h3 className="text-xs font-semibold uppercase tracking-wide text-muted">Speakers</h3>
        {focusLabel && (
          <button type="button" onClick={() => onFocus(null)} className="text-xs text-primary-700 hover:underline">
            Clear focus
          </button>
        )}
      </div>
      <ul className="divide-y divide-hairline border-y border-hairline">
        {speakers.map((speaker) => {
          const unnamed = isUnnamed(speaker)
          const display = speaker.name ?? speaker.label
          const color = colors.get(speaker.label) ?? '#4b5563'
          const focused = focusLabel === speaker.label
          return (
            <li key={speaker.label}>
              <div
                role="button"
                tabIndex={0}
                aria-pressed={focused}
                onClick={() => onFocus(focused ? null : speaker.label)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' || e.key === ' ') {
                    e.preventDefault()
                    onFocus(focused ? null : speaker.label)
                  }
                }}
                className={`px-3 py-2 text-sm transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-400 ${
                  focused ? 'bg-primary-50' : 'hover:bg-gray-50'
                }`}
                data-testid={`speaker-row-${speaker.label}`}
              >
                <div className="flex items-center gap-2.5">
                  <span className="h-3 w-3 shrink-0 rounded-full" style={{ backgroundColor: color }} aria-hidden="true" />
                  <span className="min-w-0 flex-1">
                    <span
                      className={`block truncate font-medium ${unnamed ? 'italic text-muted' : 'text-ink'}`}
                      title={unnamed ? 'Nobody has named this voice yet' : display}
                    >
                      {unnamed ? 'Unnamed' : display}
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
                <div className="mt-1 flex items-center gap-1 pl-[1.4rem]" onClick={(e) => e.stopPropagation()}>
                  <button
                    type="button"
                    onClick={() => onPlaySample(speaker)}
                    aria-label={`Play a sample of ${display}`}
                    title="Play a sample of this voice"
                    className={`${stepClass} text-primary-700`}
                  >
                    <span className="h-4 w-4">
                      <PlayIcon />
                    </span>
                  </button>
                  <button type="button" onClick={() => onStep(speaker, -1)} aria-label={`Previous turn of ${display}`} title="Previous turn" className={stepClass}>
                    ◀
                  </button>
                  <button type="button" onClick={() => onStep(speaker, 1)} aria-label={`Next turn of ${display}`} title="Next turn" className={stepClass}>
                    ▶
                  </button>
                </div>
              </div>
            </li>
          )
        })}
      </ul>
    </section>
  )
}

import { useEffect, useRef, useState } from 'react'
import Button, { CloseIcon } from '../Button'
import { searchTranscriptPeople } from '../../api/transcriptEdits'
import type { TranscriptPerson } from '../../api/types'

// Spec #110 §1 "Rename speaker" — a name, optionally linked to a person
// entity (search the entity index; free text allowed). Not free text only,
// because names drive entity mentions and quotes. Same dialog shell as
// ConfirmDialog so dialogs read as one family.

export interface SpeakerDialogResult {
  name: string
  entityId: string | null
}

interface SpeakerDialogProps {
  isOpen: boolean
  title: string
  episodeId: string
  initialName: string
  initialEntityId: string | null
  busy?: boolean
  onConfirm: (result: SpeakerDialogResult) => void
  onCancel: () => void
}

export default function SpeakerDialog({
  isOpen,
  title,
  episodeId,
  initialName,
  initialEntityId,
  busy = false,
  onConfirm,
  onCancel,
}: SpeakerDialogProps) {
  const [name, setName] = useState(initialName)
  const [entityId, setEntityId] = useState<string | null>(initialEntityId)
  const [people, setPeople] = useState<TranscriptPerson[]>([])
  const inputRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    if (!isOpen) return
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setName(initialName)
    setEntityId(initialEntityId)
    setPeople([])
    inputRef.current?.focus()
    inputRef.current?.select()
  }, [isOpen, initialName, initialEntityId])

  // Typeahead over person entities; each keystroke cancels the last call.
  useEffect(() => {
    if (!isOpen) return
    const query = name.trim()
    if (query.length < 2) {
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setPeople([])
      return
    }
    const controller = new AbortController()
    const timer = window.setTimeout(() => {
      searchTranscriptPeople(episodeId, query, controller.signal)
        .then((result) => setPeople(result.people))
        .catch(() => {
          /* aborted or offline: keep the last list */
        })
    }, 150)
    return () => {
      controller.abort()
      window.clearTimeout(timer)
    }
  }, [isOpen, name, episodeId])

  if (!isOpen) return null
  const trimmed = name.trim()
  const linked = entityId ? people.find((p) => p.id === entityId) : undefined

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
        aria-labelledby="speaker-dialog-title"
        className="bg-white rounded-xl shadow-xl max-w-md w-full p-6"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between mb-3">
          <h2 id="speaker-dialog-title" className="text-lg font-semibold text-gray-900">
            {title}
          </h2>
          <Button variant="ghost" size="sm" icon={<CloseIcon />} onClick={onCancel} aria-label="Close" disabled={busy} />
        </div>
        <form
          className="space-y-3"
          onSubmit={(e) => {
            e.preventDefault()
            if (trimmed) onConfirm({ name: trimmed, entityId })
          }}
        >
          <label className="block text-sm">
            <span className="mb-1 block text-xs font-medium text-ink">Name</span>
            <input
              ref={inputRef}
              type="text"
              value={name}
              onChange={(e) => {
                setName(e.target.value)
                // Typing a different name unlinks: the link belongs to a name.
                // Before the typeahead has resolved the linked person, the
                // name it came in with stands for it.
                const linkedName = linked?.canonical_name ?? initialName.trim()
                if (entityId && e.target.value.trim() !== linkedName) setEntityId(null)
              }}
              maxLength={200}
              className="w-full rounded-md border border-gray-200 bg-white px-3 py-1.5 text-sm placeholder:text-gray-400 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-400"
              placeholder="Who is speaking?"
              aria-label="Speaker name"
            />
          </label>
          <div>
            <p className="mb-1 text-xs font-medium text-ink">Link to a person</p>
            {entityId ? (
              <div className="flex items-center justify-between rounded-md border border-primary-200 bg-primary-50 px-3 py-1.5 text-sm">
                <span className="truncate text-primary-900">{linked?.canonical_name ?? entityId}</span>
                <button type="button" onClick={() => setEntityId(null)} className="text-xs text-primary-700 hover:underline">
                  Unlink
                </button>
              </div>
            ) : people.length > 0 ? (
              <ul className="max-h-40 overflow-y-auto rounded-md border border-gray-200" aria-label="Matching people">
                {people.map((person) => (
                  <li key={person.id}>
                    <button
                      type="button"
                      onClick={() => {
                        setEntityId(person.id)
                        setName(person.canonical_name)
                      }}
                      className="flex w-full items-center justify-between px-3 py-1.5 text-left text-sm hover:bg-gray-50 focus:bg-gray-50 focus:outline-none"
                    >
                      <span>{person.canonical_name}</span>
                      {person.matched_alias && <span className="text-xs text-muted">{person.matched_alias}</span>}
                    </button>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="text-xs text-muted">
                {trimmed.length < 2 ? 'Type a name to search the people index.' : 'No matching person; the name is kept as plain text.'}
              </p>
            )}
          </div>
          <div className="flex justify-end gap-2 pt-2">
            <Button variant="secondary" size="sm" onClick={onCancel} disabled={busy} type="button">
              Cancel
            </Button>
            <Button variant="primary" size="sm" type="submit" isLoading={busy} disabled={!trimmed}>
              Save
            </Button>
          </div>
        </form>
      </div>
    </div>
  )
}

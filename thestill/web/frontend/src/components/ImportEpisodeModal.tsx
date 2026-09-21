import { useState, useCallback } from 'react'
import { Link } from 'react-router-dom'
import { useQueryClient } from '@tanstack/react-query'
import { importEpisode } from '../api/client'
import type { ImportPayload } from '../api/types'
import Button, { CloseIcon } from './Button'

interface ImportEpisodeModalProps {
  isOpen: boolean
  onClose: () => void
}

type ImportState =
  | { kind: 'idle' }
  | { kind: 'submitting' }
  | { kind: 'success'; result: ImportPayload }
  | { kind: 'error'; message: string }

// The shell mounts the content only while open, so the form starts fresh on
// every open.
export default function ImportEpisodeModal({ isOpen, onClose }: ImportEpisodeModalProps) {
  if (!isOpen) return null
  return <ImportEpisodeModalContent onClose={onClose} />
}

function ImportEpisodeModalContent({ onClose }: Pick<ImportEpisodeModalProps, 'onClose'>) {
  const queryClient = useQueryClient()
  const [url, setUrl] = useState('')
  const [state, setState] = useState<ImportState>({ kind: 'idle' })

  const handleSubmit = useCallback(
    async (e?: React.FormEvent) => {
      e?.preventDefault()
      const trimmed = url.trim()
      if (!trimmed) {
        setState({ kind: 'error', message: 'Paste a link first.' })
        return
      }
      setState({ kind: 'submitting' })
      try {
        const response = await importEpisode({ url: trimmed })
        setState({ kind: 'success', result: response.import })
        // Refresh inbox so the new row shows up; podcasts list invalidates so
        // an auto-added channel appears once the user follows it.
        queryClient.invalidateQueries({ queryKey: ['inbox'] })
        queryClient.invalidateQueries({ queryKey: ['inbox', 'unread-count'] })
      } catch (err) {
        setState({
          kind: 'error',
          message: err instanceof Error ? err.message : 'Import failed.',
        })
      }
    },
    [url, queryClient],
  )


  const submitting = state.kind === 'submitting'

  return (
    <div
      className="fixed inset-0 z-[70] flex items-center justify-center p-4 bg-black/50"
      onClick={onClose}
      onKeyDown={(e) => {
        if (e.key === 'Escape') onClose()
      }}
    >
      <div
        className="bg-white rounded-xl shadow-xl max-w-lg w-full p-6"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-xl font-semibold text-gray-900">Import episode</h2>
          <Button
            variant="ghost"
            size="sm"
            icon={<CloseIcon />}
            onClick={onClose}
            aria-label="Close"
          />
        </div>

        {state.kind === 'success' ? (
          <ImportSuccess result={state.result} onClose={onClose} />
        ) : (
          <form onSubmit={handleSubmit}>
            <input
              autoFocus
              type="url"
              value={url}
              onChange={(e) => {
                setUrl(e.target.value)
                if (state.kind === 'error') setState({ kind: 'idle' })
              }}
              placeholder="Paste a YouTube, Apple Podcasts, or Spotify episode link, or an audio file URL"
              aria-label="Episode URL"
              disabled={submitting}
              className="w-full px-4 py-3 border rounded-lg text-gray-900 placeholder-gray-400 focus:ring-2 focus:ring-primary-500 focus:border-primary-500 transition-colors bg-white disabled:bg-gray-50"
            />

            <p className="mt-2 text-xs text-gray-500">
              Supported: YouTube videos, Apple Podcasts and Spotify episode links, and direct
              audio links (.mp3, .m4a, .opus, .ogg, .wav). Spotify links are matched to the
              show&apos;s public feed, so Spotify exclusives can&apos;t be imported.
            </p>

            {state.kind === 'error' && (
              <p className="mt-3 text-sm text-red-600">{state.message}</p>
            )}

            <div className="mt-4 flex items-center justify-end gap-2">
              <Button variant="ghost" onClick={onClose} type="button" disabled={submitting}>
                Cancel
              </Button>
              <Button type="submit" disabled={submitting || !url.trim()}>
                {submitting ? 'Importing…' : 'Import'}
              </Button>
            </div>
          </form>
        )}
      </div>
    </div>
  )
}

interface ImportSuccessProps {
  result: ImportPayload
  onClose: () => void
}

function ImportSuccess({ result, onClose }: ImportSuccessProps) {
  const heading = result.deduplicated
    ? 'Already in your inbox'
    : 'Importing — this may take a few minutes'

  return (
    <div>
      <div className="bg-green-50 border border-green-200 rounded-lg p-4">
        <h3 className="text-sm font-medium text-green-900">{heading}</h3>
        <p className="mt-1 text-sm text-green-800 truncate">{result.title}</p>
        {result.source_handle && (
          <p className="text-xs text-green-700 truncate">{result.source_handle}</p>
        )}
      </div>

      {result.parent && !result.deduplicated && (
        <div className="mt-4 border border-gray-200 rounded-lg p-4">
          <p className="text-sm text-gray-700">
            This episode is from{' '}
            <span className="font-medium text-gray-900">{result.parent.title}</span>. Follow the
            channel to keep getting new episodes.
          </p>
          <div className="mt-3">
            <Link
              to={`/podcasts/${result.parent.slug || result.parent.id}`}
              onClick={onClose}
              className="inline-flex items-center px-3 py-1.5 rounded-md text-xs font-medium bg-primary-900 text-white hover:bg-primary-800"
            >
              View channel
            </Link>
          </div>
        </div>
      )}

      <div className="mt-4 flex items-center justify-end gap-2">
        <Button variant="ghost" onClick={onClose}>
          Close
        </Button>
        <Link
          to="/inbox"
          onClick={onClose}
          className="inline-flex items-center px-4 py-2 rounded-md text-sm font-medium bg-primary-900 text-white hover:bg-primary-800"
        >
          Go to inbox
        </Link>
      </div>
    </div>
  )
}

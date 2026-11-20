import { useState, useCallback } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { useQueryClient } from '@tanstack/react-query'
import { importEpisode } from '../api/client'
import { ShowLinkError } from '../api/errors'
import { followPodcast } from '../api/followPodcast'
import type { ImportPayload } from '../api/types'
import Button, { CloseIcon } from './Button'
import { ProgressPill } from './InboxProgress'
import { deriveProgress, SAVED_VIEW_HREF } from '../utils/inbox'
import { InboxStatePanel } from './InboxStatus'
import { useEpisodeLinkState } from '../hooks/useEpisodeLinkState'

interface ImportEpisodeModalProps {
  isOpen: boolean
  onClose: () => void
}

type ImportState =
  | { kind: 'idle' }
  | { kind: 'submitting' }
  | { kind: 'success'; result: ImportPayload }
  | { kind: 'error'; message: string }
  // The link names a whole show: offer to follow it instead of refusing.
  | { kind: 'show_link'; url: string; platform: string | null }
  | { kind: 'following'; url: string; platform: string | null }
  | { kind: 'show_error'; url: string; platform: string | null; message: string }
  | { kind: 'followed' }

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
      } catch (err) {
        if (err instanceof ShowLinkError) {
          setState({ kind: 'show_link', url: err.url, platform: err.platform })
          return
        }
        setState({
          kind: 'error',
          message: err instanceof Error ? err.message : 'Import failed.',
        })
      }
    },
    [url, queryClient],
  )

  const handleFollow = useCallback(
    async (showUrl: string, platform: string | null) => {
      setState({ kind: 'following', url: showUrl, platform })
      try {
        await followPodcast(showUrl, 'import_dialog')
        queryClient.invalidateQueries({ queryKey: ['podcasts'] })
        queryClient.invalidateQueries({ queryKey: ['dashboard'] })
        queryClient.invalidateQueries({ queryKey: ['inbox'] })
        setState({ kind: 'followed' })
      } catch (err) {
        setState({
          kind: 'show_error',
          url: showUrl,
          platform,
          message: err instanceof Error ? err.message : 'Failed to follow the show.',
        })
      }
    },
    [queryClient],
  )

  const pasteAnother = useCallback(() => {
    setUrl('')
    setState({ kind: 'idle' })
  }, [])


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
        ) : state.kind === 'followed' ? (
          <FollowedShow onClose={onClose} />
        ) : state.kind === 'show_link' || state.kind === 'following' || state.kind === 'show_error' ? (
          <ShowLinkOffer
            platform={state.platform}
            following={state.kind === 'following'}
            error={state.kind === 'show_error' ? state.message : null}
            onFollow={() => void handleFollow(state.url, state.platform)}
            onPasteAnother={pasteAnother}
          />
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

const SHOW_LABELS: Record<string, string> = {
  spotify: 'a Spotify show',
  apple: 'an Apple Podcasts show',
  youtube: 'a YouTube channel',
}

interface ShowLinkOfferProps {
  platform: string | null
  following: boolean
  error: string | null
  onFollow: () => void
  onPasteAnother: () => void
}

// A pasted show link is most likely "I want this show": ask once, follow on
// yes, rather than refusing and sending the reader to the Podcasts page.
function ShowLinkOffer({ platform, following, error, onFollow, onPasteAnother }: ShowLinkOfferProps) {
  const label = (platform && SHOW_LABELS[platform]) || 'a show'
  return (
    <div>
      <div className="bg-gray-50 border border-gray-200 rounded-lg p-4">
        <h3 className="text-sm font-medium text-gray-900">This link is {label}, not an episode</h3>
        <p className="mt-1 text-sm text-gray-700">
          Follow it to get its new episodes in your inbox, or paste a single episode&apos;s link
          to import just that one.
        </p>
      </div>

      {error && <p className="mt-3 text-sm text-red-600">{error}</p>}

      <div className="mt-4 flex items-center justify-end gap-2">
        <Button variant="ghost" onClick={onPasteAnother} type="button" disabled={following}>
          Paste an episode link
        </Button>
        <Button onClick={onFollow} type="button" disabled={following} autoFocus>
          {following ? 'Following…' : 'Follow show'}
        </Button>
      </div>
    </div>
  )
}

function FollowedShow({ onClose }: { onClose: () => void }) {
  return (
    <div>
      <div className="bg-green-50 border border-green-200 rounded-lg p-4">
        <h3 className="text-sm font-medium text-green-900">You&apos;re following this show</h3>
        <p className="mt-1 text-sm text-green-800">New episodes will arrive in your inbox.</p>
      </div>
      <div className="mt-4 flex items-center justify-end gap-2">
        <Button variant="ghost" onClick={onClose}>
          Close
        </Button>
        <Link to="/podcasts" onClick={onClose} className={primaryLinkClass}>
          Go to Podcasts
        </Link>
      </div>
    </div>
  )
}

interface ImportSuccessProps {
  result: ImportPayload
  onClose: () => void
}

const primaryLinkClass =
  'inline-flex items-center px-4 py-2 rounded-md text-sm font-medium bg-primary-900 text-white hover:bg-primary-800'

// Spec #88: one view per real outcome, each answering "what now". The dialog
// lives on /inbox, so opening the episode uses the reader overlay above the
// still-mounted list (spec #52), exactly as an inbox row does.
function ImportSuccess({ result, onClose }: ImportSuccessProps) {
  const navigate = useNavigate()
  const episodeHref = result.podcast_slug
    ? `/podcasts/${result.podcast_slug}/episodes/${result.episode_slug || result.episode_id}`
    : null
  const { state: linkState } = useEpisodeLinkState(episodeHref ?? '')
  const progress = deriveProgress({ state: result.episode_state, is_failed: result.episode_failed })

  const goToInbox = (
    <Link to="/inbox" onClick={onClose} className={primaryLinkClass}>
      Go to inbox
    </Link>
  )
  const openLink = (label: string) =>
    episodeHref ? (
      <Link to={episodeHref} state={linkState} onClick={onClose} className={primaryLinkClass}>
        {label}
      </Link>
    ) : (
      goToInbox
    )

  let heading: string
  let body: React.ReactNode = null
  let primary: React.ReactNode = goToInbox

  if (result.outcome === 'new_episode') {
    heading = 'Importing — this may take a few minutes'
  } else if (result.outcome === 'added_existing') {
    heading = 'Added to your inbox'
    if (progress.kind === 'ready') {
      body = <p className="text-sm text-gray-700">It&apos;s already transcribed and summarised.</p>
      primary = openLink('Read now')
    } else if (progress.kind === 'failed') {
      body = (
        <p className="text-sm text-gray-700">
          Processing failed earlier. You can retry it from the episode page.
        </p>
      )
      primary = openLink('Open episode')
    } else {
      body = <ProgressPill status={progress} />
    }
  } else {
    heading = 'Good news — this is already in your inbox'
    body = (
      <InboxStatePanel
        entry={result.inbox_entry}
        onChanged={(state) => {
          onClose()
          // Show where a saved row went; a restored row is back in the list
          // behind the dialog, at its original date.
          if (state === 'saved') navigate(SAVED_VIEW_HREF)
        }}
      />
    )
    primary = openLink('Open episode')
  }

  return (
    <div>
      <div className="bg-green-50 border border-green-200 rounded-lg p-4">
        <h3 className="text-sm font-medium text-green-900">{heading}</h3>
        <p className="mt-1 text-sm text-green-800 truncate">{result.title}</p>
        {result.source_handle && (
          <p className="text-xs text-green-700 truncate">{result.source_handle}</p>
        )}
      </div>

      {body && <div className="mt-4">{body}</div>}

      {result.parent && result.outcome === 'new_episode' && (
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
        {primary}
      </div>
    </div>
  )
}

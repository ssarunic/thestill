import { useCallback, useEffect, useRef, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import type { FollowSource } from '../../api/types'
import { useFollowPodcast, useUnfollowPodcast } from '../../hooks/useApi'
import { useToast } from '../Toast'
import FollowButton from '../FollowButton'
import { consumePendingFollow } from '../../utils/signIn'

interface EpisodeFollowProps {
  podcastSlug: string
  podcastTitle: string
  /** The reader follows this show (from the episode response). */
  isFollowing: boolean
  /** False under the synthetic parent of bare audio imports. */
  followable: boolean
}

/** What this header did, for the show it did it to. Keyed by slug because
 * the reader stays mounted across episodes, and a confirmation must not
 * carry over to a show that was never followed. */
interface LocalFollow {
  slug: string
  following: boolean
}

const CONFIRM_DURATION_MS = 8000

/**
 * Spec #117 — Follow beside the show name in the episode header, for a
 * reader who doesn't follow the show. Nothing renders for a show already
 * followed, except "Following" and Undo right after following it here. The
 * click is explained afterwards, in a toast that says what Follow does.
 *
 * Undo sits here rather than in the toast: the inbox reader is a modal
 * dialog that keeps focus inside its panel, and the toast renders outside
 * it, so a toast action is out of keyboard and screen-reader reach there.
 */
export default function EpisodeFollow({ podcastSlug, podcastTitle, isFollowing, followable }: EpisodeFollowProps) {
  const queryClient = useQueryClient()
  const { showToast } = useToast()
  const { mutateAsync: follow, isPending: followPending, variables: followVars } = useFollowPodcast()
  const { mutateAsync: unfollow, isPending: unfollowPending } = useUnfollowPodcast()
  const [local, setLocal] = useState<LocalFollow | null>(null)
  const [failedSlug, setFailedSlug] = useState<string | null>(null)
  // After a click, focus follows the control that replaced the one pressed
  // (Follow → Undo → Follow), so a keyboard user isn't dropped on the page.
  const [focusNext, setFocusNext] = useState<'undo' | 'follow' | null>(null)
  const rowRef = useRef<HTMLSpanElement>(null)

  useEffect(() => {
    if (!focusNext) return
    const target = rowRef.current?.querySelector<HTMLButtonElement>(
      focusNext === 'undo' ? 'button[data-undo]' : 'button:not([data-undo])',
    )
    target?.focus()
  }, [focusNext, local])

  // Every open episode of this show re-reads its follow state. Only the
  // episode documents themselves (three-part keys), not their transcripts.
  const refreshShowEpisodes = useCallback(
    (slug: string) =>
      queryClient.invalidateQueries({
        predicate: (query) => query.queryKey.length === 3 && query.queryKey[0] === 'episodes' && query.queryKey[1] === slug,
      }),
    [queryClient],
  )

  // Promises, not ``mutate`` callbacks: those are dropped once this header
  // unmounts mid-request (the reader closed the overlay or moved on). The
  // query client and the toasts are app-level, so the outcome is still
  // reported and refreshed.
  const startFollow = useCallback(
    async (source: FollowSource, fromClick: boolean) => {
      const slug = podcastSlug
      const title = podcastTitle
      try {
        await follow({ podcastSlug: slug, source })
      } catch (error) {
        setFailedSlug(slug)
        showToast(`Couldn't follow ${title}: ${(error as Error).message}`, 'error')
        return
      }
      setLocal({ slug, following: true })
      if (fromClick) setFocusNext('undo')
      refreshShowEpisodes(slug)
      showToast(`Following ${title}. New episodes will arrive summarised in your inbox and briefing.`, 'success', {
        durationMs: CONFIRM_DURATION_MS,
      })
    },
    [podcastSlug, podcastTitle, follow, refreshShowEpisodes, showToast],
  )

  const undo = useCallback(async () => {
    const slug = podcastSlug
    const title = podcastTitle
    try {
      await unfollow(slug)
      setLocal({ slug, following: false })
      setFocusNext('follow')
      showToast(`Unfollowed ${title}`, 'success')
    } catch (error) {
      showToast(`Couldn't unfollow ${title}: ${(error as Error).message}`, 'error')
    } finally {
      refreshShowEpisodes(slug)
    }
  }, [podcastSlug, podcastTitle, unfollow, refreshShowEpisodes, showToast])

  // Spec #117 — finish a Follow pressed on the public episode page before
  // signing in. The intent was stored on /login by that click (never read
  // from the URL); it is read once per show this header shows.
  const pendingChecked = useRef<string | null>(null)
  useEffect(() => {
    if (pendingChecked.current === podcastSlug) return
    pendingChecked.current = podcastSlug
    if (!consumePendingFollow(podcastSlug) || isFollowing || !followable) return
    // State is set only after the awaited follow request, never during the
    // effect itself; the rule can't see past the await.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void startFollow('episode_pending_follow', false)
  }, [podcastSlug, isFollowing, followable, startFollow])

  if (!followable) return null
  const mine = local?.slug === podcastSlug ? local : null
  const following = mine ? mine.following : isFollowing
  // Already following, and not because of a click here: nothing to offer.
  if (following && !mine) return null

  const pending = followPending && followVars?.podcastSlug === podcastSlug
  const status = pending ? 'pending' : following ? 'done' : failedSlug === podcastSlug ? 'error' : 'idle'
  return (
    <span ref={rowRef} className="inline-flex shrink-0 items-center gap-1">
      <FollowButton
        size="sm"
        status={status}
        labels={{ idle: 'Follow', pending: 'Following…' }}
        onClick={() => {
          setFailedSlug(null)
          void startFollow('episode_page', true)
        }}
      />
      {following && (
        <button
          type="button"
          data-undo
          onClick={() => void undo()}
          disabled={unfollowPending}
          aria-label={`Undo following ${podcastTitle}`}
          className="min-h-[32px] rounded px-2 text-sm font-medium text-gray-600 underline underline-offset-2 hover:text-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-400 disabled:opacity-50"
        >
          Undo
        </button>
      )}
    </span>
  )
}

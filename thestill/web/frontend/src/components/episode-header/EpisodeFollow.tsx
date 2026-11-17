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
 * followed, except "Following" right after following it here. The click is
 * explained afterwards: the toast says what Follow does and offers Undo.
 */
export default function EpisodeFollow({ podcastSlug, podcastTitle, isFollowing, followable }: EpisodeFollowProps) {
  const queryClient = useQueryClient()
  const { showToast } = useToast()
  const { mutateAsync: follow, isPending, variables } = useFollowPodcast()
  const { mutateAsync: unfollow } = useUnfollowPodcast()
  const [local, setLocal] = useState<LocalFollow | null>(null)
  const [failedSlug, setFailedSlug] = useState<string | null>(null)

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
  // unmounts, and the toast's Undo outlives it by up to 8 s (the reader
  // closed the overlay or moved on). The query client and the toasts are
  // app-level, so the outcome is still reported and refreshed.
  const undo = useCallback(
    async (slug: string, title: string) => {
      try {
        await unfollow(slug)
        setLocal({ slug, following: false })
        showToast(`Unfollowed ${title}`, 'success')
      } catch (error) {
        showToast(`Couldn't unfollow ${title}: ${(error as Error).message}`, 'error')
      } finally {
        refreshShowEpisodes(slug)
      }
    },
    [unfollow, refreshShowEpisodes, showToast],
  )

  const startFollow = useCallback(
    async (source: FollowSource) => {
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
      refreshShowEpisodes(slug)
      showToast(`Following ${title}. New episodes will arrive summarised in your inbox and briefing.`, 'success', {
        durationMs: CONFIRM_DURATION_MS,
        action: { label: `Undo`, ariaLabel: `Undo following ${title}`, onClick: () => void undo(slug, title) },
      })
    },
    [podcastSlug, podcastTitle, follow, refreshShowEpisodes, showToast, undo],
  )

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
    void startFollow('episode_pending_follow')
  }, [podcastSlug, isFollowing, followable, startFollow])

  if (!followable) return null
  const mine = local?.slug === podcastSlug ? local : null
  const following = mine ? mine.following : isFollowing
  // Already following, and not because of a click here: nothing to offer.
  if (following && !mine) return null

  const pending = isPending && variables?.podcastSlug === podcastSlug
  const status = pending ? 'pending' : following ? 'done' : failedSlug === podcastSlug ? 'error' : 'idle'
  return (
    <FollowButton
      size="sm"
      status={status}
      labels={{ idle: 'Follow', pending: 'Following…' }}
      onClick={() => {
        setFailedSlug(null)
        void startFollow('episode_page')
      }}
    />
  )
}

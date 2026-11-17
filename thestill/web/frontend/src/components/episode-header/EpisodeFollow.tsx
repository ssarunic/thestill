import { useCallback, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import type { FollowSource } from '../../api/types'
import { useFollowPodcast, useUnfollowPodcast } from '../../hooks/useApi'
import { useToast } from '../Toast'
import FollowButton from '../FollowButton'

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
  const { mutate: follow, isPending, variables } = useFollowPodcast()
  const { mutate: unfollow } = useUnfollowPodcast()
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

  const undo = useCallback(
    (slug: string, title: string) =>
      unfollow(slug, {
        onSuccess: () => {
          setLocal({ slug, following: false })
          refreshShowEpisodes(slug)
          showToast(`Unfollowed ${title}`, 'success')
        },
        onError: (error) => showToast(`Couldn't unfollow ${title}: ${error.message}`, 'error'),
      }),
    [unfollow, refreshShowEpisodes, showToast],
  )

  const startFollow = useCallback(
    (source: FollowSource) => {
      const slug = podcastSlug
      const title = podcastTitle
      setFailedSlug(null)
      follow(
        { podcastSlug: slug, source },
        {
          onSuccess: () => {
            setLocal({ slug, following: true })
            refreshShowEpisodes(slug)
            showToast(`Following ${title}. New episodes will arrive summarised in your inbox and briefing.`, 'success', {
              durationMs: CONFIRM_DURATION_MS,
              action: { label: 'Undo', onClick: () => undo(slug, title) },
            })
          },
          onError: (error) => {
            setFailedSlug(slug)
            showToast(`Couldn't follow ${title}: ${error.message}`, 'error')
          },
        },
      )
    },
    [podcastSlug, podcastTitle, follow, refreshShowEpisodes, showToast, undo],
  )

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
      onClick={() => startFollow('episode_page')}
    />
  )
}

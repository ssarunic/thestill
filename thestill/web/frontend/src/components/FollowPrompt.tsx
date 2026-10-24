import { useState } from 'react'
import { Link } from 'react-router-dom'
import Button, { PlusIcon } from './Button'
import { useFollowCount, useFollowPodcast } from '../hooks/useApi'
import { usePersistedBoolean } from '../hooks/useAutoScrollFollow'

const DISMISSED_KEY = 'thestill.followPrompt.dismissed'

interface FollowPromptProps {
  podcastSlug: string
  podcastTitle: string
  /** Spacing from the placement; applied only when something renders. */
  className?: string
}

/**
 * The first-follow prompt on an episode page. A reader who follows no show
 * yet (typically just signed in from a public episode) is offered the show
 * they're reading: without a follow there is no inbox and no briefing to
 * come back to. "Not now" hides it for good on this browser; once the
 * reader follows anything it stops showing, except for its own
 * confirmation.
 */
export default function FollowPrompt({ podcastSlug, podcastTitle, className = '' }: FollowPromptProps) {
  const { data: followCount } = useFollowCount()
  const [dismissed, setDismissed] = usePersistedBoolean(DISMISSED_KEY, false)
  const { mutate: follow, isPending, isError } = useFollowPodcast()
  const [followed, setFollowed] = useState(false)

  if (followed) {
    return (
      <div
        role="status"
        className={`rounded-lg border border-green-200 bg-green-50 px-4 py-3 text-sm text-green-900 ${className}`}
      >
        <span className="font-medium">Following {podcastTitle}.</span> New episodes will arrive summarised in your{' '}
        <Link to="/inbox" className="font-medium underline">
          inbox
        </Link>
        .
      </div>
    )
  }
  if (dismissed || followCount !== 0) return null

  return (
    <section
      aria-label={`Follow ${podcastTitle}`}
      className={`flex flex-col gap-3 rounded-lg border border-primary-100 bg-primary-50 px-4 py-3 sm:flex-row sm:items-center sm:justify-between ${className}`}
    >
      <div className="text-sm text-primary-900">
        <p className="font-medium">Get every new episode of {podcastTitle} in your inbox.</p>
        <p className="mt-0.5 text-primary-800">
          Follow the show and each new episode arrives summarised, with a morning briefing across everything you follow.
        </p>
        {isError && <p className="mt-1 text-red-700">Couldn't follow the show. Try again.</p>}
      </div>
      <div className="flex shrink-0 gap-2">
        <Button
          size="sm"
          icon={<PlusIcon />}
          isLoading={isPending}
          onClick={() =>
            follow({ podcastSlug, source: 'episode_prompt' }, { onSuccess: () => setFollowed(true) })
          }
        >
          Follow
        </Button>
        <Button size="sm" variant="ghost" onClick={() => setDismissed(true)}>
          Not now
        </Button>
      </div>
    </section>
  )
}

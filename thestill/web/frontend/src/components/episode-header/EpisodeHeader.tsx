import type { RefObject } from 'react'
import { Link } from 'react-router-dom'
import type { EpisodeDetail } from '../../api/types'
import PageHero from '../PageHero'
import ActionRow from '../ActionRow'
import Artwork from '../Artwork'
import MetaEyebrow from '../MetaEyebrow'
import Button, { ChevronRightIcon, ExternalLinkIcon, PauseIcon, PlayIcon, YouTubeIcon } from '../Button'
import { buttonClassName } from '../buttonStyles'
import ShareButton from '../ShareButton'
import ExpandableDescription from '../ExpandableDescription'
import { episodeNumberLabel, formatEyebrowDate, formatMinutes } from '../../utils/episodeFormat'

export interface EpisodePlaybackState {
  /** The player's loaded track is this episode. */
  isCurrent: boolean
  isPlaying: boolean
  isLoading: boolean
  /** Toggle when current, otherwise start this episode. */
  onToggle: () => void
}

interface EpisodeHeaderProps {
  episode: EpisodeDetail
  podcastSlug: string
  titleRef?: RefObject<HTMLHeadingElement | null>
  playback: EpisodePlaybackState
  /** Spec #62 §6 — audio-kind episode with an episode-level YouTube link. */
  showWatchVideo: boolean
  onWatchVideo: () => void
  shareUrl: string
}

function typeLabel(episodeType: string | null | undefined): string | null {
  if (!episodeType || episodeType === 'full') return null
  return episodeType.charAt(0).toUpperCase() + episodeType.slice(1)
}

/**
 * Spec #76 §3.1–3.3 — the episode page's hero, action row and description:
 * artwork as the hero, one dominant action that carries the duration, the
 * show as a tappable row, and no pipeline state anywhere above the fold
 * (§5.5). Presentational: the reader owns the data and the player.
 */
export default function EpisodeHeader({
  episode,
  podcastSlug,
  titleRef,
  playback,
  showWatchVideo,
  onWatchVideo,
  shareUrl,
}: EpisodeHeaderProps) {
  const { isCurrent, isPlaying, isLoading, onToggle } = playback
  const minutes = formatMinutes(episode.duration)
  const primaryLabel = isPlaying ? 'Pause' : isCurrent ? 'Resume' : (minutes ?? 'Play')
  const primaryName = isPlaying ? 'Pause' : isCurrent ? 'Resume' : `Play episode${minutes ? `, ${minutes}` : ''}`
  const busy = isLoading && !isPlaying
  const description = episode.description_html || episode.description

  return (
    <PageHero
      artwork={
        <Artwork
          role="hero"
          sources={[episode.image_url, episode.podcast_image_url]}
          alt={`${episode.title} artwork`}
          loading="eager"
        />
      }
      backdropSources={[episode.image_url, episode.podcast_image_url]}
      eyebrow={
        <MetaEyebrow
          items={[
            formatEyebrowDate(episode.pub_date),
            episodeNumberLabel(episode.season_number, episode.episode_number),
            typeLabel(episode.episode_type),
            episode.explicit ? 'Explicit' : null,
          ]}
        />
      }
      title={episode.title}
      titleRef={titleRef}
      identity={
        // Plain navigation, deliberately leaving any inbox overlay context
        // (spec #52 interaction table).
        <Link
          to={`/podcasts/${podcastSlug}`}
          className="inline-flex max-w-full items-center gap-2 text-base text-gray-700 hover:text-primary-700"
        >
          <Artwork role="inline" sources={[episode.podcast_image_url]} />
          <span className="truncate">{episode.podcast_title}</span>
          <span className="h-4 w-4 shrink-0 text-gray-400">
            <ChevronRightIcon />
          </span>
        </Link>
      }
    >
      <ActionRow
        primary={
          <Button
            variant="primary"
            size="lg"
            pill
            onClick={onToggle}
            isLoading={busy}
            aria-label={primaryName}
            icon={isPlaying ? <PauseIcon /> : <PlayIcon />}
          >
            {primaryLabel}
          </Button>
        }
        actions={
          <>
            {showWatchVideo && (
              <Button variant="secondary" size="icon" icon={<YouTubeIcon />} onClick={onWatchVideo} title="Watch video">
                <span className="sr-only">Watch video</span>
              </Button>
            )}
            <ShareButton iconOnly title={`${episode.title} - ${episode.podcast_title}`} url={shareUrl} />
            {episode.website_url && (
              <a
                href={episode.website_url}
                target="_blank"
                rel="noopener noreferrer"
                title="Show notes"
                className={buttonClassName({ variant: 'secondary', size: 'icon' })}
              >
                <span className="h-5 w-5">
                  <ExternalLinkIcon />
                </span>
                <span className="sr-only">Show notes</span>
              </a>
            )}
          </>
        }
      />
      {description && <ExpandableDescription html={description} maxLines={3} />}
    </PageHero>
  )
}

/** Matches the loaded anatomy so first paint does not jump (spec #76 §6). */
export function EpisodeHeaderSkeleton() {
  return (
    <div className="animate-pulse" aria-hidden="true">
      <div className="flex flex-col gap-4 sm:flex-row sm:gap-6">
        <div className="flex justify-center sm:block">
          <div className="aspect-square w-[40vw] max-w-[160px] rounded-xl bg-gray-200 sm:w-[200px] sm:max-w-none" />
        </div>
        <div className="flex-1 space-y-3">
          <div className="h-4 w-1/3 rounded bg-gray-200" />
          <div className="h-7 w-11/12 rounded bg-gray-200" />
          <div className="h-7 w-2/3 rounded bg-gray-200" />
          <div className="h-7 w-1/2 rounded bg-gray-200" />
          <div className="flex items-center gap-3 pt-2">
            <div className="h-12 w-28 rounded-full bg-gray-200" />
            <div className="h-11 w-11 rounded-full bg-gray-200" />
            <div className="h-11 w-11 rounded-full bg-gray-200" />
          </div>
          <div className="space-y-2 pt-2">
            <div className="h-4 w-full rounded bg-gray-200" />
            <div className="h-4 w-full rounded bg-gray-200" />
            <div className="h-4 w-3/4 rounded bg-gray-200" />
          </div>
        </div>
      </div>
    </div>
  )
}

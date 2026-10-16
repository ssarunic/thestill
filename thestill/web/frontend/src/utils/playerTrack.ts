import type { PlaybackManifest } from '../api/types'
import type { PlayerTrack } from '../contexts/PlayerContext'

// The episode fields a player track is built from. EpisodeDetail and the
// spec #90 TrackEpisode payload (`latest`, Continue listening) both satisfy it.
export interface TrackSource {
  id: string
  slug: string
  title: string
  podcast_slug: string
  podcast_title: string
  audio_url: string
  image_url: string | null
  podcast_image_url: string | null
  duration: number | null
  playback?: PlaybackManifest | null
}

export function buildPlayerTrack(src: TrackSource): PlayerTrack {
  return {
    episodeId: src.id,
    podcastSlug: src.podcast_slug,
    episodeSlug: src.slug,
    title: src.title,
    podcastTitle: src.podcast_title,
    audioUrl: src.audio_url,
    artworkUrl: src.image_url ?? src.podcast_image_url,
    durationHint: src.duration,
    playback: src.playback ?? null,
  }
}

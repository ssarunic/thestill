import { Fragment, useMemo } from 'react'
import { usePlayer, usePlayerTime, type PlayerTrack } from '../contexts/PlayerContext'
import { useEpisodeTranscript, useEpisodeTranscriptWords } from '../hooks/useApi'
import { useKaraokeActiveWordIdx } from '../hooks/useKaraokeActiveWordIdx'
import { findActiveSegmentIndex } from '../utils/transcriptSearch'
import { HIGHLIGHT_LEAD_SECONDS } from '../utils/highlightLead'
import KaraokeWord from './KaraokeWord'

interface NowPlayingKaraokeLineProps {
  track: PlayerTrack
  /** Fetch only while the sheet is open. */
  enabled: boolean
}

/**
 * Spec #72 §3 — the "current line": the transcript segment under the
 * playhead, with the spec #38 word wipe. Reuses the transcript viewer's
 * primitives so the two never disagree: the segment is resolved with the
 * same 150 ms perceptual lead `ActiveSegmentTracker` applies, and the word
 * cursor comes from `useKaraokeActiveWordIdx` (which applies the lead to the
 * read-word cutoff itself). Plain segment text when word data is absent
 * (the 404 sentinel); nothing when there is no transcript or no segment is
 * under the playhead.
 */
export default function NowPlayingKaraokeLine({ track, enabled }: NowPlayingKaraokeLineProps) {
  const player = usePlayer()
  const currentTime = usePlayerTime()
  const { data: transcriptData } = useEpisodeTranscript(
    enabled ? track.podcastSlug : '',
    enabled ? track.episodeSlug : '',
  )
  const { data: words } = useEpisodeTranscriptWords(track.podcastSlug, track.episodeSlug, enabled)

  const segments = transcriptData?.segments?.segments ?? null
  const offset = transcriptData?.segments?.playback_time_offset_seconds ?? 0
  const segment = useMemo(() => {
    if (!segments || segments.length === 0) return null
    const idx = findActiveSegmentIndex(segments, currentTime + HIGHLIGHT_LEAD_SECONDS, offset)
    return idx >= 0 ? segments[idx] : null
  }, [segments, currentTime, offset])

  const segmentWords = segment ? words?.wordsBySegmentId.get(segment.id) ?? null : null
  const cursor = useKaraokeActiveWordIdx(segmentWords, words?.offset ?? offset, player.getCurrentTime)

  if (!segment) return null

  return (
    <p
      className="line-clamp-2 text-sm leading-relaxed text-ink"
      data-testid="now-playing-karaoke-line"
      data-segment-id={segment.id}
    >
      {segmentWords && segmentWords.length > 0
        ? segmentWords.map((word, i) => (
            <Fragment key={`${segment.id}-${i}`}>
              {i > 0 ? ' ' : null}
              <KaraokeWord word={word} read={i <= cursor.readUpTo} isActive={i === cursor.activeIdx} />
            </Fragment>
          ))
        : segment.text}
    </p>
  )
}

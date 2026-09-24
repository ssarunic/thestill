import type { EpisodeEntity, MentionLite, SummaryCitation } from '../../api/types'

// Spec #82 §Stage 3 — the mention behind a summary entity peek. The
// segment comes from the block's nearest citation, so the peek's "Show
// in transcript" lands on the passage the sentence came from; without a
// usable citation it falls back to the entity's first transcript
// mention. `role: 'summary'` is a frontend-only marker (never persisted)
// that `isSpeakingMention` reads as a name in text.
export function synthesizeSummaryMention(
  episodeEntity: EpisodeEntity,
  term: string,
  citation: SummaryCitation | undefined,
): MentionLite | null {
  let segmentId: number | undefined
  let startMs: number | undefined
  if (citation?.segment_id_hint != null) {
    segmentId = citation.segment_id_hint
    startMs = Math.round((citation.target_playback_s ?? citation.cited_playback_s) * 1000)
  } else {
    const first = episodeEntity.mentions.reduce<MentionLite | null>(
      (best, m) => (best === null || m.start_ms < best.start_ms ? m : best),
      null,
    )
    if (!first) return null
    segmentId = first.segment_id
    startMs = first.start_ms
  }
  return {
    id: 0,
    entity_id: episodeEntity.entity.id,
    segment_id: segmentId,
    start_ms: startMs,
    end_ms: startMs,
    speaker: null,
    role: 'summary',
    surface_form: term,
    quote_excerpt: term,
    confidence: 1,
    sentiment: null,
  }
}

import type { EpisodeEntity, MentionLite } from '../../api/types'

// The mention behind an *index* entity peek — a row in the right rail or
// a pill in the key entities strip. An index entry stands for the whole
// entity rather than one place in the text, so the peek borrows the
// earliest transcript mention: "Show in transcript" lands where the
// entity first comes up and ▶ plays from there, matching the rail's own
// play button. `role: 'index'` is a frontend-only marker (never
// persisted) that `isSpeakingMention` reads as a name in text.
//
// When the payload carries no mentions (the list endpoints trim them) the
// segment is unknown: `segment_id` is -1 and the caller must not offer
// "Show in transcript". `first_mention_ms` still gives ▶ a target.
export const NO_SEGMENT = -1

export function synthesizeIndexMention(episodeEntity: EpisodeEntity): MentionLite {
  const first = episodeEntity.mentions.reduce<MentionLite | null>(
    (best, m) => (best === null || m.start_ms < best.start_ms ? m : best),
    null,
  )
  const startMs = first?.start_ms ?? episodeEntity.first_mention_ms
  return {
    id: 0,
    entity_id: episodeEntity.entity.id,
    segment_id: first?.segment_id ?? NO_SEGMENT,
    start_ms: startMs,
    end_ms: startMs,
    speaker: null,
    role: 'index',
    surface_form: episodeEntity.entity.canonical_name,
    quote_excerpt: episodeEntity.entity.canonical_name,
    confidence: 1,
    sentiment: null,
  }
}

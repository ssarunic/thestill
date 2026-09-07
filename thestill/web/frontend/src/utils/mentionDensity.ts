import type { EpisodeEntity } from '../api/types'

// Spec #28 §5.2 — "top-N entity" is one selector: the key-entities strip and
// the Now Playing scrubber's tick row (spec #72 §2, the density timeline's
// new home) must agree on which entities count.
export const TOP_N = 5

export function selectTopEntities(entities: readonly EpisodeEntity[], n = TOP_N): EpisodeEntity[] {
  return entities
    .slice()
    .sort((a, b) => b.mention_count - a.mention_count)
    .slice(0, n)
}

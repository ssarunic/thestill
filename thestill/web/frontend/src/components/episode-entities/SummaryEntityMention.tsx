import type { ReactNode } from 'react'
import type { EpisodeEntity, SummaryCitation } from '../../api/types'
import EntityHighlight from './EntityHighlight'
import { synthesizeSummaryMention } from './summaryMention'

// Spec #82 §Stage 3 — the renderer behind a `span[data-entity-id]` the
// rehype plugin emitted. It turns the match into the transcript's
// `EntityHighlight` (summary variant) with a synthesised mention.
//
// The mention's segment comes from the block's nearest citation, so the
// peek's "Show in transcript" lands on the passage the sentence came
// from; a block without a usable citation falls back to the entity's
// first transcript mention. `role: 'summary'` is a frontend-only marker
// (never persisted) — `isSpeakingMention` reads it as a name in text.

export interface SummaryEntityMentionProps {
  entityId: string
  term: string
  citeId?: string
  children?: ReactNode
  entityById: Map<string, EpisodeEntity>
  citationById: Map<string, SummaryCitation>
  episodeId?: string | null
  isSmUp: boolean
  onSeek?: (seconds: number) => void
  onShowInTranscript?: (segmentId: number) => void
}

export default function SummaryEntityMention({
  entityId,
  term,
  citeId,
  children,
  entityById,
  citationById,
  episodeId = null,
  isSmUp,
  onSeek,
  onShowInTranscript,
}: SummaryEntityMentionProps) {
  const episodeEntity = entityById.get(entityId)
  const mention = episodeEntity
    ? synthesizeSummaryMention(episodeEntity, term, citeId ? citationById.get(citeId) : undefined)
    : null
  if (!episodeEntity || !mention) return <>{children}</>
  return (
    <EntityHighlight
      variant="summary"
      episodeEntity={episodeEntity}
      mention={mention}
      episodeId={episodeId}
      isSmUp={isSmUp}
      onSeek={onSeek}
      onShowInTranscript={onShowInTranscript}
    >
      {children}
    </EntityHighlight>
  )
}

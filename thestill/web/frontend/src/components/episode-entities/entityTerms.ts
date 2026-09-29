import type { EpisodeEntity } from '../../api/types'
import { INLINE_HIGHLIGHT_CONFIDENCE_FLOOR } from '../../utils/entityColors'
import { isSpeakingMention } from './mentionPermalink'

// Spec #82 §Stage 1 — the term index behind summary entity links.
//
// The summary is matched against the names this episode's transcript
// actually established: each entity's canonical name plus the surface
// forms of its in-text mentions. Only entities resolved for *this*
// episode are candidates, so precision is that of the transcript's own
// extraction; a summary that invents a name links nothing.
//
// Rules (all applied here, once per entity list):
// - at least three characters;
// - multi-word terms match case-insensitively ("zach lloyd" links);
// - single-token terms match case-sensitively and must carry an uppercase
//   letter ("Warp" links, "warp speed" does not; acronyms are exact-case);
// - a term claimed by two entities is dropped — the summary has no context
//   to pick, and a wrong link is worse than none;
// - `speaking` mentions contribute nothing: their surface form is the
//   speaker label, already covered by the canonical name.

export interface EntityTerm {
  term: string
  entityId: string
  caseSensitive: boolean
}

export interface EntityTermMatch {
  start: number
  end: number
  entityId: string
  // The text as it appears in the source, not the term's own spelling.
  text: string
}

export interface EntityTermIndex {
  terms: EntityTerm[]
  // One alternation per case rule: JS regex flags apply to the whole
  // pattern, so the two rules cannot share one. Null when the bucket is
  // empty.
  caseSensitive: RegExp | null
  caseInsensitive: RegExp | null
  byKey: Map<string, EntityTerm>
}

const MIN_TERM_LENGTH = 3

function normalizeTerm(raw: string): string {
  return raw.trim().replace(/\s+/g, ' ')
}

// Bucketed lookup key: exact for single tokens, case-folded for phrases.
function termKey(term: string, caseSensitive: boolean): string {
  return caseSensitive ? `cs:${term}` : `ci:${term.toLowerCase()}`
}

export function buildEntityTerms(entities: EpisodeEntity[]): EntityTerm[] {
  const claimed = new Map<string, EntityTerm>()
  const conflicted = new Set<string>()
  for (const episodeEntity of entities) {
    const entityId = episodeEntity.entity.id
    const candidates = new Set<string>([episodeEntity.entity.canonical_name])
    for (const mention of episodeEntity.mentions) {
      if (isSpeakingMention(mention)) continue
      if (mention.confidence < INLINE_HIGHLIGHT_CONFIDENCE_FLOOR) continue
      candidates.add(mention.surface_form)
    }
    for (const raw of candidates) {
      const term = normalizeTerm(raw)
      if (term.length < MIN_TERM_LENGTH) continue
      const caseSensitive = !term.includes(' ')
      if (caseSensitive && !/\p{Lu}/u.test(term)) continue
      const key = termKey(term, caseSensitive)
      const existing = claimed.get(key)
      if (!existing) {
        claimed.set(key, { term, entityId, caseSensitive })
      } else if (existing.entityId !== entityId) {
        conflicted.add(key)
      }
    }
  }
  for (const key of conflicted) claimed.delete(key)
  // Longest first, so a phrase wins over a name it contains at the same
  // position — both in the alternation order and in placement below.
  return [...claimed.values()].sort(
    (a, b) => b.term.length - a.term.length || a.term.localeCompare(b.term),
  )
}

function escapeRegExp(s: string): string {
  return s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
}

// Unicode-aware word boundaries on both sides, so "Warp" never matches
// "Warped" and "Vo" never matches "Volvo". A phrase's spaces match any
// whitespace run (the summary may wrap a name across a line).
function compileAlternation(terms: EntityTerm[], flags: string): RegExp | null {
  if (terms.length === 0) return null
  const alternation = terms.map((t) => escapeRegExp(t.term).replace(/ /g, '\\s+')).join('|')
  return new RegExp(`(?<![\\p{L}\\p{N}])(?:${alternation})(?![\\p{L}\\p{N}])`, flags)
}

export function buildEntityTermIndex(entities: EpisodeEntity[]): EntityTermIndex {
  const terms = buildEntityTerms(entities)
  const byKey = new Map<string, EntityTerm>()
  for (const t of terms) byKey.set(termKey(t.term, t.caseSensitive), t)
  return {
    terms,
    caseSensitive: compileAlternation(
      terms.filter((t) => t.caseSensitive),
      'gu',
    ),
    caseInsensitive: compileAlternation(
      terms.filter((t) => !t.caseSensitive),
      'giu',
    ),
    byKey,
  }
}

// Every occurrence of every term in `text`, non-overlapping, longest
// first on conflict (the same greedy rule as `buildSpans` in
// applyHighlights.tsx), returned in source order.
export function matchEntityTerms(text: string, index: EntityTermIndex): EntityTermMatch[] {
  const candidates: EntityTermMatch[] = []
  const buckets: [RegExp | null, boolean][] = [
    [index.caseSensitive, true],
    [index.caseInsensitive, false],
  ]
  for (const [regex, caseSensitive] of buckets) {
    if (!regex) continue
    for (const m of text.matchAll(regex)) {
      const matched = m[0]
      const term = index.byKey.get(termKey(normalizeTerm(matched), caseSensitive))
      if (!term) continue
      candidates.push({
        start: m.index,
        end: m.index + matched.length,
        entityId: term.entityId,
        text: matched,
      })
    }
  }
  candidates.sort((a, b) => b.end - b.start - (a.end - a.start) || a.start - b.start)
  const placed: EntityTermMatch[] = []
  for (const c of candidates) {
    if (placed.some((p) => c.start < p.end && p.start < c.end)) continue
    placed.push(c)
  }
  return placed.sort((a, b) => a.start - b.start)
}

import type { AnnotatedSegment, EpisodeEntity } from '../../api/types'
import { entityHref } from '../../utils/entityColors'
import { buildSpeakerColorMap, resolveSpeakerColor } from '../../utils/speakerColors'

export interface PersonChip {
  key: string
  name: string
  color: string
  imageUrl: string | null
  /** Entity chips navigate; speaker chips (``null``) jump the transcript. */
  href: string | null
  /** First segment spoken by this label — the jump target for speaker chips. */
  segmentId: number | null
}

const MAX_ENTITY_PEOPLE = 8
const PLACEHOLDER_SPEAKER = /^(speaker[_ ]?\d+|unknown( speaker)?|none|null)$/i
// Role words the facts extractor uses for voices outside the conversation
// (facts_extractor's speaker-mapping prompt, role_linker's generic roles),
// numbered when several share one ("Archival Audio 2") and sometimes
// annotated ("Ad Narrator (Sponsor)"). A label that is only a role names
// nobody, so it is no person to list. "Host 1" / "Guest 2" stay: they
// are unnamed people taking part.
const ROLE_ONLY_SPEAKER =
  /^(?:(?:ad|sponsor|promo)(?: (?:narrator|reader|read|voice))?|narrator|announcer|voice ?over|archival(?: (?:audio|clip|footage))?|(?:audio |news |film )?clip|soundbite|listener|caller|theme music)$/i

/** The label minus a trailing ``(Role)`` and a trailing number. */
function roleStem(label: string): string {
  return label
    .replace(/\s*\([^)]*\)\s*$/, '')
    .replace(/\s+\d+$/, '')
    .trim()
}

/** Spec #76 §3.5 de-duplication contract: trim, collapse whitespace, case-fold. Nothing else. */
export function normalizePersonName(name: string): string {
  return name.trim().replace(/\s+/g, ' ').toLowerCase()
}

/**
 * Spec #76 §3.5 — merge the people this episode is about with the people
 * heard in it, in that order:
 *
 * 1. person entities tagged host or guest, by salience, capped at eight;
 * 2. distinct transcript speaker labels not already covered (exact
 *    normalised-name match only — alias merging belongs to the entity
 *    index, spec #28), not diarisation placeholders and not role-only
 *    labels such as "Ad Narrator 1" or "Archival Audio 2".
 */
export function buildPeople(entities: EpisodeEntity[], segments: AnnotatedSegment[] | null | undefined): PersonChip[] {
  const people = entities
    .filter((e) => e.entity.type === 'person' && (e.speaker_kind === 'host' || e.speaker_kind === 'guest'))
    .sort((a, b) => b.salience - a.salience)
    .slice(0, MAX_ENTITY_PEOPLE)

  // Only segments the transcript renders as speaker rows count; ad breaks,
  // music and intros can carry a preserved speaker and would otherwise
  // both surface a chip and shift the palette away from the viewer's
  // colours (see buildSpeakerColorMap's contract).
  const renderedSpeakers: (string | null)[] = []
  const speakerLabels: { label: string; segmentId: number }[] = []
  const seenSpeakers = new Set<string>()
  for (const segment of segments ?? []) {
    if (segment.kind !== 'content' && segment.kind !== 'filler') continue
    renderedSpeakers.push(segment.speaker)
    const label = segment.speaker?.trim()
    if (!label || PLACEHOLDER_SPEAKER.test(label) || ROLE_ONLY_SPEAKER.test(roleStem(label))) continue
    const key = normalizePersonName(label)
    if (seenSpeakers.has(key)) continue
    seenSpeakers.add(key)
    speakerLabels.push({ label, segmentId: segment.id })
  }

  // One colour map, fed every speaker row exactly as the transcript feeds
  // its own (placeholders and role labels included, though they get no
  // chip), then the entity people, so a person heard in the episode keeps
  // the colour the transcript gives their label.
  const colors = buildSpeakerColorMap([...renderedSpeakers, ...people.map((p) => p.entity.canonical_name)])
  const covered = new Set<string>()
  const chips: PersonChip[] = []

  for (const person of people) {
    const name = person.entity.canonical_name
    covered.add(normalizePersonName(name))
    chips.push({
      key: `entity:${person.entity.id}`,
      name,
      color: resolveSpeakerColor(name, colors),
      imageUrl: person.entity.image_url ?? null,
      href: entityHref(person.entity.type, person.entity.id),
      segmentId: null,
    })
  }
  for (const { label, segmentId } of speakerLabels) {
    if (covered.has(normalizePersonName(label))) continue
    chips.push({
      key: `speaker:${label}`,
      name: label,
      color: resolveSpeakerColor(label, colors),
      imageUrl: null,
      href: null,
      segmentId,
    })
  }
  return chips
}

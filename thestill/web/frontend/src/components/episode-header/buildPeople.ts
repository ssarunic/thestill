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
}

const MAX_ENTITY_PEOPLE = 8
const PLACEHOLDER_SPEAKER = /^(speaker[_ ]?\d+|unknown)$/i

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
 *    index, spec #28) and not diarisation placeholders.
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
  const speakerLabels: string[] = []
  const seenSpeakers = new Set<string>()
  for (const segment of segments ?? []) {
    if (segment.kind !== 'content' && segment.kind !== 'filler') continue
    const label = segment.speaker?.trim()
    if (!label || PLACEHOLDER_SPEAKER.test(label)) continue
    const key = normalizePersonName(label)
    if (seenSpeakers.has(key)) continue
    seenSpeakers.add(key)
    speakerLabels.push(label)
  }

  // One colour map, transcript speakers first so a person heard in the
  // episode keeps the colour the transcript gives their label.
  const colors = buildSpeakerColorMap([...speakerLabels, ...people.map((p) => p.entity.canonical_name)])
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
    })
  }
  for (const label of speakerLabels) {
    if (covered.has(normalizePersonName(label))) continue
    chips.push({
      key: `speaker:${label}`,
      name: label,
      color: resolveSpeakerColor(label, colors),
      imageUrl: null,
      href: null,
    })
  }
  return chips
}

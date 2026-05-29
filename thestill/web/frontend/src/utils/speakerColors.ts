// Shared speaker-colour logic used by both transcript viewers. Keeping it
// in one place means a given speaker stays the same colour across the
// "Legacy blended" and "Segmented" tabs — the visual anchor matters more
// than the specific palette, so the two views must agree.
//
// Palette — an Okabe–Ito-derived qualitative palette. Okabe–Ito is the
// de-facto colour-blind-safe categorical palette (distinguishable under
// deuteranopia/protanopia). The values below are darkened from the
// canonical set so they stay legible as bold text on a white background —
// the canonical light "sky"/"yellow" fail WCAG contrast as text. Hues are
// spread around the wheel so adjacent speakers never read as "the same
// colour"; the previous palette clustered blue, purple and indigo in the
// cool arc, which is what made speakers hard to tell apart.
//
// Assignment — colours are handed out by *order of first appearance*, which
// is the standard categorical approach: N speakers always use the N
// most-separated colours with zero collisions. (Hashing a name into the
// palette, the old behaviour, collides for ~4+ speakers — a birthday-paradox
// problem that produces exactly the "two speakers, one colour" ambiguity we
// want to avoid.)
//
// Identity, not diarisation label, drives the colour. The keys are the
// *resolved* speaker — the real name once the LLM speaker-mapping has run —
// so when two diarisation labels are merged to the same person (e.g.
// SPEAKER_00 and SPEAKER_03 both map to "Lenny Rachitsky") they collapse to
// one map entry and share a colour automatically.
//
// Colours are hex / hsl strings applied via inline `style`, because the
// overflow path generates colours at runtime (golden-angle hue rotation)
// which Tailwind cannot pre-compile into utility classes.

// Neutral grey for segments without a real speaker (ad breaks, unmapped
// "Unknown"). slate-600 — readable, clearly "not a person".
export const NEUTRAL_SPEAKER_COLOR = '#4b5563'

const PALETTE = [
  '#0064A6', // blue
  '#C24400', // vermillion
  '#00795A', // bluish green
  '#9C4F86', // reddish purple
  '#1A7E9C', // teal / sky
  '#9A6700', // amber
  '#7D4E24', // brown
  '#5B57B8', // indigo
]

function colorForIndex(index: number): string {
  if (index < PALETTE.length) return PALETTE[index]
  // Overflow beyond the curated palette: spread the remaining speakers
  // evenly around the hue wheel via the golden angle, at a fixed dark
  // lightness so the label stays legible as bold text on white.
  const hue = (index * 137.508) % 360
  return `hsl(${hue.toFixed(1)} 60% 33%)`
}

const UNKNOWN_LABELS = new Set(['', 'unknown'])

function isRealSpeaker(speaker: string | null | undefined): speaker is string {
  return !!speaker && !UNKNOWN_LABELS.has(speaker.trim().toLowerCase())
}

// Assign a colour to each distinct speaker by order of first appearance.
// Pass the transcript's speakers in document order; null / "Unknown" /
// ad-break segments are skipped so they don't consume a palette slot.
export function buildSpeakerColorMap(
  speakersInOrder: ReadonlyArray<string | null | undefined>,
): Map<string, string> {
  const map = new Map<string, string>()
  for (const speaker of speakersInOrder) {
    if (!isRealSpeaker(speaker)) continue
    const key = speaker.trim()
    if (!map.has(key)) map.set(key, colorForIndex(map.size))
  }
  return map
}

// Look up the colour for a speaker, falling back to a neutral grey for
// unknown / ad-break / unmapped-empty segments.
export function resolveSpeakerColor(
  speaker: string | null | undefined,
  colorMap: Map<string, string>,
): string {
  if (!isRealSpeaker(speaker)) return NEUTRAL_SPEAKER_COLOR
  return colorMap.get(speaker.trim()) ?? NEUTRAL_SPEAKER_COLOR
}

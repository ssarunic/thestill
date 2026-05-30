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

// WCAG AA contrast for normal-size text. The overflow colours are darkened
// until they clear this against white so speaker labels stay legible.
const AA_CONTRAST = 4.5

function colorForIndex(index: number): string {
  if (index < PALETTE.length) return PALETTE[index]
  // Overflow beyond the curated palette: spread the remaining speakers evenly
  // around the hue wheel via the golden angle. A *fixed* lightness doesn't
  // work — at L=33% the yellow-green hues (~60–90°) only reach ~3.8–4.1:1 on
  // white and fail AA — so darken per-hue until the contrast target is met.
  // Blues already pass and stay vivid; only the light hues get stepped down.
  const hue = (index * 137.508) % 360
  const saturation = 0.6
  let lightness = 0.33
  while (lightness > 0.12 && contrastOnWhite(hslLuminance(hue, saturation, lightness)) < AA_CONTRAST) {
    lightness -= 0.02
  }
  return `hsl(${hue.toFixed(1)} ${Math.round(saturation * 100)}% ${Math.round(lightness * 100)}%)`
}

// --- WCAG contrast helpers (sRGB relative luminance, per WCAG 2.x) ---

function hslLuminance(h: number, s: number, l: number): number {
  const [r, g, b] = hslToRgb(h, s, l)
  return relativeLuminance(r, g, b)
}

// Contrast ratio of a colour (given its relative luminance) against white.
function contrastOnWhite(luminance: number): number {
  return (1 + 0.05) / (luminance + 0.05)
}

function relativeLuminance(r: number, g: number, b: number): number {
  const lin = (c: number) => (c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4)
  return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b)
}

// h in [0,360), s/l in [0,1] → r/g/b in [0,1].
function hslToRgb(h: number, s: number, l: number): [number, number, number] {
  const c = (1 - Math.abs(2 * l - 1)) * s
  const hp = (h % 360) / 60
  const x = c * (1 - Math.abs((hp % 2) - 1))
  let r = 0
  let g = 0
  let b = 0
  if (hp < 1) [r, g, b] = [c, x, 0]
  else if (hp < 2) [r, g, b] = [x, c, 0]
  else if (hp < 3) [r, g, b] = [0, c, x]
  else if (hp < 4) [r, g, b] = [0, x, c]
  else if (hp < 5) [r, g, b] = [x, 0, c]
  else [r, g, b] = [c, 0, x]
  const m = l - c / 2
  return [r + m, g + m, b + m]
}

const UNKNOWN_LABELS = new Set(['', 'unknown'])

function isRealSpeaker(speaker: string | null | undefined): speaker is string {
  return !!speaker && !UNKNOWN_LABELS.has(speaker.trim().toLowerCase())
}

// Assign a colour to each distinct speaker by order of first appearance.
// Pass the speakers in document order. null / "" / "Unknown" are skipped here
// so they don't consume a palette slot — but this helper only sees strings, so
// the *caller* must exclude segments that aren't rendered as speaker rows
// (ad breaks, music, intros can carry a real speaker preserved from cleanup).
// Including them would shift later speakers' colours and desync the two viewers.
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

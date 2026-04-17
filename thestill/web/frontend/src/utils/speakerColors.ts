// Shared speaker-colour mapping used by both transcript viewers. Keeping
// this in one place means a given speaker stays the same colour across
// the "Legacy blended" and "Segmented" tabs — the visual anchor is more
// important than the specific palette, so the two views must agree.

export const speakerColors: Record<string, string> = {
  SPEAKER_00: 'text-blue-700',
  SPEAKER_01: 'text-purple-700',
  SPEAKER_02: 'text-green-700',
  SPEAKER_03: 'text-orange-700',
  SPEAKER_04: 'text-pink-700',
}

const FALLBACK_COLORS = [
  'text-blue-700',
  'text-purple-700',
  'text-green-700',
  'text-orange-700',
  'text-pink-700',
  'text-indigo-700',
  'text-red-700',
]

export function getSpeakerColor(speaker: string): string {
  if (speakerColors[speaker]) return speakerColors[speaker]
  // Consistent hash-based fallback so named speakers (e.g. "Lenny
  // Rachitsky") keep the same colour across renders.
  const hash = speaker.split('').reduce((acc, char) => acc + char.charCodeAt(0), 0)
  return FALLBACK_COLORS[hash % FALLBACK_COLORS.length]
}

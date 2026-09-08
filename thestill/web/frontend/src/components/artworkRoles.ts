// Spec #76 §5.8 — one wrapper fixes size and radius per role, replacing the
// seven size/radius combinations spec #73 §2 counted.
export type ArtworkRole = 'inline' | 'bar' | 'rowSm' | 'row' | 'sheet' | 'card' | 'hero'

export const ARTWORK_ROLE: Record<ArtworkRole, { box: string; radius: string; px: number; glyph: string }> = {
  /** 28 px — the show row under a title, the collapsed bar. */
  inline: { box: 'w-7 h-7', radius: 'rounded-md', px: 28, glyph: 'w-3.5 h-3.5' },
  /** 32 px — the collapsed header bar. */
  bar: { box: 'w-8 h-8', radius: 'rounded-md', px: 32, glyph: 'w-4 h-4' },
  /** 40 px — episode rows inside a podcast. */
  rowSm: { box: 'w-10 h-10', radius: 'rounded-lg', px: 40, glyph: 'w-5 h-5' },
  /** 48 px — podcast and inbox rows (``ListRowArtwork``). */
  row: { box: 'w-12 h-12', radius: 'rounded-lg', px: 48, glyph: 'w-5 h-5' },
  /** 64 px — the desktop Now Playing card header (spec #72). */
  sheet: { box: 'w-16 h-16', radius: 'rounded-lg', px: 64, glyph: 'w-7 h-7' },
  /** 96 px — podcast detail; the phone Now Playing sheet header. */
  card: { box: 'w-24 h-24', radius: 'rounded-lg', px: 96, glyph: 'w-10 h-10' },
  /**
   * Episode hero: 40 vw capped at 160 px on phones (the largest size that
   * keeps the tabs above the fold at 393 × 852, spec #76 §7.1), 200 px
   * from ``sm``.
   */
  hero: { box: 'w-[40vw] max-w-[160px] sm:w-[200px] sm:max-w-none', radius: 'rounded-xl', px: 200, glyph: 'w-12 h-12' },
}

/** Box + radius classes for a role, so skeletons stay in step with the real artwork. */
export function artworkFrameClass(role: ArtworkRole): string {
  return `${ARTWORK_ROLE[role].box} ${ARTWORK_ROLE[role].radius} shrink-0 aspect-square`
}

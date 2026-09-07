// Spec #76 — listener-facing formatting for the episode hero and the
// Information list. Pure functions so the row builder is unit-testable.

/** ``Sun 6 Sep`` — year appended only when it is not the current year. */
export function formatEyebrowDate(iso: string | null | undefined, now: Date = new Date()): string | null {
  if (!iso) return null
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return null
  const sameYear = date.getFullYear() === now.getFullYear()
  return date.toLocaleDateString('en-GB', {
    weekday: 'short',
    day: 'numeric',
    month: 'short',
    ...(sameYear ? {} : { year: 'numeric' }),
  })
}

/** ``6 Sep 2026, 07:00`` in the reader's local time. */
export function formatPublished(iso: string | null | undefined): string | null {
  if (!iso) return null
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return null
  return date.toLocaleString('en-GB', {
    day: 'numeric',
    month: 'short',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  })
}

/** ``58 min 25 s`` / ``1 h 8 min`` for the Information list. */
export function formatLength(seconds: number | null | undefined): string | null {
  if (seconds == null || seconds <= 0) return null
  const total = Math.round(seconds)
  const h = Math.floor(total / 3600)
  const m = Math.floor((total % 3600) / 60)
  const s = total % 60
  if (h > 0) return m > 0 ? `${h} h ${m} min` : `${h} h`
  return s > 0 ? `${m} min ${s} s` : `${m} min`
}

/** ``58 min`` — the primary action's label (spec #76 §3.2). */
export function formatMinutes(seconds: number | null | undefined): string | null {
  if (seconds == null || seconds <= 0) return null
  return `${Math.max(1, Math.round(seconds / 60))} min`
}

/** ``English`` from ``en`` via ``Intl.DisplayNames``; the code itself when unknown. */
export function languageName(code: string | null | undefined): string | null {
  if (!code) return null
  const primary = code.trim().split(/[-_]/, 1)[0].toLowerCase()
  if (!primary) return null
  try {
    const name = new Intl.DisplayNames(['en'], { type: 'language' }).of(primary)
    return name && name !== primary ? name : primary.toUpperCase()
  } catch {
    return primary.toUpperCase()
  }
}

/** ``profgmedia.com`` for a show-notes link; ``null`` when the URL does not parse. */
export function hostOf(url: string | null | undefined): string | null {
  if (!url) return null
  try {
    return new URL(url).hostname.replace(/^www\./, '')
  } catch {
    return null
  }
}

/** ``S1 E5`` / ``E5`` / ``S1``; ``null`` when neither is set. */
export function episodeNumberLabel(
  seasonNumber: number | null | undefined,
  episodeNumber: number | null | undefined,
): string | null {
  if (seasonNumber && episodeNumber) return `S${seasonNumber} E${episodeNumber}`
  if (episodeNumber) return `E${episodeNumber}`
  if (seasonNumber) return `S${seasonNumber}`
  return null
}

/** ``Bonus`` / ``Trailer``; ``null`` for a full episode or no type. */
export function episodeTypeLabel(episodeType: string | null | undefined): string | null {
  if (!episodeType || episodeType === 'full') return null
  return episodeType.charAt(0).toUpperCase() + episodeType.slice(1)
}

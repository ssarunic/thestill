/**
 * Recover the stable origin image from a Transistor imgproxy URL.
 *
 * Transistor episode/podcast artwork is served through signed imgproxy URLs of
 * the form:
 *   https://img.transistorcdn.com/{signature}/{opt:...}/{opt:...}/{base64url-of-origin}.jpg
 *
 * The signature eventually expires and the CDN returns 404, but the original
 * source URL is base64url-encoded in the trailing path segments, so we can
 * decode it and load the origin directly. Returns null for any URL that is not
 * a recognisable Transistor imgproxy URL.
 */
export function transistorOrigin(url: string): string | null {
  try {
    const u = new URL(url)
    if (!u.hostname.endsWith('transistorcdn.com')) return null
    // Drop the leading signature segment; processing options contain ':',
    // the remaining segments are the base64url-encoded source URL.
    const segs = u.pathname.split('/').filter(Boolean).slice(1)
    const b64 = segs
      .filter((s) => !s.includes(':'))
      .join('')
      .replace(/\.\w+$/, '')
    if (!b64) return null
    const std = b64.replace(/-/g, '+').replace(/_/g, '/')
    const decoded = atob(std + '='.repeat((4 - (std.length % 4)) % 4))
    return decoded.startsWith('http') ? decoded : null
  } catch {
    return null
  }
}

/**
 * Build an ordered, de-duplicated list of image URLs to try. Each input URL is
 * followed immediately by its self-healed origin (when applicable), so a broken
 * signed URL falls back to its own true image before dropping to the next
 * source (e.g. episode artwork -> episode origin -> podcast artwork).
 */
export function expandImageCandidates(
  urls: (string | null | undefined)[],
): string[] {
  const out: string[] = []
  for (const u of urls) {
    if (!u) continue
    if (!out.includes(u)) out.push(u)
    const origin = transistorOrigin(u)
    if (origin && !out.includes(origin)) out.push(origin)
  }
  return out
}

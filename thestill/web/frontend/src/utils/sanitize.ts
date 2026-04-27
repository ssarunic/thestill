// Spec #25 item 3.2 — single sanitization entry point for any HTML
// that came from outside our control (RSS feed descriptions, episode
// HTML notes, etc.). Centralising this means new callers can't forget
// to add the noopener-forcing hook or accidentally widen the allowlist.
//
// DOMPurify already strips ``javascript:`` URIs and most known XSS
// vectors with default settings; this module adds two small extras:
//
// 1. A conservative tag/attr allowlist matched to what podcast feeds
//    actually need (paragraph, line-break, basic emphasis, lists,
//    links). Anything else — `<script>`, `<iframe>`, `<style>`,
//    `<svg>`, raw `on*` handlers — is dropped silently by the
//    allowlist.
// 2. An ``afterSanitizeAttributes`` hook that forces
//    ``rel="noopener noreferrer"`` and ``target="_blank"`` on every
//    surviving `<a>`. Without this an attacker can use ``target=...``
//    + ``window.opener`` to nudge the parent tab, even with the URL
//    sanitised.

import DOMPurify from 'dompurify'

const ALLOWED_TAGS = ['p', 'br', 'strong', 'b', 'em', 'i', 'a', 'ul', 'ol', 'li']
const ALLOWED_ATTR = ['href', 'target', 'rel']

let hookRegistered = false

function ensureHook() {
  if (hookRegistered) return
  hookRegistered = true
  DOMPurify.addHook('afterSanitizeAttributes', (node) => {
    if (node.tagName === 'A') {
      // Open external links in a new tab without leaking ``window.opener``.
      node.setAttribute('target', '_blank')
      node.setAttribute('rel', 'noopener noreferrer')
    }
  })
}

/**
 * Sanitise HTML that originated from an untrusted source (RSS feed,
 * external description, etc.) and is destined for
 * ``dangerouslySetInnerHTML``. Returns the safe HTML string.
 *
 * The output:
 * - Contains only tags from ``ALLOWED_TAGS``.
 * - Has ``<a>`` tags forced to ``target="_blank" rel="noopener noreferrer"``.
 * - Has ``javascript:`` and ``data:`` href URIs stripped (DOMPurify default).
 */
export function sanitizeUntrustedHtml(html: string): string {
  ensureHook()
  return DOMPurify.sanitize(html, {
    ALLOWED_TAGS,
    ALLOWED_ATTR,
  })
}

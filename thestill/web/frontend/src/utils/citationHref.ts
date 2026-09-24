// Spec #54 — the summarizer cites transcript passages as relative links of
// the form `?t=<seconds>&cite=<id>`. The `cite` id resolves through the
// citations sidecar the summary endpoint returns. Shared by the summary's
// link renderer (the citation button) and the entity-mention plugin (spec
// #82), which borrows the nearest citation's segment for a name in the
// same block.
export function parseCitationId(href: string | null | undefined): string | null {
  if (!href?.startsWith('?')) return null
  return new URLSearchParams(href.slice(1)).get('cite')
}

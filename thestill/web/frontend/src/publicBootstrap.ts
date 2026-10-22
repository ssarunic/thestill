// Spec #105 — the data block the server writes into a signed-out public
// page. Each entry names a ``publicKeys`` factory and its arguments, so the
// app seeds exactly the keys its hooks read, then renders the page from
// cache: no skeleton, and no repeat of the API calls the server already
// made. A missing, malformed or other-version block is ignored, and the
// page fetches as it always has.
import type { QueryClient } from '@tanstack/react-query'
import { publicKeys, type PublicQueryName } from './api/public'

export const BOOTSTRAP_ID = 'thestill-bootstrap'
export const BOOTSTRAP_VERSION = 1

export type PrerenderedPage = 'episode' | 'podcast' | 'entity' | 'landing' | 'top'

const PAGES: readonly PrerenderedPage[] = ['episode', 'podcast', 'entity', 'landing', 'top']

interface BootstrapEntry {
  query: PublicQueryName
  args: unknown[]
  data: unknown
  /** Present for an infinite query: the first page's param. */
  page_param?: unknown
}

export interface PublicBootstrap {
  v: number
  page: PrerenderedPage
  viewer: 'anonymous'
  queries: BootstrapEntry[]
}

export function readBootstrap(doc: Document = document): PublicBootstrap | null {
  const text = doc.getElementById(BOOTSTRAP_ID)?.textContent
  if (!text) return null
  try {
    const parsed = JSON.parse(text) as Partial<PublicBootstrap>
    if (parsed.v !== BOOTSTRAP_VERSION || !PAGES.includes(parsed.page as PrerenderedPage) || !Array.isArray(parsed.queries)) {
      return null
    }
    return parsed as PublicBootstrap
  } catch {
    return null
  }
}

/** Seed every entry the app knows; returns how many were seeded. */
export function seedBootstrap(client: QueryClient, bootstrap: PublicBootstrap): number {
  let seeded = 0
  for (const entry of bootstrap.queries) {
    if (!entry || !Object.prototype.hasOwnProperty.call(publicKeys, entry.query) || !Array.isArray(entry.args)) continue
    const factory = publicKeys[entry.query] as (...args: unknown[]) => readonly unknown[]
    const data = entry.page_param === undefined ? entry.data : { pages: [entry.data], pageParams: [entry.page_param] }
    client.setQueryData(factory(...entry.args), data)
    seeded += 1
  }
  return seeded
}

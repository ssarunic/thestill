// Spec #94 — the signed-out public site, its own chunk (no player, no inbox).
// Spec #105 — preloadable, with the page a server-written URL shows.
import type { PrerenderedPage } from './publicBootstrap'
import { preloadable } from './utils/preloadable'

export const PublicApp = preloadable(() => import('./PublicApp'))

export async function preloadPublicSite(page: PrerenderedPage): Promise<void> {
  await PublicApp.preload()
  const { preloadPublicPage } = await import('./pages/public/registry')
  await preloadPublicPage(page)
}

// Spec #105 — the public pages as preloadable chunks. For a page the server
// wrote, main.tsx loads its chunks before the first render, so React's first
// commit replaces the server HTML with the full page, not a fallback.
import type { PrerenderedPage } from '../../publicBootstrap'
import { PublicSummaryViewer } from '../../components/public/summaryViewer'
import { preloadable } from '../../utils/preloadable'

export const Landing = preloadable(() => import('./Landing'))
export const TopChart = preloadable(() => import('./TopChart'))
export const Show = preloadable(() => import('./Show'))
export const Episode = preloadable(() => import('./Episode'))
export const EntityPage = preloadable(() => import('./EntityPage'))

const PRELOADS: Record<PrerenderedPage, (() => Promise<unknown>)[]> = {
  episode: [Episode.preload, PublicSummaryViewer.preload],
  podcast: [Show.preload],
  entity: [EntityPage.preload],
  landing: [Landing.preload, PublicSummaryViewer.preload],
  top: [TopChart.preload],
}

export function preloadPublicPage(page: PrerenderedPage): Promise<unknown> {
  return Promise.all(PRELOADS[page].map((load) => load()))
}

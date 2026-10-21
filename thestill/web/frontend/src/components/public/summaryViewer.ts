// Spec #105 — the summary renderer for public pages, preloadable so a page
// the server wrote shows its summary on the first render, not a skeleton.
import { preloadable } from '../../utils/preloadable'

export const PublicSummaryViewer = preloadable(() => import('../SummaryViewer'))

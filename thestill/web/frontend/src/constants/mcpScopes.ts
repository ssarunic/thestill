import type { McpTokenScope } from '../api/types'

// Labels for the MCP scopes (spec #78 Phase 2, spec #109), shared by the
// Settings connector card and the OAuth consent screen.
export const SCOPE_COPY: Record<McpTokenScope, { label: string; hint: string }> = {
  read: { label: 'Read', hint: 'Podcasts, episodes, transcripts, summaries, search. Always on.' },
  follows: { label: 'Follows', hint: 'Add podcasts and unfollow. Adding a feed runs the pipeline, so this is off by default.' },
  pipeline: { label: 'Pipeline', hint: 'Refresh, download, transcribe, summarise. Admins only.' },
}

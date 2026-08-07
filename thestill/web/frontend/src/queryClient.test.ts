import { describe, expect, it } from 'vitest'
import { createQueryClient } from './queryClient'

// Spec #69 Phase 3 — the app must not have a default refetchInterval.
// The old app-wide 5s poll made every mounted query (including every
// loaded page of every infinite query) re-hit its endpoint every 5
// seconds. Live behavior is opt-in per hook; see queryClient.ts for the
// roster. If you need a new live view, set refetchInterval on that hook —
// do not weaken these assertions.
describe('createQueryClient', () => {
  it('sets no app-wide refetchInterval', () => {
    const defaults = createQueryClient().getDefaultOptions()
    expect(defaults.queries?.refetchInterval).toBeUndefined()
    expect(defaults.queries?.refetchIntervalInBackground).toBeUndefined()
  })

  it('keeps a 60s default staleTime so focus revalidation stays cheap', () => {
    const defaults = createQueryClient().getDefaultOptions()
    expect(defaults.queries?.staleTime).toBe(60_000)
  })
})

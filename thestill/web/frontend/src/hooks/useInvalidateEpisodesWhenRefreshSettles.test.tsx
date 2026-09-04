import { describe, it, expect, vi } from 'vitest'
import { renderHook } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { ReactNode } from 'react'
import { useInvalidateEpisodesWhenRefreshSettles } from './useApi'

/**
 * Spec #74 — the detail page invalidates its episode list exactly once, when
 * the server's `refresh_pending` flag flips from true to false. A page that
 * was never pending must not invalidate on mount.
 */
function setup() {
  const queryClient = new QueryClient()
  const invalidate = vi.spyOn(queryClient, 'invalidateQueries')
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  )
  const hook = renderHook(
    ({ pending }: { pending: boolean | undefined }) =>
      useInvalidateEpisodesWhenRefreshSettles('show', pending),
    { wrapper, initialProps: { pending: undefined as boolean | undefined } },
  )
  return { hook, invalidate }
}

describe('useInvalidateEpisodesWhenRefreshSettles', () => {
  it('invalidates the episode list once when pending flips true → false', () => {
    const { hook, invalidate } = setup()

    hook.rerender({ pending: true })
    expect(invalidate).not.toHaveBeenCalled()

    hook.rerender({ pending: true })
    hook.rerender({ pending: false })
    expect(invalidate).toHaveBeenCalledTimes(1)
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ['podcasts', 'show', 'episodes'] })

    hook.rerender({ pending: false })
    expect(invalidate).toHaveBeenCalledTimes(1)
  })

  it('never invalidates when the page was never pending', () => {
    const { hook, invalidate } = setup()
    hook.rerender({ pending: false })
    hook.rerender({ pending: undefined })
    expect(invalidate).not.toHaveBeenCalled()
  })
})

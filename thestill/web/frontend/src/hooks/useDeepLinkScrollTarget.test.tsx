import { describe, it, expect, beforeEach, vi } from 'vitest'
import { useEffect } from 'react'
import { act, render, screen } from '@testing-library/react'
import { MemoryRouter, useNavigate } from 'react-router-dom'
import type { AnnotatedSegment } from '../api/types'
import { useDeepLinkScrollTarget, __resetDeepLinkScrollForTests } from './useDeepLinkScrollTarget'

const seg = (id: number, start: number, end: number): AnnotatedSegment =>
  ({ id, start, end, speaker: null, text: `s${id}`, kind: 'content', sponsor: null, source_segment_ids: [], source_word_span: null, user_segment_id: null }) as AnnotatedSegment

const SEGMENTS = [seg(1, 0, 10), seg(2, 10, 20), seg(3, 30, 40)]

// Captured in an effect (react-hooks/globals) so tests can drive history.
const navRef: { current: ReturnType<typeof useNavigate> | null } = { current: null }
const nav: ReturnType<typeof useNavigate> = (...args: unknown[]) =>
  (navRef.current as unknown as (...a: unknown[]) => void)(...args)
function Harness({ onTarget, segments = SEGMENTS, offset = 0 }: { onTarget: (id: number) => void; segments?: AnnotatedSegment[] | null; offset?: number }) {
  const navigate = useNavigate()
  useEffect(() => {
    navRef.current = navigate
  })
  useDeepLinkScrollTarget({ episodeId: 'ep-1', segments, offset, onTarget })
  return <div data-testid="ok" />
}

describe('useDeepLinkScrollTarget (spec #72 §Deep link)', () => {
  beforeEach(() => __resetDeepLinkScrollForTests())

  it('resolves ?t= to the segment under it once, then not again on a return to the same entry', () => {
    const onTarget = vi.fn()
    render(
      <MemoryRouter initialEntries={['/inbox', '/ep?t=15']} initialIndex={1}>
        <Harness onTarget={onTarget} />
      </MemoryRouter>,
    )
    expect(onTarget).toHaveBeenCalledTimes(1)
    expect(onTarget).toHaveBeenCalledWith(2)

    // Back to the inbox and Forward again (a POP to an already-handled entry).
    act(() => nav(-1))
    act(() => nav(1))
    expect(screen.getByTestId('ok')).toBeInTheDocument()
    expect(onTarget).toHaveBeenCalledTimes(1)
  })

  it('fires again for a fresh push carrying a new t', () => {
    const onTarget = vi.fn()
    render(
      <MemoryRouter initialEntries={['/ep?t=5']}>
        <Harness onTarget={onTarget} />
      </MemoryRouter>,
    )
    expect(onTarget).toHaveBeenLastCalledWith(1)
    act(() => nav({ pathname: '/ep', search: '?t=35' }))
    expect(onTarget).toHaveBeenCalledTimes(2)
    expect(onTarget).toHaveBeenLastCalledWith(3)
  })

  it('lands on the last started segment when t falls in a gap, honours the offset, ignores junk', () => {
    const onTarget = vi.fn()
    render(
      <MemoryRouter initialEntries={['/ep?t=25']}>
        <Harness onTarget={onTarget} />
      </MemoryRouter>,
    )
    expect(onTarget).toHaveBeenCalledWith(2)

    __resetDeepLinkScrollForTests()
    const withOffset = vi.fn()
    render(
      <MemoryRouter initialEntries={['/ep?t=45']}>
        <Harness onTarget={withOffset} offset={30} />
      </MemoryRouter>,
    )
    // Engine 45 - offset 30 = transcript 15 → segment 2.
    expect(withOffset).toHaveBeenCalledWith(2)

    __resetDeepLinkScrollForTests()
    const junk = vi.fn()
    render(
      <MemoryRouter initialEntries={['/ep?t=abc']}>
        <Harness onTarget={junk} />
      </MemoryRouter>,
    )
    expect(junk).not.toHaveBeenCalled()
  })

  it('waits for segments before resolving', () => {
    const onTarget = vi.fn()
    const { rerender } = render(
      <MemoryRouter initialEntries={['/ep?t=15']}>
        <Harness onTarget={onTarget} segments={null} />
      </MemoryRouter>,
    )
    expect(onTarget).not.toHaveBeenCalled()
    rerender(
      <MemoryRouter initialEntries={['/ep?t=15']}>
        <Harness onTarget={onTarget} segments={SEGMENTS} />
      </MemoryRouter>,
    )
    expect(onTarget).toHaveBeenCalledWith(2)
  })
})

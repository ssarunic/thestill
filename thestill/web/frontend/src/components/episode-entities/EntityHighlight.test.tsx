import { fireEvent, render, screen, act } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'
import EntityHighlight from './EntityHighlight'
import type { EntityCitationRow, EpisodeEntity, MentionLite } from '../../api/types'

vi.mock('../../hooks/useApi', () => ({
  useEntitySummary: vi.fn(),
}))

import { useEntitySummary } from '../../hooks/useApi'

function mention(id: number, segmentId: number, startMs: number): MentionLite {
  return {
    id,
    entity_id: 'person:alice',
    segment_id: segmentId,
    start_ms: startMs,
    end_ms: startMs + 1000,
    speaker: null,
    role: null,
    surface_form: 'Alice',
    quote_excerpt: 'Alice',
    confidence: 0.9,
    sentiment: null,
  }
}

const MENTIONS = [mention(1, 10, 5_000), mention(2, 20, 65_000), mention(3, 30, 125_000)]

const ALICE: EpisodeEntity = {
  entity: { id: 'person:alice', type: 'person', canonical_name: 'Alice', wikidata_qid: 'Q1' },
  mention_count: 3,
  first_mention_ms: 5_000,
  speaker_kind: 'unknown',
  salience: 3,
  mentions: MENTIONS,
}

function citation(episodeId: string, podcast: string, episode: string, startMs = 30_000): EntityCitationRow {
  return {
    episode_id: episodeId,
    podcast_id: 'p1',
    podcast_slug: 'show',
    episode_slug: `ep-${episodeId}`,
    podcast_title: podcast,
    episode_title: episode,
    published_at: null,
    start_ms: startMs,
    end_ms: startMs + 1000,
    speaker: null,
    quote: '',
    surface_form: 'Alice',
  }
}

function LocationProbe() {
  const location = useLocation()
  return <div data-testid="location">{location.pathname}</div>
}

// `onSegmentActivate` stands in for the seekable transcript segment that
// wraps every highlight: its click / Enter handlers seek playback.
function renderHighlight(
  props: Partial<React.ComponentProps<typeof EntityHighlight>> = {},
  onSegmentActivate = vi.fn(),
) {
  return render(
    <MemoryRouter initialEntries={['/podcasts/show/episodes/ep-1']}>
        <Routes>
          <Route
            path="*"
            element={
              <>
                <p onClick={onSegmentActivate} onKeyDown={onSegmentActivate}>
                  <EntityHighlight episodeEntity={ALICE} mention={MENTIONS[1]} episodeId="e1" {...props}>
                    Alice
                  </EntityHighlight>
                </p>
                {/* The other mentions' anchors, as the transcript would render them. */}
                <a id="m=person:alice:10" href="/x">
                  first
                </a>
                <a id="m=person:alice:30" href="/x">
                  third
                </a>
                <LocationProbe />
              </>
            }
          />
        </Routes>
    </MemoryRouter>,
  )
}

describe('EntityHighlight', () => {
  beforeEach(() => {
    vi.mocked(useEntitySummary).mockReturnValue({ data: undefined } as never)
    Element.prototype.scrollIntoView = vi.fn()
  })
  afterEach(() => {
    vi.clearAllMocks()
  })

  it('keeps the entity href so modifier clicks still open a new tab', () => {
    renderHighlight()
    expect(screen.getByRole('link', { name: /Alice, Person/ })).toHaveAttribute(
      'href',
      '/entities/person/alice',
    )
  })

  it('a plain click pins the peek instead of navigating', () => {
    renderHighlight()
    const link = screen.getByRole('link', { name: /Alice, Person/ })
    fireEvent.click(link)
    expect(screen.getByTestId('entity-hover-card')).toBeInTheDocument()
    expect(screen.getByTestId('location')).toHaveTextContent('/podcasts/show/episodes/ep-1')
    expect(link).toHaveAttribute('aria-expanded', 'true')
    // Mouse-out does not dismiss a pinned card…
    vi.useFakeTimers()
    try {
      fireEvent.mouseLeave(link)
      act(() => {
        vi.advanceTimersByTime(500)
      })
    } finally {
      vi.useRealTimers()
    }
    expect(screen.getByTestId('entity-hover-card')).toBeInTheDocument()
    // …Escape does, and claims the key before the reader overlay's own
    // bubbling `document` listener sees it, so the overlay stays open.
    const overlayListener = vi.fn((e: KeyboardEvent) => e.defaultPrevented)
    document.addEventListener('keydown', overlayListener)
    try {
      link.focus()
      const esc = new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true })
      act(() => {
        link.dispatchEvent(esc)
      })
      expect(esc.defaultPrevented).toBe(true)
      expect(overlayListener).toHaveReturnedWith(true)
    } finally {
      document.removeEventListener('keydown', overlayListener)
    }
    expect(screen.queryByTestId('entity-hover-card')).not.toBeInTheDocument()
  })

  it('renders the desktop card on the transient rung, above the reader overlay', () => {
    renderHighlight()
    fireEvent.click(screen.getByRole('link', { name: /Alice, Person/ }))
    const wrapper = screen.getByTestId('entity-hover-card').parentElement as HTMLElement
    expect(Number(wrapper.style.zIndex)).toBeGreaterThan(45)
    expect(wrapper.style.position).toBe('fixed')
  })

  it('clicks and keys inside the card never reach the seekable segment', () => {
    const onSegmentActivate = vi.fn()
    renderHighlight({ onSeek: vi.fn() }, onSegmentActivate)
    fireEvent.click(screen.getByRole('link', { name: /Alice, Person/ }))
    const next = screen.getByRole('button', { name: 'Next mention at 2:05' })
    fireEvent.keyDown(next, { key: 'Enter' })
    fireEvent.click(next)
    expect(onSegmentActivate).not.toHaveBeenCalled()
  })

  it('follows its word through a scroll and closes only once the word leaves the viewport', () => {
    renderHighlight()
    const link = screen.getByRole('link', { name: /Alice, Person/ })
    const rect = (top: number) =>
      ({ top, bottom: top + 16, left: 40, right: 80, width: 40, height: 16 }) as DOMRect
    link.getBoundingClientRect = () => rect(100)
    fireEvent.click(link)
    const wrapper = () => screen.getByTestId('entity-hover-card').parentElement as HTMLElement
    expect(wrapper().style.top).toBe('120px')

    // Follow-playback auto-scroll moves the word up: the card moves with it.
    link.getBoundingClientRect = () => rect(60)
    fireEvent.scroll(document.body)
    expect(wrapper().style.top).toBe('80px')
    expect(link).toHaveAttribute('aria-expanded', 'true')

    // Scrolled off the top: nothing to point at any more.
    link.getBoundingClientRect = () => rect(-200)
    fireEvent.scroll(document.body)
    expect(screen.queryByTestId('entity-hover-card')).not.toBeInTheDocument()
  })

  it('fetches the entity summary on click, but on hover only once the hover has settled', () => {
    vi.useFakeTimers()
    try {
      renderHighlight()
      const link = screen.getByRole('link', { name: /Alice, Person/ })
      fireEvent.mouseEnter(link)
      expect(screen.getByTestId('entity-hover-card')).toBeInTheDocument()
      expect(useEntitySummary).toHaveBeenLastCalledWith(null, null)
      act(() => {
        vi.advanceTimersByTime(300)
      })
      expect(useEntitySummary).toHaveBeenLastCalledWith('person', 'alice')

      // A sweep across the word never fetches.
      fireEvent.mouseLeave(link)
      act(() => {
        vi.advanceTimersByTime(200)
      })
      vi.mocked(useEntitySummary).mockClear()
      fireEvent.mouseEnter(link)
      fireEvent.mouseLeave(link)
      act(() => {
        vi.advanceTimersByTime(1000)
      })
      expect(useEntitySummary).not.toHaveBeenCalledWith('person', 'alice')
    } finally {
      vi.useRealTimers()
    }
  })

  it('prev/next only offer mentions whose highlight is rendered, one per segment', () => {
    const extra = [
      // Same segment as the current mention: shares its anchor.
      mention(4, 20, 66_000),
      // Below the confidence floor / not in the DOM: no anchor to jump to.
      mention(5, 40, 200_000),
    ]
    renderHighlight({
      episodeEntity: { ...ALICE, mention_count: 5, mentions: [...MENTIONS, ...extra] },
    })
    fireEvent.click(screen.getByRole('link', { name: /Alice, Person/ }))
    expect(screen.getByTestId('entity-hover-card')).toHaveTextContent('5× this episode · 2 of 3')
    expect(screen.getByRole('button', { name: 'Next mention at 2:05' })).toBeEnabled()
    expect(screen.queryByRole('button', { name: /3:20/ })).not.toBeInTheDocument()
  })

  it('a modifier click falls through to the browser', () => {
    renderHighlight()
    const link = screen.getByRole('link', { name: /Alice, Person/ })
    // fireEvent returns false when the handler called preventDefault.
    // (jsdom logs "Not implemented: navigation" here — that is the
    // browser default going ahead, which is the point.)
    expect(fireEvent.click(link, { metaKey: true })).toBe(true)
    expect(screen.queryByTestId('entity-hover-card')).not.toBeInTheDocument()
  })

  it('does not bubble the click to the seekable segment', () => {
    const onSegmentClick = vi.fn()
    render(
      <MemoryRouter>
        <div onClick={onSegmentClick}>
          <EntityHighlight episodeEntity={ALICE} mention={MENTIONS[1]}>
            Alice
          </EntityHighlight>
        </div>
      </MemoryRouter>,
    )
    fireEvent.click(screen.getByRole('link', { name: /Alice, Person/ }))
    expect(onSegmentClick).not.toHaveBeenCalled()
  })

  it('a click outside closes the pinned card', () => {
    renderHighlight()
    fireEvent.click(screen.getByRole('link', { name: /Alice, Person/ }))
    expect(screen.getByTestId('entity-hover-card')).toBeInTheDocument()
    fireEvent.mouseDown(document.body)
    expect(screen.queryByTestId('entity-hover-card')).not.toBeInTheDocument()
  })

  it('shows the in-episode position and jumps to the previous / next mention without seeking', () => {
    const onSeek = vi.fn()
    renderHighlight({ onSeek })
    fireEvent.click(screen.getByRole('link', { name: /Alice, Person/ }))
    expect(screen.getByTestId('entity-hover-card')).toHaveTextContent('3× this episode · 2 of 3')

    const third = document.getElementById('m=person:alice:30') as HTMLElement
    const first = document.getElementById('m=person:alice:10') as HTMLElement
    const scrollThird = vi.fn()
    const scrollFirst = vi.fn()
    third.scrollIntoView = scrollThird
    first.scrollIntoView = scrollFirst

    fireEvent.click(screen.getByRole('button', { name: 'Next mention at 2:05' }))
    expect(scrollThird).toHaveBeenCalledWith(expect.objectContaining({ block: 'center' }))
    expect(scrollFirst).not.toHaveBeenCalled()
    expect(onSeek).not.toHaveBeenCalled()
    // The jump closes the peek.
    expect(screen.queryByTestId('entity-hover-card')).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole('link', { name: /Alice, Person/ }))
    fireEvent.click(screen.getByRole('button', { name: 'Previous mention at 0:05' }))
    expect(scrollFirst).toHaveBeenCalledTimes(1)
  })

  it('the ▶ button seeks to this mention', () => {
    const onSeek = vi.fn()
    renderHighlight({ onSeek })
    fireEvent.click(screen.getByRole('link', { name: /Alice, Person/ }))
    fireEvent.click(screen.getByRole('button', { name: 'Play from 1:05' }))
    expect(onSeek).toHaveBeenCalledWith(65)
  })

  it('shows the gloss and other episodes from the entity summary, skipping this episode', () => {
    vi.mocked(useEntitySummary).mockReturnValue({
      data: {
        description: 'A very important person.',
        recent_mentions: [
          citation('e1', 'This Show', 'This episode'),
          citation('e2', 'This Show', 'Last week', 42_000),
          citation('e2', 'This Show', 'Last week', 99_000),
          citation('e3', 'Other Show', 'Guest spot', 600_000),
        ],
      },
    } as never)
    renderHighlight()
    fireEvent.click(screen.getByRole('link', { name: /Alice, Person/ }))
    expect(useEntitySummary).toHaveBeenCalledWith('person', 'alice')
    expect(screen.getByTestId('entity-peek-gloss')).toHaveTextContent('A very important person.')
    const elsewhere = screen.getByTestId('entity-peek-elsewhere')
    expect(elsewhere).not.toHaveTextContent('This episode')
    const links = elsewhere.querySelectorAll('a')
    expect(links).toHaveLength(2)
    expect(links[0]).toHaveTextContent('This Show · Last week 0:42')
    expect(links[0]).toHaveAttribute('href', '/podcasts/show/episodes/ep-e2?t=42')
    expect(links[1]).toHaveTextContent('Other Show · Guest spot 10:00')
  })

  it('"Open entity page" is an in-app navigation', () => {
    renderHighlight()
    fireEvent.click(screen.getByRole('link', { name: /Alice, Person/ }))
    fireEvent.click(screen.getByRole('link', { name: 'Open entity page →' }))
    expect(screen.getByTestId('location')).toHaveTextContent('/entities/person/alice')
    expect(screen.queryByTestId('entity-hover-card')).not.toBeInTheDocument()
  })

  describe('on a phone', () => {
    function renderPhone(onSegmentActivate = vi.fn()) {
      return renderHighlight({ isSmUp: false }, onSegmentActivate)
    }

    it('hover does nothing; a tap opens a bottom sheet and the scrim closes it', () => {
      renderPhone()
      const link = screen.getByRole('link', { name: /Alice, Person/ })
      fireEvent.mouseEnter(link)
      expect(screen.queryByTestId('entity-hover-card')).not.toBeInTheDocument()

      fireEvent.click(link)
      const sheet = screen.getByTestId('entity-peek-sheet')
      expect(sheet).toHaveAttribute('aria-modal', 'true')
      expect(sheet).toHaveAttribute('aria-label', 'Alice — Person')
      expect(screen.getByTestId('entity-hover-card')).toBeInTheDocument()
      expect(screen.getByTestId('location')).toHaveTextContent('/podcasts/show/episodes/ep-1')

      fireEvent.click(screen.getByTestId('entity-peek-sheet-root').firstChild as Element)
      expect(screen.queryByTestId('entity-peek-sheet')).not.toBeInTheDocument()
    })

    it('swipe-down on the handle closes the sheet; a cancelled gesture does not', () => {
      renderPhone()
      fireEvent.click(screen.getByRole('link', { name: /Alice, Person/ }))
      const handle = screen.getByTestId('entity-peek-drag-handle')
      fireEvent.pointerDown(handle, { pointerId: 1, clientY: 100 })
      fireEvent.pointerMove(handle, { pointerId: 1, clientY: 150 })
      fireEvent.pointerCancel(handle, { pointerId: 1, clientY: 300 })
      expect(screen.getByTestId('entity-peek-sheet')).toBeInTheDocument()
      expect(screen.getByTestId('entity-peek-sheet').style.transform).toBe('')

      fireEvent.pointerDown(handle, { pointerId: 2, clientY: 100 })
      fireEvent.pointerMove(handle, { pointerId: 2, clientY: 150 })
      fireEvent.pointerUp(handle, { pointerId: 2, clientY: 200 })
      expect(screen.queryByTestId('entity-peek-sheet')).not.toBeInTheDocument()
    })

    it('is modal: locks page scroll, traps Tab, and claims Esc', () => {
      document.body.style.overflow = ''
      renderPhone()
      fireEvent.click(screen.getByRole('link', { name: /Alice, Person/ }))
      const sheet = screen.getByTestId('entity-peek-sheet')
      expect(document.body.style.overflow).toBe('hidden')

      const controls = sheet.querySelectorAll<HTMLElement>('a[href], button:not([disabled])')
      const last = controls[controls.length - 1]
      last.focus()
      fireEvent.keyDown(sheet, { key: 'Tab' })
      expect(document.activeElement).toBe(controls[0])

      const esc = new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true })
      act(() => {
        sheet.dispatchEvent(esc)
      })
      expect(esc.defaultPrevented).toBe(true)
      expect(screen.queryByTestId('entity-peek-sheet')).not.toBeInTheDocument()
      expect(document.body.style.overflow).toBe('')
    })

    it('taps inside the sheet never reach the seekable segment', () => {
      const onSegmentActivate = vi.fn()
      renderPhone(onSegmentActivate)
      fireEvent.click(screen.getByRole('link', { name: /Alice, Person/ }))
      fireEvent.click(screen.getByRole('button', { name: 'Next mention at 2:05' }))
      expect(onSegmentActivate).not.toHaveBeenCalled()
    })
  })
})

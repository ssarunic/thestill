import { describe, expect, it } from 'vitest'
import { render } from '@testing-library/react'
import KaraokeWord from './KaraokeWord'

describe('KaraokeWord', () => {
  it('renders the word text in a span', () => {
    const { container } = render(
      <KaraokeWord word={{ w: 'Hello', s: 0, e: 0.5 }} read={false} isActive={false} />,
    )
    const span = container.querySelector('span[data-karaoke-word]')
    expect(span?.textContent).toBe('Hello')
  })

  it('omits aria-current when inactive', () => {
    const { container } = render(
      <KaraokeWord word={{ w: 'Hello', s: 0, e: 0.5 }} read={false} isActive={false} />,
    )
    const span = container.querySelector('span[data-karaoke-word]')
    expect(span?.getAttribute('aria-current')).toBeNull()
  })

  it('sets aria-current="true" when active', () => {
    const { container } = render(
      <KaraokeWord word={{ w: 'Hello', s: 0, e: 0.5 }} read={true} isActive={true} />,
    )
    const span = container.querySelector('span[data-karaoke-word]')
    expect(span?.getAttribute('aria-current')).toBe('true')
  })

  it('uses the muted text colour for unread words', () => {
    const { container } = render(
      <KaraokeWord word={{ w: 'Hello', s: 0, e: 0.5 }} read={false} isActive={false} />,
    )
    const span = container.querySelector('span[data-karaoke-word]') as HTMLElement
    expect(span.className).toMatch(/text-gray-400/)
    expect(span.className).not.toMatch(/text-gray-900/)
  })

  it('uses the strong text colour for read words', () => {
    const { container } = render(
      <KaraokeWord word={{ w: 'Hello', s: 0, e: 0.5 }} read={true} isActive={false} />,
    )
    const span = container.querySelector('span[data-karaoke-word]') as HTMLElement
    expect(span.className).toMatch(/text-gray-900/)
    expect(span.className).not.toMatch(/text-gray-400/)
  })

  it('renders the currently-active word in the same colour as already-read words', () => {
    // The visual design merges read + currently-spoken into one colour;
    // the only differentiator on the active word is aria-current.
    const { container } = render(
      <KaraokeWord word={{ w: 'Hello', s: 0, e: 0.5 }} read={true} isActive={true} />,
    )
    const span = container.querySelector('span[data-karaoke-word]') as HTMLElement
    expect(span.className).toMatch(/text-gray-900/)
  })

  it('writes no inline background or gradient styles', () => {
    // The simplified design swaps text colour only — no background fills,
    // no gradient wipe. Confirm we're not leaking style attributes that
    // would carry the old karaoke wipe behaviour.
    const { container } = render(
      <KaraokeWord word={{ w: 'Hello', s: 0, e: 0.5 }} read={true} isActive={true} />,
    )
    const span = container.querySelector('span[data-karaoke-word]') as HTMLElement
    expect(span.style.backgroundImage).toBe('')
    expect(span.style.background).toBe('')
  })
})

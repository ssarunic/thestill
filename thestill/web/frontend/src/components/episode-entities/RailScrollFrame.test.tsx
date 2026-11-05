import { describe, expect, it } from 'vitest'
import { act, fireEvent, render, screen } from '@testing-library/react'
import RailScrollFrame from './RailScrollFrame'

// jsdom does no layout, so the frame's scroll geometry is stubbed per test.
function setGeometry(el: HTMLElement, { scrollHeight, clientHeight, scrollTop }: Record<string, number>) {
  Object.defineProperty(el, 'scrollHeight', { configurable: true, value: scrollHeight })
  Object.defineProperty(el, 'clientHeight', { configurable: true, value: clientHeight })
  el.scrollTop = scrollTop
}

describe('RailScrollFrame', () => {
  it('caps its height above the mini player so a tall rail scrolls on its own', () => {
    render(<RailScrollFrame host="page">rail</RailScrollFrame>)
    const frame = screen.getByTestId('entity-rail-scroll')
    expect(frame.className).toContain('overflow-y-auto')
    expect(frame.style.maxHeight).toBe('calc(100dvh - var(--player-h, 0px) - 5.5rem)')
  })

  it('pins below the collapsed episode bar on the page and near the top in the overlay', () => {
    const { container, rerender } = render(<RailScrollFrame host="page">rail</RailScrollFrame>)
    expect(container.firstElementChild?.className).toContain('top-[4.5rem]')
    rerender(<RailScrollFrame host="overlay">rail</RailScrollFrame>)
    expect(container.firstElementChild?.className).toContain('top-4')
  })

  it('shows the "more below" fade only while content is hidden below the edge', () => {
    render(<RailScrollFrame host="page">rail</RailScrollFrame>)
    const frame = screen.getByTestId('entity-rail-scroll')
    expect(screen.queryByTestId('entity-rail-more-below')).toBeNull()

    setGeometry(frame, { scrollHeight: 1200, clientHeight: 600, scrollTop: 0 })
    act(() => {
      fireEvent.scroll(frame)
    })
    expect(screen.getByTestId('entity-rail-more-below')).toBeTruthy()

    setGeometry(frame, { scrollHeight: 1200, clientHeight: 600, scrollTop: 600 })
    act(() => {
      fireEvent.scroll(frame)
    })
    expect(screen.queryByTestId('entity-rail-more-below')).toBeNull()
  })
})

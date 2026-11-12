import { act, render } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useAutoScrollFollow } from './useAutoScrollFollow'

// The row's top edge, as the test moves it; height and viewport are fixed.
let top = 0
let height = 100
const scrollIntoView = vi.fn()

function Harness({ onReady }: { onReady: (jump: () => void) => void }) {
  const follow = useAutoScrollFollow({ activeKey: null, enabled: false })
  onReady(() => follow.scrollToKey(1))
  return <div ref={follow.registerRef(1)} data-testid="row" />
}

function mount() {
  let jump = () => {}
  const view = render(<Harness onReady={(j) => (jump = j)} />)
  const row = view.getByTestId('row')
  row.scrollIntoView = scrollIntoView
  row.getBoundingClientRect = () => ({ top, height, bottom: top + height }) as DOMRect
  return { jump, ...view }
}

describe('useAutoScrollFollow jumps', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    scrollIntoView.mockReset()
    Object.defineProperty(window, 'innerHeight', { configurable: true, value: 800 })
    height = 100
  })
  afterEach(() => vi.useRealTimers())

  it('glides to a near row and jumps instantly to a far one', () => {
    top = 1200
    const near = mount()
    act(() => near.jump())
    expect(scrollIntoView).toHaveBeenLastCalledWith({ block: 'center', behavior: 'smooth' })
    near.unmount()

    top = 17000
    const far = mount()
    act(() => far.jump())
    expect(scrollIntoView).toHaveBeenLastCalledWith({ block: 'center', behavior: 'instant' })
  })

  it('re-aims while the row came to rest off screen, and stops once it is on screen', () => {
    top = 17000
    const { jump } = mount()
    act(() => jump())
    scrollIntoView.mockImplementation(() => {
      top = top > 5100 ? 2500 : 350 // lands short once more, then on screen
    })
    top = 5200 // the first jump fell short
    act(() => vi.advanceTimersByTime(2000))
    expect(scrollIntoView).toHaveBeenCalledTimes(3)
    expect(top).toBe(350)
    expect(vi.getTimerCount()).toBe(0)
  })

  it('accepts a row taller than the screen that lies across its middle', () => {
    top = 17000
    height = 1400
    const { jump } = mount()
    act(() => jump())
    top = -300 // centred: top is above the viewport
    act(() => vi.advanceTimersByTime(2000))
    expect(scrollIntoView).toHaveBeenCalledTimes(1)
  })

  it('gives up when a re-aim moves nothing (the row is at the end of the page)', () => {
    top = 17000
    const { jump } = mount()
    act(() => jump())
    top = 760 // partly below the fold, and the page cannot scroll further
    act(() => vi.advanceTimersByTime(2000))
    expect(scrollIntoView).toHaveBeenCalledTimes(2)
    expect(vi.getTimerCount()).toBe(0)
  })

  it('stops when the visitor scrolls, and on unmount', () => {
    top = 17000
    const first = mount()
    act(() => first.jump())
    top = 5200
    act(() => window.dispatchEvent(new Event('wheel')))
    act(() => vi.advanceTimersByTime(2000))
    expect(scrollIntoView).toHaveBeenCalledTimes(1)
    first.unmount()

    top = 17000
    const second = mount()
    act(() => second.jump())
    second.unmount()
    expect(vi.getTimerCount()).toBe(0)
  })
})

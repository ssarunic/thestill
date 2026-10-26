import { beforeEach, describe, expect, it, vi } from 'vitest'
import { act, renderHook } from '@testing-library/react'
import { useReaderEvents } from './useReaderEvents'
import { track } from '../analytics/client'

vi.mock('../analytics/client', () => ({ track: vi.fn() }))

const trackMock = track as ReturnType<typeof vi.fn>

function scroller(scrollHeight: number, clientHeight: number) {
  const el = document.createElement('div')
  Object.defineProperty(el, 'scrollHeight', { value: scrollHeight })
  Object.defineProperty(el, 'clientHeight', { value: clientHeight })
  return el
}

function scrollTo(el: HTMLElement, top: number) {
  el.scrollTop = top
  act(() => {
    el.dispatchEvent(new Event('scroll'))
  })
}

describe('useReaderEvents', () => {
  beforeEach(() => {
    trackMock.mockReset()
    // Runs the frame at once; 0 means "no frame pending" once it has run.
    vi.spyOn(window, 'requestAnimationFrame').mockImplementation((cb) => {
      cb(0)
      return 0
    })
  })

  it('sends each tab once per episode, the landing tab included', () => {
    const { rerender } = renderHook((props: { episodeId: string; tab: 'summary' | 'transcript' }) => useReaderEvents({ ...props, ready: true }), {
      initialProps: { episodeId: 'e1', tab: 'summary' },
    })
    rerender({ episodeId: 'e1', tab: 'transcript' })
    rerender({ episodeId: 'e1', tab: 'summary' })
    expect(trackMock.mock.calls).toEqual([
      ['reader_tab', { tab: 'summary' }],
      ['reader_tab', { tab: 'transcript' }],
    ])
    rerender({ episodeId: 'e2', tab: 'summary' })
    expect(trackMock).toHaveBeenLastCalledWith('reader_tab', { tab: 'summary' })
  })

  it('sends each depth milestone once, on scroll, only once the content is ready', () => {
    const el = scroller(1000, 200)
    const ref = { current: el }
    const { rerender } = renderHook((props: { ready: boolean }) => useReaderEvents({ episodeId: 'e1', tab: 'transcript', ready: props.ready, scrollContainerRef: ref }), {
      initialProps: { ready: false },
    })
    scrollTo(el, 800)
    expect(trackMock.mock.calls.filter(([name]) => name === 'read_depth')).toEqual([])

    rerender({ ready: true })
    scrollTo(el, 400) // bottom at 60%
    scrollTo(el, 100) // back up: nothing new
    scrollTo(el, 790) // bottom at 99%
    expect(trackMock.mock.calls.filter(([name]) => name === 'read_depth')).toEqual([
      ['read_depth', { tab: 'transcript', percent: 25 }],
      ['read_depth', { tab: 'transcript', percent: 50 }],
      ['read_depth', { tab: 'transcript', percent: 75 }],
      ['read_depth', { tab: 'transcript', percent: 100 }],
    ])
  })
})

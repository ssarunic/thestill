import { describe, expect, it } from 'vitest'
import { clampWindow, followWindow, tickStep, ticksFor, timeToX, xToTime, zoomWindow } from './laneMath'
import { formatClock } from './time'

describe('laneMath', () => {
  it('maps seconds to pixels and back inside the window', () => {
    expect(timeToX(90, 60, 120, 600)).toBe(150)
    expect(xToTime(150, 60, 120, 600)).toBe(90)
  })

  it('picks a tick step that keeps about eight ticks on screen', () => {
    expect(tickStep(20)).toBe(5)
    expect(tickStep(120)).toBe(15)
    expect(tickStep(600)).toBe(120)
    expect(tickStep(3600)).toBe(600)
  })

  it('lists ticks on multiples of the step inside the window', () => {
    expect(ticksFor(55, 60)).toEqual([60, 70, 80, 90, 100, 110])
  })

  it('clamps the window to the episode', () => {
    expect(clampWindow(-10, 120, 3600)).toBe(0)
    expect(clampWindow(3600, 120, 3600)).toBe(3480)
    expect(clampWindow(30, 120, 60)).toBe(0)
  })

  it('follows the playhead only when it leaves the middle of the window', () => {
    expect(followWindow(100, 60, 120, 3600)).toBe(60)
    expect(followWindow(170, 60, 120, 3600)).toBe(170 - 36)
    expect(followWindow(10, 60, 120, 3600)).toBe(0)
  })

  it('zooms around the anchor and bounds the span', () => {
    const out = zoomWindow(60, 120, 2, 120, 3600)
    expect(out.span).toBe(240)
    expect(out.start).toBe(0)
    expect(zoomWindow(60, 30, 0.5, 70, 3600).span).toBe(20)
    expect(zoomWindow(0, 120, 100, 60, 300).span).toBe(300)
  })
})

describe('formatClock', () => {
  it('formats minutes, hours and tenths', () => {
    expect(formatClock(31.26)).toBe('0:31')
    expect(formatClock(31.26, true)).toBe('0:31.2')
    expect(formatClock(3734)).toBe('1:02:14')
    expect(formatClock(-3)).toBe('0:00')
  })
})

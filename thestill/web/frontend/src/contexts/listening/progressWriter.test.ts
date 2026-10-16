// Spec #90 — ProgressWriter cadence, ordering, hold gate and retries.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { SendResult } from '../../api/listening'
import type { ProgressBody } from '../../api/types'
import { ProgressWriter, type Sample } from './progressWriter'

const EP = 'ep-1'
const okAck: SendResult = { kind: 'ok', ack: { accepted: true, completed: false, listening: null } }

function setup(overrides: { send?: ReturnType<typeof vi.fn> } = {}) {
  let sample: Sample | null = { episodeId: EP, position: 100, duration: 3600, playing: true }
  let ids = 0
  const send = overrides.send ?? vi.fn().mockResolvedValue(okAck)
  const onAck = vi.fn()
  const onNotFound = vi.fn()
  const onPendingChange = vi.fn()
  const writer = new ProgressWriter({
    send,
    sample: () => sample,
    onAck,
    onNotFound,
    onPendingChange,
    newId: () => `s${++ids}`,
  })
  return {
    writer,
    send,
    onAck,
    onNotFound,
    onPendingChange,
    setSample: (next: Partial<Sample> | null) => {
      sample = next === null ? null : { ...(sample as Sample), ...next }
    },
    bodies: () => send.mock.calls.map((c) => c[1] as ProgressBody),
  }
}

// Let queued promise chains settle under fake timers.
async function flushPromises() {
  for (let i = 0; i < 20; i++) await Promise.resolve()
}

beforeEach(() => {
  vi.useFakeTimers()
  vi.setSystemTime(new Date('2026-10-16T09:00:00Z'))
})

afterEach(() => {
  vi.useRealTimers()
  vi.restoreAllMocks()
})

describe('cadence', () => {
  it('ticks at most every 15 s of playback', async () => {
    const t = setup()
    t.writer.startSession(EP)
    t.writer.tick()
    expect(t.send).not.toHaveBeenCalled()

    vi.advanceTimersByTime(14_000)
    t.writer.tick()
    expect(t.send).not.toHaveBeenCalled()

    vi.advanceTimersByTime(1_000)
    t.writer.tick()
    await flushPromises()
    expect(t.bodies()).toEqual([
      { event: 'tick', position_seconds: 100, duration_seconds: 3600, session_id: 's1', seq: 1 },
    ])

    vi.advanceTimersByTime(5_000)
    t.writer.tick()
    expect(t.send).toHaveBeenCalledTimes(1)
  })

  it('never ticks while paused', () => {
    const t = setup()
    t.writer.startSession(EP)
    t.setSample({ playing: false })
    vi.advanceTimersByTime(60_000)
    t.writer.tick()
    expect(t.send).not.toHaveBeenCalled()
  })

  it('debounces seek writes by 1 s and writes the settled position', async () => {
    const t = setup()
    t.writer.startSession(EP)
    t.writer.onSeeked()
    t.setSample({ position: 200 })
    vi.advanceTimersByTime(500)
    t.writer.onSeeked()
    t.setSample({ position: 300 })
    vi.advanceTimersByTime(999)
    expect(t.send).not.toHaveBeenCalled()
    vi.advanceTimersByTime(1)
    await flushPromises()
    expect(t.bodies().map((b) => [b.event, b.position_seconds])).toEqual([['seek', 300]])
  })

  it('a pause flush cancels a pending seek write', async () => {
    const t = setup()
    t.writer.startSession(EP)
    t.writer.onSeeked()
    t.writer.flush('pause')
    vi.advanceTimersByTime(2_000)
    await flushPromises()
    expect(t.bodies().map((b) => b.event)).toEqual(['pause'])
  })
})

describe('sessions and ordering', () => {
  it('numbers writes within a session and starts over in a new one', async () => {
    const t = setup()
    t.writer.startSession(EP)
    t.writer.flush('pause')
    t.writer.flush('pause')
    t.writer.startSession(EP)
    t.writer.flush('pause')
    await flushPromises()
    expect(t.bodies().map((b) => [b.session_id, b.seq])).toEqual([
      ['s1', 1],
      ['s1', 2],
      ['s2', 1],
    ])
  })

  it('writes nothing without a session or for another episode', () => {
    const t = setup()
    t.writer.flush('pause')
    t.writer.startSession('other-episode')
    t.writer.flush('pause')
    expect(t.send).not.toHaveBeenCalled()
  })

  it('holds writes until the start seek lands, then the landing seek is not a write', async () => {
    const t = setup()
    t.writer.startSession(EP, { target: 1934 })
    t.setSample({ position: 0 })
    vi.advanceTimersByTime(20_000)
    t.writer.tick()
    t.writer.flush('pause')
    expect(t.send).not.toHaveBeenCalled()
    expect(t.writer.heldTarget()).toBe(1934)

    t.writer.onSeeked() // the resume seek landing
    vi.advanceTimersByTime(2_000)
    expect(t.send).not.toHaveBeenCalled()
    expect(t.writer.heldTarget()).toBeNull()

    t.setSample({ position: 1935 })
    t.writer.flush('pause')
    await flushPromises()
    expect(t.bodies().map((b) => b.position_seconds)).toEqual([1935])
  })

  it('a Stop during a hold records the held position', async () => {
    const t = setup()
    t.writer.startSession(EP, { target: 1934 })
    t.setSample({ position: 0, playing: false })
    t.writer.stop()
    await flushPromises()
    expect(t.bodies()).toEqual([
      expect.objectContaining({ event: 'stop', position_seconds: 1934 }),
    ])
    expect(t.writer.hasSession()).toBe(false)
  })

  it('a Stop while the held target is still unknown writes nothing (0:00 would erase it)', async () => {
    const t = setup()
    t.writer.startSession(EP, { target: null })
    t.setSample({ position: 0 })
    t.writer.stop()
    await flushPromises()
    expect(t.send).not.toHaveBeenCalled()
  })

  it('a Stop of an unheld session records the live position', async () => {
    const t = setup()
    t.writer.startSession(EP)
    t.setSample({ position: 321 })
    t.writer.stop()
    await flushPromises()
    expect(t.bodies()).toEqual([expect.objectContaining({ event: 'stop', position_seconds: 321 })])
  })

  it('a mark waits for an in-flight write to the same episode and uses its own session', async () => {
    let resolveTick: (r: SendResult) => void = () => {}
    const send = vi
      .fn()
      .mockImplementationOnce(() => new Promise<SendResult>((r) => (resolveTick = r)))
      .mockResolvedValue(okAck)
    const t = setup({ send })
    t.writer.startSession(EP)
    t.writer.flush('pause')
    const marked = t.writer.mark(EP, 'mark_played')
    await flushPromises()
    expect(send).toHaveBeenCalledTimes(1) // the mark is queued behind the pause

    resolveTick(okAck)
    await marked
    expect(t.bodies().map((b) => b.event)).toEqual(['pause', 'mark_played'])
    expect(t.bodies()[1]).toMatchObject({ session_id: 'mark-s2', seq: 0, position_seconds: 0 })
    // The marked episode's session is closed: a late tick cannot undo it.
    vi.advanceTimersByTime(30_000)
    t.writer.tick()
    expect(send).toHaveBeenCalledTimes(2)
  })

  it('keepalive writes leave synchronously, bypassing the queue', async () => {
    const send = vi.fn(() => new Promise<SendResult>(() => {}))
    const t = setup({ send })
    t.writer.startSession(EP)
    t.writer.flush('pause') // queued: leaves on the next microtask
    t.writer.flush('pause', { keepalive: true }) // the document is going away: now
    expect(send).toHaveBeenCalledTimes(1)
    expect(send.mock.calls[0][1]).toMatchObject({ seq: 2 })
    expect(send.mock.calls[0][2]).toEqual({ keepalive: true })
    await flushPromises()
    // seq, not arrival order, keeps the server right.
    expect(send.mock.calls.map((c) => (c[1] as ProgressBody).seq)).toEqual([2, 1])
  })
})

describe('acks, failures and pending writes', () => {
  it('reports acks with the triggering event and clears the pending write', async () => {
    const t = setup()
    t.writer.startSession(EP)
    t.writer.flush('ended')
    expect(t.writer.getPending()).not.toBeNull()
    await flushPromises()
    expect(t.onAck).toHaveBeenCalledWith(EP, okAck.kind === 'ok' ? okAck.ack : null, 'ended')
    expect(t.writer.getPending()).toBeNull()
    expect(t.onPendingChange).toHaveBeenCalledTimes(2)
  })

  it('keeps a failed write pending, warns once per streak, and the next write supersedes it', async () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {})
    const send = vi.fn().mockResolvedValue({ kind: 'failed' })
    const t = setup({ send })
    t.writer.startSession(EP)
    t.writer.flush('pause')
    await flushPromises()
    t.writer.flush('pause')
    await flushPromises()
    expect(warn).toHaveBeenCalledTimes(1)
    expect(t.writer.getPending()?.body.seq).toBe(2)

    send.mockResolvedValue(okAck)
    t.writer.flush('pause')
    await flushPromises()
    expect(t.writer.getPending()).toBeNull()
  })

  it('a 404 drops the pending write and reports the episode', async () => {
    const t = setup({ send: vi.fn().mockResolvedValue({ kind: 'not_found' }) })
    t.writer.startSession(EP)
    t.writer.flush('pause')
    await flushPromises()
    expect(t.writer.getPending()).toBeNull()
    expect(t.onNotFound).toHaveBeenCalledWith(EP)
  })

  it('re-sends an adopted pending write with its original session and seq', async () => {
    const t = setup()
    const body: ProgressBody = {
      event: 'tick',
      position_seconds: 50,
      duration_seconds: 100,
      session_id: 'old-tab',
      seq: 7,
    }
    t.writer.adoptPending({ episodeId: EP, body })
    await flushPromises()
    expect(t.send).toHaveBeenCalledWith(EP, body, { keepalive: false })
  })

  it('an unacknowledged write for the outgoing episode goes out before the next one', async () => {
    const send = vi.fn().mockResolvedValueOnce({ kind: 'failed' }).mockResolvedValue(okAck)
    vi.spyOn(console, 'warn').mockImplementation(() => {})
    const t = setup({ send })
    t.writer.startSession(EP)
    t.writer.flush('pause')
    await flushPromises()

    t.setSample({ episodeId: 'ep-2', position: 5 })
    t.writer.startSession('ep-2')
    t.writer.flush('pause')
    await flushPromises()
    expect(send.mock.calls.map((c) => c[0])).toEqual([EP, EP, 'ep-2'])
  })
})

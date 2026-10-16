// Spec #90 "Client write cadence" + "Write ordering" — a framework-free
// writer, driven by the player's engine events and tested with fake timers.
//
// One session per track start: a random session_id and a seq that grows
// with every write, so the server can order a tab's writes without trusting
// its clock. Rules:
// - ticks go out at most every 15 s of playback, seek writes are debounced
//   1 s, pause/ended/stop flush immediately;
// - a session started with `hold` (a resume or restore seek still pending)
//   writes nothing until that seek lands, so the engine's 0:00 before
//   metadata can never overwrite a stored position;
// - writes to one episode go out in order (a per-episode promise chain), so
//   a manual mark can never be overtaken by a tick already in flight;
// - keepalive writes (page hide) leave synchronously — the document is going
//   away — and rely on seq for ordering;
// - the latest unacknowledged write stays pending (mirrored into the tab
//   snapshot) and is superseded by the next write; failures never touch
//   playback and warn once per failure streak.
import type { SendResult } from '../../api/listening'
import type { ListeningEvent, ProgressAck, ProgressBody } from '../../api/types'
import type { PendingWrite } from './sessionSnapshot'

export const TICK_INTERVAL_MS = 15_000
export const SEEK_DEBOUNCE_MS = 1_000

export interface Sample {
  episodeId: string
  // Logical (transcript-timeline) seconds.
  position: number
  duration: number | null
  playing: boolean
}

export type FlushEvent = Extract<ListeningEvent, 'tick' | 'pause' | 'seek' | 'ended' | 'stop'>
export type MarkEvent = Extract<ListeningEvent, 'mark_played' | 'mark_unplayed'>

export interface ProgressWriterOptions {
  send: (episodeId: string, body: ProgressBody, options: { keepalive: boolean }) => Promise<SendResult>
  sample: () => Sample | null
  onAck: (episodeId: string, ack: ProgressAck, event: ListeningEvent) => void
  onNotFound: (episodeId: string) => void
  onPendingChange: (pending: PendingWrite | null) => void
  newId?: () => string
  now?: () => number
  tickMs?: number
  seekDebounceMs?: number
}

interface Session {
  episodeId: string
  id: string
  seq: number
  hold: boolean
  // Where a held session is headed (logical seconds) — what the snapshot
  // and a Stop record while the engine still reports 0:00. Null while the
  // target is still being looked up.
  heldPosition: number | null
}

export function randomId(): string {
  try {
    if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') return crypto.randomUUID()
  } catch {
    // Insecure context — fall through.
  }
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 12)}`
}

export class ProgressWriter {
  private session: Session | null = null
  private lastWriteAt = 0
  private seekTimer: ReturnType<typeof setTimeout> | null = null
  private pending: PendingWrite | null = null
  private chains = new Map<string, Promise<unknown>>()
  private failing = false
  private readonly opts: Required<Omit<ProgressWriterOptions, 'newId' | 'now'>> & {
    newId: () => string
    now: () => number
  }

  constructor(options: ProgressWriterOptions) {
    this.opts = {
      tickMs: TICK_INTERVAL_MS,
      seekDebounceMs: SEEK_DEBOUNCE_MS,
      newId: randomId,
      now: () => Date.now(),
      ...options,
    }
  }

  /**
   * `heldAt` (logical seconds) holds the session until that seek lands;
   * `hold` alone holds it while the target is still unknown.
   */
  startSession(episodeId: string, { heldAt, hold = false }: { heldAt?: number; hold?: boolean } = {}): void {
    this.cancelSeek()
    this.session = {
      episodeId,
      id: this.opts.newId(),
      seq: 0,
      hold: hold || heldAt !== undefined,
      heldPosition: heldAt ?? null,
    }
    this.lastWriteAt = this.opts.now()
  }

  closeSession(): void {
    this.cancelSeek()
    this.session = null
  }

  hasSession(episodeId?: string): boolean {
    return this.session !== null && (episodeId === undefined || this.session.episodeId === episodeId)
  }

  // The held target while a hold is active and the target is known, else null.
  heldPosition(): number | null {
    return this.session?.hold ? this.session.heldPosition : null
  }

  // A late-learned resume target for a held session.
  retarget(episodeId: string, logicalPosition: number): void {
    if (this.session?.episodeId === episodeId && this.session.hold) this.session.heldPosition = logicalPosition
  }

  releaseHold(episodeId: string): void {
    if (this.session?.episodeId === episodeId) this.session.hold = false
  }

  getPending(): PendingWrite | null {
    return this.pending
  }

  // The first `seeked` of a held session is the resume/restore seek
  // landing: it releases the hold and is not a user seek. Later ones are,
  // and write (debounced) — also while paused, since a seek is an action.
  onSeeked(): void {
    if (!this.session) return
    if (this.session.hold) {
      this.session.hold = false
      return
    }
    this.cancelSeek()
    this.seekTimer = setTimeout(() => {
      this.seekTimer = null
      this.flush('seek')
    }, this.opts.seekDebounceMs)
  }

  tick(): void {
    const sample = this.sessionSample()
    if (!sample || !sample.playing) return
    if (this.opts.now() - this.lastWriteAt < this.opts.tickMs) return
    this.write('tick', sample, false)
  }

  /**
   * Write now. `evenIfHeld` lets a Stop during a hold (a restored bar whose
   * seek never landed) still record the intended position.
   */
  flush(event: FlushEvent, { keepalive = false, evenIfHeld = false }: { keepalive?: boolean; evenIfHeld?: boolean } = {}): void {
    if (event !== 'seek') this.cancelSeek()
    const session = this.session
    if (!session) return
    let sample: Sample | null
    if (session.hold) {
      // An unknown target is never written: 0:00 would erase the position.
      if (!evenIfHeld || session.heldPosition === null) return
      const live = this.opts.sample()
      if (!live || live.episodeId !== session.episodeId) return
      sample = { ...live, position: session.heldPosition }
    } else {
      sample = this.sessionSample()
    }
    if (sample) this.write(event, sample, keepalive)
  }

  /**
   * A manual Mark as played / unplayed. Closes the episode's session (so a
   * late tick from it cannot undo the mark), drops its pending write, and
   * goes out behind any write to that episode already in flight with a
   * one-off session id.
   */
  mark(episodeId: string, event: MarkEvent): Promise<SendResult> {
    if (this.session?.episodeId === episodeId) this.closeSession()
    if (this.pending?.episodeId === episodeId) this.setPending(null)
    return this.dispatch(
      {
        episodeId,
        body: {
          event,
          position_seconds: 0,
          duration_seconds: null,
          session_id: `mark-${this.opts.newId()}`,
          seq: 0,
        },
      },
      false
    )
  }

  // Re-send a write left unacknowledged by a previous document (tab snapshot).
  adoptPending(pending: PendingWrite): void {
    this.setPending(pending)
    void this.dispatch(pending, false)
  }

  dispose(): void {
    this.cancelSeek()
  }

  private sessionSample(): Sample | null {
    const session = this.session
    if (!session || session.hold) return null
    const sample = this.opts.sample()
    // A sample for another track (the engine mid-switch) is never written
    // under this session.
    if (!sample || sample.episodeId !== session.episodeId) return null
    return sample
  }

  private write(event: FlushEvent, sample: Sample, keepalive: boolean): void {
    const session = this.session
    if (!session) return
    session.seq += 1
    const write: PendingWrite = {
      episodeId: sample.episodeId,
      body: {
        event,
        position_seconds: Math.max(0, sample.position),
        duration_seconds: sample.duration,
        session_id: session.id,
        seq: session.seq,
      },
    }
    this.lastWriteAt = this.opts.now()
    // An unacknowledged write for another episode (the outgoing track) is
    // not superseded by this one — send it first.
    const prior = this.pending
    if (prior && prior.episodeId !== write.episodeId) void this.dispatch(prior, false)
    this.setPending(write)
    void this.dispatch(write, keepalive)
  }

  private dispatch(write: PendingWrite, keepalive: boolean): Promise<SendResult> {
    const run = () => this.opts.send(write.episodeId, write.body, { keepalive })
    const request = keepalive ? run() : this.enqueue(write.episodeId, run)
    return request.then((result) => {
      this.settle(write, result)
      return result
    })
  }

  private enqueue<T>(episodeId: string, run: () => Promise<T>): Promise<T> {
    const previous = this.chains.get(episodeId) ?? Promise.resolve()
    const next = previous.then(run, run)
    const tail = next.catch(() => undefined)
    this.chains.set(episodeId, tail)
    void tail.then(() => {
      if (this.chains.get(episodeId) === tail) this.chains.delete(episodeId)
    })
    return next
  }

  private settle(write: PendingWrite, result: SendResult): void {
    if (result.kind === 'ok') {
      this.failing = false
      if (this.pending === write) this.setPending(null)
      this.opts.onAck(write.episodeId, result.ack, write.body.event)
      return
    }
    if (result.kind === 'not_found') {
      if (this.pending?.episodeId === write.episodeId) this.setPending(null)
      this.opts.onNotFound(write.episodeId)
      return
    }
    if (!this.failing) {
      this.failing = true
      console.warn('[listening] Progress write failed; the latest position will be re-sent', {
        episodeId: write.episodeId,
        event: write.body.event,
      })
    }
  }

  private setPending(pending: PendingWrite | null): void {
    this.pending = pending
    this.opts.onPendingChange(pending)
  }

  private cancelSeek(): void {
    if (this.seekTimer !== null) {
      clearTimeout(this.seekTimer)
      this.seekTimer = null
    }
  }
}

// Spec #90 "Client write cadence" + "Write ordering" — a framework-free
// writer, driven by the player's engine events and tested with fake timers.
//
// One session per track start: a random session_id and a seq that grows
// with every write, so the server can order a tab's writes without trusting
// its clock. Rules:
// - ticks go out at most every 15 s of playback, seek writes are debounced
//   1 s, pause/ended flush immediately;
// - a held session (a resume or restore seek still pending) writes nothing
//   until that seek lands, so the engine's 0:00 before metadata can never
//   overwrite a stored position. Its target is what a Stop and the tab
//   snapshot record meanwhile; a target still being looked up is unknown,
//   and an unknown target is never written;
// - writes to one episode go out in order (a per-episode promise chain), so
//   a manual mark can never be overtaken by a tick already in flight;
// - keepalive writes (page hide) leave synchronously — the document is going
//   away — but still join their episode's chain, so a later write (a mark)
//   waits for them;
// - leaving a track records it only when this tab is playing it: a paused
//   tab never writes, so it cannot overwrite newer progress from another
//   device;
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

type WriteEvent = Extract<ListeningEvent, 'tick' | 'pause' | 'seek' | 'ended' | 'stop'>
export type FlushEvent = Exclude<WriteEvent, 'stop'>
export type MarkEvent = Extract<ListeningEvent, 'mark_played' | 'mark_unplayed'>

/** A held session's target, in logical seconds; null while it is being looked up. */
export interface Hold {
  target: number | null
}

export interface ProgressWriterOptions {
  send: (episodeId: string, body: ProgressBody, options: { keepalive: boolean }) => Promise<SendResult>
  sample: () => Sample | null
  onAck: (episodeId: string, ack: ProgressAck, event: ListeningEvent) => void
  onNotFound: (episodeId: string) => void
  onPendingChange: (pending: PendingWrite | null) => void
  // Test seams.
  newId?: () => string
  now?: () => number
}

interface Session {
  episodeId: string
  id: string
  seq: number
  held: Hold | null
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
  private readonly opts: Required<ProgressWriterOptions>

  constructor(options: ProgressWriterOptions) {
    this.opts = { newId: randomId, now: () => Date.now(), ...options }
  }

  startSession(episodeId: string, hold: Hold | null = null): void {
    this.cancelSeek()
    this.session = { episodeId, id: this.opts.newId(), seq: 0, held: hold }
    this.lastWriteAt = this.opts.now()
  }

  closeSession(): void {
    this.cancelSeek()
    this.session = null
  }

  /**
   * Leaving the track (another one is starting): record its position only
   * when this tab is playing it, or a seek write is still debounced, then
   * close the session.
   */
  endSession(): void {
    const sample = this.sessionSample()
    if (sample && (sample.playing || this.seekTimer !== null)) {
      this.write(sample.playing ? 'pause' : 'seek', sample, false)
    }
    this.closeSession()
  }

  hasSession(episodeId?: string): boolean {
    return this.session !== null && (episodeId === undefined || this.session.episodeId === episodeId)
  }

  /** The held target while a hold is active and its target is known, else null. */
  heldTarget(): number | null {
    return this.session?.held?.target ?? null
  }

  // A late-learned resume target for a held session.
  retarget(episodeId: string, target: number): void {
    if (this.session?.episodeId === episodeId && this.session.held) this.session.held.target = target
  }

  releaseHold(episodeId: string): void {
    if (this.session?.episodeId === episodeId) this.session.held = null
  }

  getPending(): PendingWrite | null {
    return this.pending
  }

  // The first `seeked` of a held session is the resume/restore seek
  // landing: it releases the hold and is not a user seek. Later ones are,
  // and write (debounced) — also while paused, since a seek is an action.
  onSeeked(): void {
    if (!this.session) return
    if (this.session.held) {
      this.session.held = null
      return
    }
    this.cancelSeek()
    this.seekTimer = setTimeout(() => {
      this.seekTimer = null
      this.flush('seek')
    }, SEEK_DEBOUNCE_MS)
  }

  tick(): void {
    const sample = this.sessionSample()
    if (!sample || !sample.playing) return
    if (this.opts.now() - this.lastWriteAt < TICK_INTERVAL_MS) return
    this.write('tick', sample, false)
  }

  flush(event: FlushEvent, { keepalive = false }: { keepalive?: boolean } = {}): void {
    if (event !== 'seek') this.cancelSeek()
    const sample = this.sessionSample()
    if (sample) this.write(event, sample, keepalive)
  }

  /**
   * Stop: record the position (a held session's known target — a restored
   * bar whose seek never landed — else the live one) and close the session.
   */
  stop(): void {
    const session = this.session
    if (session) {
      const live = this.opts.sample()
      const target = session.held ? session.held.target : live?.position
      if (live && live.episodeId === session.episodeId && target != null) {
        this.write('stop', { ...live, position: target }, false)
      }
    }
    this.closeSession()
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

  private sessionSample(): Sample | null {
    const session = this.session
    if (!session || session.held) return null
    const sample = this.opts.sample()
    // A sample for another track (the engine mid-switch) is never written
    // under this session.
    if (!sample || sample.episodeId !== session.episodeId) return null
    return sample
  }

  private write(event: WriteEvent, sample: Sample, keepalive: boolean): void {
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
    return this.enqueue(write.episodeId, run, keepalive).then((result) => {
      this.settle(write, result)
      return result
    })
  }

  /**
   * Chain a write behind the episode's in-flight ones. `immediate` (keepalive)
   * leaves now — the document is going away — yet still joins the chain, so
   * whatever comes next waits for it too.
   */
  private enqueue<T>(episodeId: string, run: () => Promise<T>, immediate: boolean): Promise<T> {
    const previous = this.chains.get(episodeId) ?? Promise.resolve()
    const next = immediate ? run() : previous.then(run, run)
    const tail: Promise<unknown> = Promise.allSettled([previous, next])
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

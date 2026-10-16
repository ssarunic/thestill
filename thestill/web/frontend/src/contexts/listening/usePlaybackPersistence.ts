// Spec #90 — the player's persistence layer: the one React hook that wires
// the framework-free pieces (ProgressWriter, tab snapshot, position cache,
// resume/restore rules) to PlayerProvider through a small port. The
// provider calls it once and forwards engine events; everything about
// listening progress lives here, not in PlayerContext.
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { getLatestListening, getListening, putListening } from '../../api/listening'
import type { ListeningSummary } from '../../api/types'
import { useOptionalAuth } from '../AuthContext'
import type { PlayerTrack, PlayOptions, RenditionKind } from '../PlayerContext'
import { buildPlayerTrack } from '../../utils/playerTrack'
import { listeningCache, listeningWrites } from './listeningCache'
import { ProgressWriter, type Hold, type Sample } from './progressWriter'
import { planRestore } from './restore'
import { resolveResume, resumablePosition } from './resume'
import { LOCAL_USER_KEY, clearSnapshot, readSnapshot, writeSnapshot, type SessionSnapshot } from './sessionSnapshot'

/** What the persistence layer needs from the player — implemented over its refs. */
export interface PlayerPort {
  getTrack(): PlayerTrack | null
  // Logical (transcript-timeline) seconds.
  getLogicalPosition(): number
  getLogicalDuration(): number | null
  isPlaying(): boolean
  isEnded(): boolean
  getRendition(): RenditionKind
  // Load a track paused (or playing, when the browser allows) at a logical
  // position on the native engine. No-op once a track is loaded.
  restore(track: PlayerTrack, options: { logicalPosition: number; rendition?: RenditionKind; autoplay: boolean }): void
  // Apply a position learned after the source was assigned. False when it
  // can no longer apply (track changed, user already seeked).
  applyLateSeek(episodeId: string, logicalPosition: number): boolean
  // Pause and rewind the current track to the top.
  rewindToStart(): void
}

export type PersistedEngineEvent = 'play' | 'pause' | 'timeupdate' | 'seeked' | 'ended'

export interface PlaybackPersistence {
  onEngineEvent(event: PersistedEngineEvent): void
  /**
   * A new track is about to load (play() or playYouTube()). Flushes the
   * outgoing track, abandons any pending restore, opens the new session and
   * returns the engine-time seek for the new source (null = top).
   */
  startTrack(next: PlayerTrack, options: PlayOptions | undefined, offset: number): number | null
  // Any other user playback action (resume, same-episode play): marks the
  // restore as abandoned if it has not landed yet.
  noteUserAction(): void
  // Stop: writes `stop` (hides the episode from restore) and clears the tab snapshot.
  onStop(): void
  markListened(episodeId: string, played: boolean): Promise<void>
}

/**
 * The tab snapshot, owned by one user: written only while someone is
 * signed in, read back only by the same user. A tab shared across a logout
 * and another sign-in never restores — or re-sends — the previous user's
 * session.
 */
class SnapshotMirror {
  private writer: ProgressWriter | null = null

  constructor(
    private readonly port: PlayerPort,
    private userKey: string | null,
  ) {}

  attach(writer: ProgressWriter): void {
    this.writer = writer
  }

  setUser(userKey: string | null): void {
    this.userKey = userKey
  }

  read(): SessionSnapshot | null {
    return this.userKey ? readSnapshot(this.userKey) : null
  }

  save(): void {
    const track = this.port.getTrack()
    if (!this.userKey || !track) return
    writeSnapshot(this.userKey, {
      track,
      logicalPosition: this.writer?.heldTarget() ?? this.port.getLogicalPosition(),
      wasPlaying: this.port.isPlaying(),
      rendition: this.port.getRendition(),
      pending: this.writer?.getPending() ?? null,
      savedAt: Date.now(),
    })
  }
}

/** `port` must be stable (memoized over refs): it is captured once. */
export function usePlaybackPersistence(port: PlayerPort): PlaybackPersistence {
  const auth = useOptionalAuth()
  // Without an AuthProvider (isolated tests) there is nobody to wait for.
  const authReady = auth ? !auth.isLoading && auth.user !== null : true
  const userKey = auth ? (auth.user?.id ?? null) : LOCAL_USER_KEY

  const userActedRef = useRef(false)

  const [{ writer, mirror }] = useState(() => {
    const snapshots = new SnapshotMirror(port, userKey)
    const created = new ProgressWriter({
      send: putListening,
      sample: (): Sample | null => {
        const track = port.getTrack()
        if (!track) return null
        return {
          episodeId: track.episodeId,
          position: port.getLogicalPosition(),
          duration: port.getLogicalDuration(),
          playing: port.isPlaying(),
        }
      },
      onAck: (episodeId, ack, event) => {
        listeningCache.seed(episodeId, ack.listening)
        listeningWrites.emit({ episodeId, event, completed: ack.completed })
      },
      onNotFound: (episodeId) => {
        if (snapshots.read()?.track.episodeId === episodeId) clearSnapshot()
      },
      // Mirror before the network call settles, so a reload racing a
      // failed write still has the position.
      onPendingChange: () => snapshots.save(),
    })
    snapshots.attach(created)
    return { writer: created, mirror: snapshots }
  })

  useEffect(() => () => writer.closeSession(), [writer])

  // A different user signed in (or signed out): neither the cache nor the
  // tab snapshot is theirs.
  const lastUserKeyRef = useRef(userKey)
  useEffect(() => {
    mirror.setUser(userKey)
    const previous = lastUserKeyRef.current
    lastUserKeyRef.current = userKey
    if (previous !== null && previous !== userKey) {
      listeningCache.clear()
      clearSnapshot()
    }
  }, [mirror, userKey])

  // Page hide: a keepalive write outlives the document. `pagehide` is the
  // document going away (a pause); `visibilitychange` to hidden is often a
  // backgrounded tab that keeps playing (a tick, which never completes).
  useEffect(() => {
    const onPageHide = () => {
      if (port.isPlaying()) writer.flush('pause', { keepalive: true })
      mirror.save()
    }
    const onVisibility = () => {
      if (document.visibilityState !== 'hidden') return
      if (port.isPlaying()) writer.flush('tick', { keepalive: true })
      mirror.save()
    }
    window.addEventListener('pagehide', onPageHide)
    document.addEventListener('visibilitychange', onVisibility)
    return () => {
      window.removeEventListener('pagehide', onPageHide)
      document.removeEventListener('visibilitychange', onVisibility)
    }
  }, [mirror, port, writer])

  // Restore on load: the tab snapshot first (no network), else the server's
  // latest unfinished episode, paused. A user action before it lands wins.
  const restoredRef = useRef(false)
  useEffect(() => {
    if (!authReady || restoredRef.current) return
    restoredRef.current = true
    if (userActedRef.current || port.getTrack()) return

    const begin = (track: PlayerTrack, logicalPosition: number) => {
      writer.startSession(track.episodeId, logicalPosition > 0 ? { target: logicalPosition } : null)
    }

    const plan = planRestore(mirror.read(), Date.now())
    if (plan.kind === 'snapshot') {
      const { snapshot } = plan
      begin(snapshot.track, snapshot.logicalPosition)
      port.restore(snapshot.track, {
        logicalPosition: snapshot.logicalPosition,
        rendition: snapshot.rendition,
        autoplay: plan.autoplay,
      })
      if (snapshot.pending) writer.adoptPending(snapshot.pending)
      return
    }

    const controller = new AbortController()
    let settled = false
    getLatestListening(controller.signal)
      .then((item) => {
        settled = true
        if (!item) return
        listeningCache.seed(item.episode.id, item.listening)
        if (userActedRef.current || port.getTrack()) return
        const track = buildPlayerTrack(item.episode)
        begin(track, item.listening.position_seconds)
        port.restore(track, { logicalPosition: item.listening.position_seconds, autoplay: false })
      })
      .catch(() => {
        // Offline or signed out: restore is best-effort, never an error state.
        settled = true
      })
    return () => {
      controller.abort()
      // StrictMode's dev remount aborts the first fetch; let the second run.
      if (!settled) restoredRef.current = false
    }
  }, [authReady, mirror, port, writer])

  const onEngineEvent = useCallback(
    (event: PersistedEngineEvent) => {
      switch (event) {
        case 'play': {
          // Replay after `ended` or a mark: a fresh session.
          const track = port.getTrack()
          if (track && !writer.hasSession(track.episodeId)) writer.startSession(track.episodeId)
          mirror.save()
          return
        }
        case 'timeupdate':
          writer.tick()
          return
        case 'seeked':
          writer.onSeeked()
          return
        case 'pause':
          // Browsers fire `pause` just before `ended`; the ended write
          // carries the completion on its own.
          if (!port.isEnded()) writer.flush('pause')
          // No session = Stop or a mark just closed it; the teardown's own
          // pause must not resurrect the snapshot Stop cleared.
          if (writer.hasSession()) mirror.save()
          return
        case 'ended':
          writer.flush('ended')
          writer.closeSession()
          return
      }
    },
    [mirror, port, writer]
  )

  const noteUserAction = useCallback(() => {
    userActedRef.current = true
  }, [])

  const startTrack = useCallback(
    (next: PlayerTrack, options: PlayOptions | undefined, offset: number): number | null => {
      userActedRef.current = true
      // The outgoing track's last position, while the port still reports
      // it — only if this tab was playing it (see ProgressWriter.endSession).
      writer.endSession()

      const decision = resolveResume({
        startAt: options?.startAt,
        restart: options?.restart,
        cached: listeningCache.get(next.episodeId),
        offset,
      })
      // Hold writes until the start seek lands (or the late lookup settles):
      // until then the engine reports 0:00, not the position.
      let hold: Hold | null = null
      if (decision.seekTo !== null && decision.seekTo > 0) hold = { target: Math.max(0, decision.seekTo - offset) }
      else if (decision.fetchLate) hold = { target: null }
      writer.startSession(next.episodeId, hold)

      if (decision.fetchLate) {
        // Surfaces without a `listening` payload (search, ⌘K, entities):
        // play() already assigned the source synchronously; the position
        // lands as a late seek, normally before the remote metadata does.
        const episodeId = next.episodeId
        getListening(episodeId)
          .then((summary: ListeningSummary | null) => {
            listeningCache.seed(episodeId, summary)
            if (!writer.hasSession(episodeId)) return
            const position = resumablePosition(summary)
            if (position !== null) writer.retarget(episodeId, position)
            if (position === null || !port.applyLateSeek(episodeId, position)) {
              writer.releaseHold(episodeId)
            }
          })
          .catch(() => writer.releaseHold(episodeId))
      }
      return decision.seekTo
    },
    [port, writer]
  )

  const onStop = useCallback(() => {
    userActedRef.current = true
    writer.stop()
    clearSnapshot()
  }, [writer])

  const markListened = useCallback(
    async (episodeId: string, played: boolean) => {
      const isCurrent = port.getTrack()?.episodeId === episodeId
      // The mark closes the episode's session so no late tick can undo it.
      const result = writer.mark(episodeId, played ? 'mark_played' : 'mark_unplayed')
      if (isCurrent) {
        // Played: rewind so the bar matches; the next play opens a session.
        // Unplayed: listening may carry on, so it gets a fresh session now
        // (queued behind the mark) instead of going unsaved until a pause.
        if (played) port.rewindToStart()
        else writer.startSession(episodeId)
      }
      const outcome = await result
      if (outcome.kind !== 'ok') throw new Error('Could not update listening progress')
    },
    [port, writer]
  )

  return useMemo(
    () => ({ onEngineEvent, startTrack, noteUserAction, onStop, markListened }),
    [onEngineEvent, startTrack, noteUserAction, onStop, markListened]
  )
}

# Continue Listening

> **Status:** 📝 Draft
> **Created:** 2026-09-30
> **Updated:** 2026-09-30
> **Priority:** Medium
> **Author:** Product & Engineering
> **Related:** [#61 unified-av-playback-session](61-unified-av-playback-session.md), [#62 youtube-video-rendition](62-youtube-video-rendition.md), [#64 legacy-account-claim](64-legacy-account-claim.md), [#71 player-shell-layer](71-player-shell-layer.md), [#72 now-playing-sheet](72-now-playing-sheet.md), [#29 per-user-inbox-fanout](29-per-user-inbox-fanout.md)

---

## Executive Summary

The player forgets everything when the page goes away. A hard refresh
(Cmd-R), a closed tab, a new tab or a second device all bring back an
empty shell: no mini player, and the episode starts again at 0:00 the next
time it is played. For two-hour interview shows that means scrubbing to
find your place, every time.

This spec stores one listening position per user and episode on the
server, and uses it in three places:

1. **Resume on play.** Starting an episode you have part-listened to
   continues where you stopped. An explicit start point (a transcript
   segment, a summary citation) still wins.
2. **Restore on load.** Reloading the tab brings the mini player back on
   the same episode and position, and keeps playing when the browser
   allows it. A new tab or another device brings the bar back paused on
   the most recent unfinished episode, one tap from continuing, however
   long ago it was.
3. **Show progress.** Inbox rows and the episode page show how much is
   left or that an episode was played, and an optional strip on `/inbox`
   lists the episodes you are in the middle of. Any episode can be marked
   played or unplayed by hand.

Browsers do not let a freshly loaded page start audio without a user
gesture. Safari refuses almost always; Chrome allows it on sites with high
media engagement. The spec treats "paused at the right second, one tap to
continue" as the guaranteed outcome and automatic playback after a reload
as a best-effort bonus, never as an error when refused.

## Problem

1. **Player state lives only in memory.** `PlayerProvider` keeps the
   track, position, rendition and engine in React state and refs
   ([PlayerContext.tsx:194](../thestill/web/frontend/src/contexts/PlayerContext.tsx#L194)).
   The only persisted player setting is the speed preference
   ([playbackRate.ts](../thestill/web/frontend/src/utils/playbackRate.ts),
   spec #72 §5). Any document reload discards the session.
2. **Every play of a new episode starts at zero.** `play()` without
   `startAt` assigns the source with no seek
   ([PlayerContext.tsx:365](../thestill/web/frontend/src/contexts/PlayerContext.tsx#L365)).
   Nothing records where a previous session stopped.
3. **Finishing an episode leaves no trace.** `onEnded` resets
   `currentTime` to 0
   ([PlayerContext.tsx:299](../thestill/web/frontend/src/contexts/PlayerContext.tsx#L299));
   there is no "played" state anywhere, so the inbox cannot distinguish an
   episode you listened to from one you never started.
4. **A local fix would stay on one device.** Even a browser snapshot would
   only help the same browser. Listening on the phone during a commute and
   continuing on the laptop needs the position on the server.

## Goals

- Resume any part-listened episode at its last position, from every
  surface that can start playback (reader, inbox, search, ⌘K, entities).
- Survive a same-tab reload with the same episode, position, rendition
  and, where the browser permits, playing state.
- Carry the position across tabs and devices for the same user.
- Record completion, so a finished episode restarts from the beginning
  and surfaces can show it as played, and let the user correct it by hand.

## Non-Goals

- **Live handoff between devices.** No push channel. Two devices playing
  at once simply overwrite each other's position; the later action wins.
- **Listening history page or stats.** The table supports it later; no UI
  in this spec.
- **Briefing narration audio.** Briefings keep their own `listened_at`
  ([#36](36-per-user-digest-from-inbox.md)); this spec covers episodes only.
- **Restoring the YouTube engine.** A restored session always starts on
  the native engine (see [Restore on load](#restore-on-load)).
- **MCP exposure.** No tool reads or writes listening progress yet.
- **Removing played episodes from the inbox.** Finishing or marking an
  episode played never removes, hides or dismisses its inbox row, now or
  as a setting later (unlike Spotify's "Remove played episodes"). The
  inbox only ever changes state; leaving it is the user's own action.
- **Offline playback or a service worker.**

## Design

### Position timeline

Positions are stored on the **logical (transcript) timeline**: engine time
minus the active asset's `timeline_offset`
([PlayerContext.tsx:207](../thestill/web/frontend/src/contexts/PlayerContext.tsx#L207),
spec #61 §4). That keeps one position valid across the audio and video
renditions of the same episode, whose offsets differ. When the player
resumes, it adds the chosen asset's offset back before passing the value to
the engine, the same conversion the rendition switch already does. The
YouTube engine has no offset mapping (spec #62 §8), so positions written
while it is active are stored as-is and are best-effort by design.

### Data model

One row per user and episode:

```sql
CREATE TABLE IF NOT EXISTS user_episode_progress (
    user_id          uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    episode_id       uuid NOT NULL REFERENCES episodes(id) ON DELETE CASCADE,
    position_seconds double precision NOT NULL CHECK (position_seconds >= 0),
    duration_seconds double precision NULL CHECK (duration_seconds IS NULL OR duration_seconds > 0),
    completed_at     timestamptz NULL,
    stopped_at       timestamptz NULL,
    session_id       text NOT NULL,
    seq              bigint NOT NULL,
    updated_at       timestamptz NOT NULL,
    PRIMARY KEY (user_id, episode_id)
);
CREATE INDEX IF NOT EXISTS idx_progress_user_updated
    ON user_episode_progress(user_id, updated_at DESC);
```

- `duration_seconds` is the engine-reported duration at write time, so
  surfaces can render "32 min left" without trusting the feed's
  `episodes.duration`, which is often missing or rounded.
- `completed_at` is set when the episode finishes or is marked played
  (see [Completion](#completion)), and cleared by the next non-completing
  write or by Mark as unplayed.
- `stopped_at` records an explicit Stop; it keeps the position but removes
  the row from the restore candidates (see [Restore on load](#restore-on-load)).
- `session_id` + `seq` order writes without trusting client clocks (see
  [Write ordering](#write-ordering)).
- `updated_at` is server time, ISO-8601 with `+00:00` on SQLite.

The DDL lives in the usual three places: `postgres_schema.SCHEMA_SQL`, the
SQLite migration block in `sqlite_podcast_repository.py`, and Alembic
revision `0014`. A repository pair
(`ListeningProgressRepository`, SQLite + Postgres) owns the table; a
`ListeningProgressService` owns the rules below.

The legacy local-account claim (spec #64,
[legacy_claim_repository.py](../thestill/repositories/legacy_claim_repository.py))
moves `user_episode_progress` rows with the rest of the per-user data. On a
collision the row with the later `updated_at` wins.

### API

All routes sit under `/api/me/listening`, behind `require_session`, and
resolve the user the same way as `/api/inbox` (the default user in
single-user mode).

**`PUT /api/me/listening/{episode_id}`** upserts the position.

```json
{
  "position_seconds": 1934.2,
  "duration_seconds": 7412.0,
  "session_id": "b1f0c6…",
  "seq": 41,
  "event": "tick"
}
```

`event` is one of `tick`, `pause`, `seek`, `ended`, `stop`, `mark_played`,
`mark_unplayed`. The two `mark_*` events come from the manual action (see
[Mark as played](#mark-as-played)) and ignore `position_seconds`. The response is
`{"accepted": true|false, "completed": bool}`; `accepted: false` means the
write was older than the stored one and was ignored. The route returns 404
for an unknown episode and 400 for a non-finite or negative position. A
position beyond `duration_seconds + 5` is clamped to the duration.

**`GET /api/me/listening/{episode_id}`** returns the row's
`{position_seconds, duration_seconds, completed_at, updated_at}`, or
`{"item": null}` when the user has never played the episode. Surfaces that
cannot carry `listening` in their payload use it (see
[Resume on play](#resume-on-play)).

**`GET /api/me/listening/latest`** returns the restore candidate: the
user's most recently updated row that is not completed and not stopped
since its last update, however old, with enough episode data to build a `PlayerTrack` (ids, slugs, title,
podcast title, artwork, `audio_url`, `playback` manifest, duration). It
returns `{"item": null}` when there is none.

**`GET /api/me/listening?state=in_progress&limit=`** lists unfinished rows
newest first for the Continue listening strip (Phase 3).

**Read payloads.** Inbox items (`GET /api/inbox`, `GET /api/inbox/{id}`)
and the episode detail response
([api_podcasts.py:314](../thestill/web/routes/api_podcasts.py#L314)) gain
`listening: {position_seconds, duration_seconds, completed_at, updated_at} | null`
for the current user. The inbox query adds one `LEFT JOIN` on the primary
key; no new index is needed.

### Write ordering

Each tab generates a random `session_id` when the player starts a track and
increments `seq` on every write. The service accepts a write when either:

- the `session_id` differs from the stored one (a different tab or device,
  so the latest action wins), or
- the `session_id` matches and `seq` is greater than the stored `seq`.

This prevents the only realistic reordering: a keepalive flush sent on
`pagehide` arriving after a later tick from the same tab. It avoids
comparing timestamps from clocks on different devices.

### Client write cadence

A small `useListeningProgressWriter` inside `PlayerProvider` writes the
logical position:

- every 15 s of playback (wall clock, throttled off `timeupdate`);
- on pause, on seek end (debounced 1 s), on Stop, and on `ended`;
- before a track change, for the outgoing episode;
- on `pagehide` and on `visibilitychange` to `hidden`, with
  `fetch(…, {keepalive: true})` so the write outlives the document.

A paused tab never writes, so a forgotten tab on another device cannot
overwrite newer progress. Writes are fire-and-forget: a failure never
touches playback. The latest unsent write stays in memory and in the tab
snapshot (below) and goes out with the next tick or on the next load.

### Tab snapshot

Alongside the server write, the player mirrors the session into
`sessionStorage` (`thestill:player:session`): the `PlayerTrack`, the
logical position, `wasPlaying`, the active rendition, the pending unsent
write and `savedAt`. The mirror is written before the network call, so a
reload that races a failed write still has the position. `sessionStorage`
survives a reload of the same tab and is not shared with new tabs, which
is the scope this state needs: whether *this tab* was playing, and which
rendition it chose, are not facts about the user.

All storage access is wrapped in try/catch; a blocked or empty storage
falls back to the server path.

### Restore on load

When `PlayerProvider` mounts, before any user action:

1. **Same-tab reload.** If a tab snapshot exists and is under 30 minutes
   old, restore from it without waiting for the network: set the track,
   select its rendition on the native engine, and load the source with a
   seek to the stored position plus that asset's offset. Re-send its
   pending write, if any.
2. **Otherwise**, call `GET /api/me/listening/latest`. If it returns an
   item, restore it the same way, paused, on the manifest's default
   rendition.
3. **Resume playing** only in case 1, when `wasPlaying` was true and the
   snapshot is under 2 minutes old (a reload, not a return hours later).
   Call the engine's `play()`; if the browser rejects it
   (`NotAllowedError`), stay paused. This is not an error: `mediaError`
   stays null and the bar shows the ordinary play button.
4. **A user action wins.** If the user starts playback anywhere before the
   restore completes, the restore is abandoned.

Restored sessions always start on the native engine. The YouTube rendition
needs a visible surface and is opt-in per episode (spec #62), so the user
switches back to it from the reader if they want it; the position carries
over best-effort as it does today.

Stop is the "I'm done with the bar" gesture. It writes `event: "stop"`,
which sets `stopped_at` and removes the row from `latest` until the next
play of that episode. Without it, the bar would reappear on every load of
every device indefinitely. There is deliberately no age limit on the
restore candidate: Spotify brings back the last played item on any device
however long ago it was, and an arbitrary cut-off would make the bar
appear or not for reasons the user cannot see. Stop is the one control.

### Resume on play

`play(track)` for a new episode, with no `startAt`, resumes from the
episode's known position unless it is completed. The position comes from
a client cache, keyed by episode id, that is filled from:

- the `listening` field on inbox and episode payloads;
- the player's own writes;
- the `latest` response.

Search results, ⌘K and entity pages do not carry `listening`. For those,
`play()` keeps its synchronous source assignment (Safari only accepts the
initial `play()` inside the click handler) and fetches the position in
parallel from `GET /api/me/listening/{episode_id}`. The native engine
already defers a seek until `loadedmetadata`
([native-engine.ts:88](../thestill/web/frontend/src/contexts/playback-engine/native-engine.ts#L88));
it gains `setPendingSeek(seconds)`, which replaces the pending seek before
metadata arrives, or seeks immediately afterwards if the position is still
under 5 s and the user has not seeked. A same-origin JSON request normally
completes before a remote MP3's metadata, so the late seek is rare.

An explicit `startAt` always wins: transcript segment clicks, summary
citations, entity mentions and the "Play from beginning" action pass one.

### Completion

An episode counts as completed when the engine fires `ended`, or when a
pause, stop or page-hide write lands with no more than 45 s left, the usual
length of an outro ad read. On completion the service sets `completed_at`
and resets `position_seconds` to 0, so the next play starts from the top.

Completing an episode also marks its inbox row read if it was unread, via
the existing `mark_read_if_unread`
([inbox_service.py:382](../thestill/services/inbox_service.py#L382)).
Listening to the end is consuming the episode, just as opening its summary
is. Rows in `saved` or `dismissed` are untouched.

Episodes with no known duration (streams, broken feeds) complete only on
`ended`.

The 45 s threshold is a fixed number, not a share of the duration. No
major player documents its rule (Spotify's 30-second figure is its
play-count definition for creator analytics, not completion), and the
manual toggle below corrects the cases the rule gets wrong.

### Mark as played

Every episode can be marked played or unplayed by hand, as in Spotify and
Apple Podcasts:

- **Mark as played** sends `event: "mark_played"`. It has the same effect
  as finishing: `completed_at` set, position reset to 0, inbox row marked
  read if unread. If the episode is the current track, the player pauses
  and rewinds the bar to 0 so a later tick cannot undo the mark.
- **Mark as unplayed** sends `event: "mark_unplayed"`. It clears
  `completed_at`, leaves the position at 0 and does not touch the inbox
  row; reading state is the inbox's own control.

The manual action writes with its own `session_id` per click, so it always
wins over whatever was stored and is never rejected as out of order.

### Surfaces

- **Mini player.** No new component: a restored session renders the
  existing bar ([#71](71-player-shell-layer.md)) paused at the position.
- **Inbox rows.** A 2 px progress line along the bottom of the artwork and
  "32 min left" in the existing metadata line; "Played" with a check once
  completed. No extra row height (spec #73 density rules). Mark as played /
  unplayed sits in the row's existing actions menu.
- **Episode page.** The primary play button reads "Resume · 32 min left"
  when there is a position, with a quiet "Play from beginning" link next to
  it. A completed episode shows "Played" and the button plays from 0.
  Mark as played / unplayed sits with the page's other episode actions.
- **Continue listening strip (Phase 3).** Above the inbox list, up to three
  unfinished episodes from `GET /api/me/listening?state=in_progress`,
  styled like the Arriving soon strip ([#88](88-import-outcomes-and-arriving-soon.md)).
  Each item resumes in place. Hidden when empty and while `?q=` filters the
  inbox (spec #85).

## Failure Modes

| Failure | Behaviour | Why it is not silent |
| --- | --- | --- |
| Write fails (offline, 5xx, server restart) | Playback continues; the pending write stays in memory and the tab snapshot and is re-sent on the next tick or load | `console.warn` once per failure streak; server errors are logged by the route |
| Late or duplicate write from the same tab | Rejected by `session_id` + `seq`; response `accepted: false` | Logged at debug with both `seq` values |
| Browser refuses automatic playback after reload | Bar restored paused at the position | Expected outcome, documented above; not a `mediaError` |
| Restore target deleted or unknown | `latest` never returns it (cascade); a stale tab snapshot gets a 404 on its re-sent write and is cleared | Route returns 404, client drops the snapshot |
| Stale media URL in the tab snapshot | Existing `mediaError` path shows in the bar | Snapshots older than 30 minutes are ignored |
| Two devices playing the same episode | Last write wins | Documented non-goal |
| Storage blocked (private mode) | Server path only; same-tab reload falls back to `latest` | try/catch around every access |

Listening positions are personal data: rows cascade with the user, routes
never log positions at info level, and no position is sent to analytics.

## Implementation Plan

### Phase 1 — Store and API

- Table in `postgres_schema`, the SQLite migration block and Alembic
  `0014`; repository pair with a shared contract test run against both
  backends (Postgres via `TEST_DATABASE_URL`).
- `ListeningProgressService`: write ordering, clamping, completion,
  mark played / unplayed, mark-read on completion.
- Routes: `PUT /{episode_id}`, `GET /{episode_id}`, `GET /latest`, and
  the `GET ?state=in_progress` list.
- `listening` on inbox items and the episode detail payload.
- Legacy claim moves the table.
- Tests: ordering (same session older `seq` rejected, other session
  accepted), completion threshold, mark played / unplayed (including over
  a row with a higher stored `seq`), stop hides from `latest`, an old row
  is still returned by `latest`, cascade on user and episode delete.

### Phase 2 — Player

- `useListeningProgressWriter`: cadence, keepalive flush on
  `pagehide`/`visibilitychange`, pending-write retry.
- Tab snapshot mirror.
- Restore on mount (snapshot, then `latest`), best-effort autoplay,
  abandon on user action.
- Resume on play from the cache, parallel fetch plus
  `NativeEngine.setPendingSeek` for surfaces without `listening`.
- Marking the current track played pauses and rewinds it.
- Tests in `PlayerContext.test.tsx` with fake timers: 15 s cadence,
  flush on `pagehide`, restore from snapshot without network, autoplay
  rejection leaves the bar paused with no error, explicit `startAt` beats
  a stored position, completed episode starts at 0, logical-to-engine
  offset on a video rendition.
- One Playwright check: play, wait, reload, the bar shows the same
  episode within 2 s of the pre-reload position.

### Phase 3 — Surfaces

- Inbox row progress line, time left, Played, and Mark as played /
  unplayed in the row actions.
- Episode page Resume / Play from beginning / Played / Mark as played.
- Continue listening strip on `/inbox`.

## Known Gaps

- `db_promotion` (the one-time SQLite → Postgres cutover) does not copy
  the new table; a self-hoster promoting after this ships loses positions,
  not episodes.
- Progress written while the YouTube engine is active can drift by the
  length of YouTube's dynamic ad insertion.
- Positions from before a transcript is re-timed (a changed
  `timeline_offset`) are not migrated; the error is bounded by the offset
  change, typically a few seconds.

## Open Questions

None.

## Decision Log

| Date | Decision | Reason |
| --- | --- | --- |
| 2026-09-30 | Server-side per-user row, not only a local snapshot | The local snapshot alone fixes reload but not new tabs or devices, which is the "continue where you left off" promise |
| 2026-09-30 | `sessionStorage` for tab state, not `localStorage` | Whether this tab was playing and which rendition it chose are tab facts; `localStorage` would make every new tab try to resume someone else's session |
| 2026-09-30 | Order writes by `session_id` + `seq`, not client timestamps | Device clocks drift; the only reordering that matters happens within one tab |
| 2026-09-30 | Automatic playback after reload is best-effort | Browser autoplay policy; paused at the right second is the guaranteed outcome |
| 2026-09-30 | Restore always on the native engine | YouTube playback needs a visible surface and is opt-in per episode (spec #62) |
| 2026-09-30 | Stop hides the episode from restore; pause does not | Otherwise the bar comes back on every load after the user dismissed it |
| 2026-09-30 | No age limit on the restore candidate | Spotify restores the last played item on every device with no expiry; Stop already covers "I don't want this back" |
| 2026-09-30 | Finishing marks the inbox row read (unread only) | Listening to the end is consuming the episode; Spotify likewise ties finishing to list tidying (Remove played episodes), and the manual toggle covers mistakes |
| 2026-09-30 | Played episodes are never removed from the inbox | Product rule: the inbox never drops a row on its own; completion only moves unread to read |
| 2026-09-30 | Fixed 45 s completion threshold plus a manual Mark as played / unplayed | No player publishes its rule; a manual correction matters more than tuning the number |

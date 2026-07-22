# YouTube Video Rendition via Iframe Engine

> **Status:** 🚧 Implemented on `feat/62-youtube-rendition` (2026-07-22) — all three increments: observer + dual-backend data layer, manifest `youtube` asset + CSP + engine adapter (`PlaybackEngine` / `NativeEngine` / `YouTubeEngine`), theater/floating hosting, polled+interpolated clock, §7 policy effect. Open items unchanged.
> **Created:** 2026-07-22
> **Updated:** 2026-07-22
> **Author:** Engineering (playback design)
> **Related:** [#61 unified-av-playback-session](61-unified-av-playback-session.md) (this spec is the "YouTube iframe engine" its §6 gated — the engine adapter boundary, presentation state machine, and playback-asset manifest defined there are the substrate), [#39 video-alternate-enclosure-player](39-video-alternate-enclosure-player.md) (**superseded** — its page-scoped embed predates #61's unified session; its YouTube-ID extraction rules and variant-selection notes are folded into §5), [#38 karaoke-word-highlighting](38-karaoke-word-highlighting.md) (constrains the engine adapter's `getCurrentTime()`), [#44 postgres port](44-postgres-database-support.md) (dual-backend contract obligations for the new accessor)

---

## Executive summary

For episodes whose feed publishes an episode-level YouTube link
(`<podcast:alternateEnclosure mime_type="video/youtube">`), let the user
switch playback to the official YouTube embedded player, behind the same
playback session spec #61 built. **Transcription is untouched**: the RSS
audio enclosure remains the only thing the pipeline downloads, downsamples,
and transcribes. YouTube playback is a user-requested *rendition* of the
same logical session.

This resolves spec #61 §6's product/legal gate by construction: what
YouTube's developer policies prohibit is downloading/caching AV content,
separating audio from video, and background playback. The **IFrame Player
API embed is the sanctioned path** — nothing is downloaded, the player is
YouTube's own, and §7 makes background playback structurally impossible.
The pipeline's existing audio download for transcription is unchanged
behavior, orthogonal to playback.

**Accepted tradeoff (explicit product decision):** YouTube inserts its own
ads dynamically, so there is **no stable mapping between transcript time
and YouTube playback time**. Rendition switches and transcript seeks onto
the YouTube engine are best-effort (§8). This is understood and accepted;
drift is a property of the rendition, not a bug.

## 1. Data reality (verified 2026-07-22)

- `episode_alternate_enclosures` exists in both schemas
  ([postgres_schema.py:188](../thestill/repositories/postgres_schema.py#L188),
  Alembic `0001_initial_schema`) and holds **14 rows, all
  `mime_type='video/youtube'`**, across two subscribed shows: The News
  Agents (7) and My Therapist Ghosted Me (7). `source_uri` is
  `https://youtu.be/<id>` form.
- **The observer that wrote those rows never merged.** Commit `eda2e27`
  (extractor, `AlternateEnclosure` model, SQLite accessor, refresh wiring,
  CLI backfill, 741 lines) lives only on the stale branch
  `claude/video-podcast-support-qayfB`, branched from May-11 main. The rows
  date from 2026-05-13 and were carried into Postgres by the one-time
  `db_promotion` cutover. On main there is **no reader, no writer, and no
  accessor in either repository** — the table is dead weight until Phase 0
  re-lands the observer.
- The refresh architecture has since been rebuilt (spec #19 batch writes →
  `save_refresh_batch` accumulators, #42/#49/#60 failure handling, #44
  Postgres port, #61 enclosure MIME handling). The old branch is
  **reference material, not merge material**: reuse its XML extractor and
  tests; rewrite the wiring.

## 2. Scope

### In scope

1. **Phase 0 — re-land the alternate-enclosure observer** on current main:
   extraction on refresh, batch persistence, accessors in BOTH repositories
   (dual-backend contract test), so episode-level YouTube links exist and
   stay fresh.
2. Playback manifest gains a `youtube` asset (§4) when an episode has a
   `video/youtube` alternate enclosure with a valid video ID.
3. A **YouTube iframe engine** behind #61's session interface (§6): one
   stable iframe node in the media layer, positioned over the same theater /
   floating-tile slots, exactly one engine active at a time.
4. Rendition switching UI: the theater menu's existing
   `canSwitchRendition` affordance grows a YouTube option; switching
   carries position best-effort (§8).
5. Compliance-driven presentation policy (§7): when YouTube video is not
   visibly presented, the session switches to the native audio rendition —
   never a hidden-but-playing iframe.

### Non-goals

- **`video/mp4` / HLS alternate enclosures.** The corpus has zero today
  (all 14 rows are `video/youtube`). The manifest shape leaves room; the
  native engine picks them up in a later increment when real data exists.
- **Fuzzy episode↔YouTube matching** (description scraping, channel
  search, title matching). Only publisher-declared alternate enclosures.
  The show-level `top_podcasts.youtube_url` is a directory link, not an
  episode mapping — not used.
- **YouTube-sourced feeds** (`YouTubeMediaSource`): unchanged; those
  episodes flow through the audio pipeline as today. (A YouTube-sourced
  episode trivially knows its own video ID — wiring that into the manifest
  is a cheap follow-up, listed as an open item, not v1.)
- **Downloading/caching any YouTube AV**, quality selection, SponsorBlock,
  captions from YouTube.
- **Media Session / lock-screen integration and native PiP for the
  YouTube engine** — the iframe owns its audio; these stay native-engine
  features. Documented as expectations, not bugs (§7).
- **Karaoke parity.** Word-sync on the YouTube engine is degraded by
  design (§8); v1 ships polling+interpolation and may disable highlighting
  if it proves unusable — the transcript itself always renders.

## 3. Phase 0 — alternate-enclosure observer, rebuilt

Port from `eda2e27` (reference), rewritten for current main:

- **Extractor** (`RSSMediaSource.extract_alternate_enclosures(rss_content)`):
  parse raw XML (defusedxml, same pattern as `extract_transcript_links` —
  feedparser drops repeated child tags), namespace
  `https://podcastindex.org/namespace/1.0`, one entry per
  `<podcast:source>` child inheriting the parent's
  `mime_type`/`height`/`bitrate`/`title`. Keyed by episode GUID like
  transcript links. The old branch's extractor + its 198-line test file
  port nearly verbatim.
- **Persistence**: a new accumulator on the refresh path feeding
  `save_refresh_batch` (the #19 single-transaction pattern —
  follow how `episode_audio_updates` flows through
  [feed_manager.py](../thestill/core/feed_manager.py) `RefreshAttemptResult`).
  Insert with `ON CONFLICT (episode_id, source_uri) DO NOTHING` /
  `INSERT OR IGNORE`; entries vanished from the feed are left in place
  (observational table; a missing tag must never delete history —
  same principle as "a missing enclosure must never blank a stored URL").
- **Accessors in BOTH repositories** (#42 path-drift lesson; spec #60's
  Phase-0 failure mode was exactly a SQLite-only accessor):
  `get_alternate_enclosures(episode_id)` plus a batched
  `get_alternate_enclosures_for_episodes(ids)` for list endpoints if the
  manifest is served there. Covered by the dual-backend contract suite
  ([test_podcast_repository_episodes_contract.py](../tests/integration/test_podcast_repository_episodes_contract.py)).
- **Timestamps**: ISO-8601 `+00:00` strings in raw SQLite SQL, never
  `CURRENT_TIMESTAMP` (the existing table DDL's default is grandfathered;
  new writes pass explicit values).

## 4. Manifest extension

`build_playback_manifest` ([playback.py](../thestill/services/playback.py))
gains an optional third asset alongside `audio`/`video`:

```jsonc
{
  "kind": "audio",            // unchanged: from the enclosure MIME
  "audio": { ... },           // RSS enclosure asset (unchanged)
  "video": null,              // native video asset (unchanged, #61)
  "youtube": {                // NEW — present iff a valid video/youtube
    "video_id": "6wz3LrdMnVo",//        alternate enclosure exists
    "watch_url": "https://www.youtube.com/watch?v=6wz3LrdMnVo",
    "title": "…"              // alt-enclosure title, may be null
  },
  "poster_url": …,
  "captions_url": null
}
```

- `kind` is **not** changed by the presence of `youtube` — it still
  classifies the enclosure asset. A YouTube rendition is opt-in via the
  toggle, never the default engine.
- **Video-ID extraction** (from #39): accept `youtu.be/<id>`,
  `youtube.com/watch?v=<id>`, `youtube.com/embed/<id>`; validate
  `^[A-Za-z0-9_-]{11}$`. Feed data is untrusted input (#42
  unsanitized-input lesson): an unparseable/invalid URI ⇒ omit the asset
  and log a warning — never emit a malformed ID into a frontend iframe URL.
- Multiple `video/youtube` rows for one episode: pick `is_default=1`
  first, else first by insertion order (deterministic; matches #39's
  "pick the best variant deterministically").
- Signature becomes `build_playback_manifest(episode, alternate_enclosures=None)`;
  both call sites (`api_episodes.py`, `api_podcasts.py`) pass the fetched
  rows. Frontend `PlaybackManifest` type extends accordingly.

## 5. Engine adapter (#61 invariant 1, realized)

`PlayerContext` gains `activeEngine: 'native' | 'youtube'`, with **exactly
one engine active**:

- **Stable iframe node**, created lazily on first YouTube playback and
  then never destroyed while a YouTube rendition is active — it lives in
  the same global media layer as the `<video>` node and is positioned over
  the registered slot identically (theater / floating tile). The `<video>`
  node's stable-element invariant is untouched; the two nodes coexist,
  at most one visible/audible.
- **IFrame Player API**: script loaded on demand
  (`https://www.youtube.com/iframe_api`), player constructed with
  `playsinline: 1`, `origin` set, YouTube's **default controls kept** (v1
  — no chrome-stripping; our custom transport is a proxy, not a
  replacement). All calls feature-guarded; API load failure surfaces
  through the existing `mediaError` channel.
- **Transport mapping**: `play/pause/seek/skip/setRate` proxy to
  `playVideo/pauseVideo/seekTo/setPlaybackRate`; `onStateChange` drives
  `isPlaying/isLoading` and ended; `getCurrentTime()` serves karaoke via
  ~250 ms polling with linear interpolation between samples, correcting
  after seeks (#61 §6's sketch). The rAF consumers stay engine-agnostic.
- **Engine switches are controlled transitions** (mirror of #61 §5):
  native→YouTube pauses the media element (source retained for instant
  switch-back), constructs/reveals the iframe, `seekTo(logicalTime)`
  best-effort; YouTube→native reads the iframe clock, treats it as logical
  time (accepting ad drift), seeks the native engine, destroys nothing.
  Rate carries across. Play state carries only via user gesture —
  both directions are always user-initiated (autoplay policies).

## 6. Rendition UI

- Theater menu: alongside #61's "Use audio rendition", episodes with a
  `youtube` asset offer **"Play video on YouTube player"** (and back:
  "Use audio rendition"). `canSwitchRendition` is true when ≥2 of
  {audio asset (or legacy `audioUrl`), native video asset, youtube asset}
  exist.
- For `kind: 'audio'` episodes with a `youtube` asset (the entire current
  corpus — News Agents episodes are audio enclosures + YT link), the
  reader shows no theater by default; a lightweight **"Watch video"**
  affordance near the play controls enters the YouTube rendition and
  registers the theater slot. Leaving the rendition returns to plain
  audio presentation.
- Mini player: unchanged for audio; while the YouTube engine is active it
  shows artwork + a "Show video" affordance exactly as for native video
  (returning to the reader/theater), and its play/pause proxies the
  engine adapter.

## 7. Presentation & compliance policy

The #61 presentation state machine is reused with one hard rule added:

> **The YouTube engine may only play while its iframe is visibly
> presented** (theater or floating tile). Any transition that would leave
> it unpresented — closing the floating tile, mobile navigation away from
> the reader, "Hide video" — **switches the session to the native audio
> rendition** instead of hiding a playing iframe.

Rationale: a hidden-but-audible iframe is background playback of separated
audio — both prohibited. This also gives "Hide video" a coherent meaning on
the YouTube engine: it *is* the audio-rendition switch (continuity
best-effort per §8), rather than #61's visual-only toggle.

Consequences, stated as expectations: no lock-screen metadata, no native
PiP button, no background playback on the YouTube rendition; fullscreen is
the iframe's own control (`fs=1`). YouTube serves its own ads inside the
embed — they play; transport proxying during ads follows whatever the
IFrame API allows (typically no-ops).

## 8. Drift and transcript sync

There is deliberately **no `timeline_offset` for the YouTube asset** — a
static offset cannot model dynamic ad insertion.

- **Segment/citation seek onto the YouTube engine**: `seekTo(logical
  seconds)` verbatim, best-effort. With no ads shown, YouTube time ≈
  publisher time for these shows; with ads, the landing point is off by
  the ad time YouTube inserted before that point. Accepted.
- **Karaoke**: driven by the polled+interpolated clock; the per-episode
  `playback_time_offset_seconds` is applied by consumers as today. During
  ads the clock pauses (state `BUFFERING`/ad) — highlighting freezes
  rather than drifting wildly. If real-world behavior is worse than
  "degraded but honest", v1 falls back to disabling word-highlight on
  this engine (transcript still renders and seeks).
- **Rendition switches preserve position best-effort**, not exactly (#61
  invariant 4 is relaxed **for this engine only** — the spec text of #61
  stays authoritative for native↔native switches).

## 9. Testing

- **Phase 0**: extractor unit tests (ported), dual-backend contract tests
  for accessors + refresh-batch persistence (idempotent re-observe,
  malformed-entry isolation), refresh integration (a fixture feed with
  `<podcast:alternateEnclosure>` producing rows on both backends).
- **Manifest**: video-ID extraction table test (valid forms, 11-char
  validation, hostile URIs rejected), default-selection, audio-kind +
  youtube-asset combination.
- **Frontend**: engine adapter with a mocked IFrame API (transport
  proxying, state mapping, polling clock interpolation, controlled
  transitions carrying position/rate); presentation-policy tests (every
  unpresented-iframe path lands on the audio rendition); rendition-menu
  gating.
- **Live rehearsal**: refresh The News Agents (subscribed; feed still
  carries the tag), verify rows refresh and an episode page offers the
  toggle. Scratch servers per the isolation conventions
  (`STORAGE_PATH`, explicit `DATABASE_URL`, `REFRESH_SCHEDULER_ENABLED=false`).

## 10. Increments

1. **Observer + data** (backend only, shippable alone): Phase 0 extractor,
   batch persistence, both-backend accessors, contract tests. Value:
   adoption telemetry resumes after the 2-month gap.
2. **Manifest + toggle + theater embed**: `youtube` manifest asset, engine
   adapter core (play/pause/seek/state), theater hosting, "Watch video" /
   rendition menu entries, §7 policy for the reader page.
3. **Floating tile + karaoke polling + polish**: off-reader floating
   hosting on desktop, polled clock + interpolation for word-sync,
   audio-fallback on tile close / mobile navigation, mediaError surfaces,
   docs + spec #39 marked superseded.

## Open items

- YouTube-sourced feeds (`YouTubeMediaSource`) trivially know their video
  ID — emit a `youtube` manifest asset for them too (needs an
  episode-level column or derivation from `external_id`; separate small
  follow-up).
- Whether to backfill alternate enclosures for existing episodes via a
  one-shot CLI (the old branch had `thestill backfill-alt-enclosures`;
  refresh-driven re-observation may make it unnecessary since the tags
  ride on current feed entries only).
- Native `video/mp4` alternate enclosures as additional **native-engine**
  renditions (the #39 remainder) — wait for real corpus data.

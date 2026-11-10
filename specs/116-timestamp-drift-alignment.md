# Timestamp Drift Alignment: Map the Transcript onto the Listener's Copy

> **Status:** 📝 Draft (2026-11-10)
> **Created:** 2026-11-10
> **Updated:** 2026-11-10
> **Priority:** High. Every timestamp feature (click-to-seek, karaoke,
> summary citations, chapters, clips) lands in the wrong place on any
> episode whose ads changed since transcription. Today the reader only
> warns.
> **Author:** Product & Engineering
> **Related:** [#18 segment-preserving-transcript-cleaning](18-segment-preserving-transcript-cleaning.md)
> (`ad_break` segments, `playback_time_offset_seconds`),
> [#23 transcript-playback-sync](23-transcript-playback-sync.md),
> [#24 word-level-transcript-highlighting](24-word-level-transcript-highlighting.md),
> [#54 summary-segment-citations](54-summary-segment-citations.md),
> [#61 unified-av-playback-session](61-unified-av-playback-session.md)
> (`timeline_offset` per asset), [#97 episode-chapters](97-episode-chapters.md)
> (`timeline` provenance), [#110 transcript-editor](110-transcript-editor.md)
> (Phase 4 waits for this), [#113 hls-video-rendition](113-hls-video-rendition.md)
> (drift banner per rendition)

---

## Overview

Most publishers insert ads at download time. The copy Thestill
transcribed and the copy a listener's browser streams carry different
ads, so every transcript timestamp after the first changed ad break is
off by tens of seconds. Thestill does not keep episode audio and the
reader plays the publisher's URL directly, so there is no shared copy to
fall back on.

Today the reader compares the transcribed duration with the live
duration and shows "Timestamps may have drifted" when they differ by
15 s or more ([EpisodeReader.tsx:950](../thestill/web/frontend/src/components/EpisodeReader.tsx#L950)).
Nothing is corrected.

This spec stores a small **sync track** for each episode at download
time: about 36 KB per hour of anchors taken from the transcribed copy. When a
listener plays the episode, the browser locates those anchors in its own
copy, fits a step function of offsets, and the player maps transcript
time to audio time through it. Where the browser cannot read the audio,
the banner stays as it is today.

## Goals

1. On an episode whose ads changed, a click on a transcript segment, a
   summary citation or a chapter starts playback within 0.5 s of the
   right word.
2. Karaoke highlighting follows the audio across ad breaks, and pauses
   while an ad that is not in the transcript plays.
3. Storage stays under 250 KB per hour of audio (36 KB until the
   perceptual track of Phase 3). No episode audio is retained.
4. Every failure (no CORS, unknown codec, variant mismatch, low
   confidence) falls back to today's banner. A wrong map is never
   applied.
5. Publisher delivery is untouched: the audio still comes from the
   publisher's URL, with the publisher's ads.

## Non-goals

- **Skipping or marking ads for the listener.** The map knows where
  listener-side ads are. Using that to skip them is a policy decision
  for a separate spec.
- **Proxying or re-hosting audio.** It removes drift, and it also removes
  the publisher's ad impressions and download counts.
- **Backfilling episodes transcribed before this ships.** Their original
  copy is gone, so there is nothing to fingerprint. See Phase 5.
- **YouTube and HLS video renditions.** They keep the scalar
  `timeline_offset` of #61 and the banner of #113.
- **Fixing word timings after transcript edits.** That is forced
  alignment, the follow-up named in #110.

## Background findings

Measured on 2026-11-10 against the local catalogue. Scripts were
throwaway; the numbers are the record.

### Enclosure probe (42 reachable episodes, one per show)

Each URL was requested three times with `Origin: https://thestill.me`
and `Range: bytes=0-1`: twice with a Chrome user agent, once with
AppleCoreMedia.

| Check | Result |
|---|---|
| Range requests honoured | 42 of 42 |
| `Access-Control-Allow-Origin` on the final response | 33 of 42 |
| `Access-Control-Allow-Origin` on every redirect hop | 27 of 42 |
| Size differs between the two user agents | 9 of 42 |
| Size differs between two requests with the same user agent | 0 of 42 |
| Size changed for the same user agent 15 minutes later | 0 of 9 |

A browser `fetch()` needs the header on every hop, so 27 of 42 (64%) is
the share the client path can reach today. The failures were all
`anchor.fm` URLs, all `api.substack.com` URLs, and some `podtrac`
chains. None of the six `anchor.fm` episodes changed size between user
agents.

The episodes were at least four months old, so most campaigns had
expired. The share of fresh episodes that differ per listener is higher
than 9 of 42.

### Pair alignment (5 episodes, each downloaded twice)

Copy A used the Chrome user agent, copy B AppleCoreMedia. Both were
fingerprinted with a Haitsma-Kalker sub-band hash (5512 Hz mono, 32 bits
per 11.6 ms frame) and aligned over the whole file.

| Episode | Host chain | A vs B length | What differed |
|---|---|---|---|
| The Rest Is History | pdst.fm → Megaphone | +71 s | post-roll only |
| Today in Focus | Acast | −43 s | mid-roll (−40.7 s), late break (−72.8 s) |
| The News Agents | Captivate | −20 s | pre-roll (+10.0 s), mid-roll |
| The Prof G Pod | podtrac → Megaphone | −10 s | three blocks: −11.4 s, −6.0 s, −10.2 s |
| No Such Thing As A Fish | pdst.fm chain | +29 s | block 10–20 s in (+28.8 s) |

Findings:

- **The offset is constant between splices**, to within one fingerprint
  frame (about 10 ms). Ads never stretch the content.
- **Short anchors locate content.** A 3 s anchor cut to 128 bytes found
  the right position for 99% of content anchors, searching the whole
  file with no window. The misses straddled a splice.
- **Re-encoding is survivable.** Against copy B re-encoded to 48 kbps /
  22 kHz mono the same anchors still matched: bit-error rate at most
  0.10 on true matches, about 0.46 on unrelated audio.
- **A single anchor cannot be trusted.** Some correct anchors had a
  second match elsewhere at a bit-error rate of 0.01, and anchors inside
  copy A's ads matched somewhere in copy B at 0.000. Causes: silence,
  jingles, and the same ad creative in a different slot.
- **Content frames are byte-identical.** Hashing raw MP3 frames, the
  share of copy A's frames found byte-for-byte in copy B was 99.3%,
  92.6%, 95.0%, 81.0% and 100%. That equals the non-ad share of each
  episode. All five hosts splice ads in without re-encoding the episode.
- **Cost.** Decoding a 74-minute MP3 took 11 s and fingerprinting 5 s in
  unoptimised numpy. Frame hashing needs no decode and took under 2 s.

Not tested: Chromaprint (the `fpcalc` binary was not available), AAC
enclosures, and any of this inside a real browser.

## Clarifications

1. **Whose copy is aligned?** The listener's. A server re-download gets
   its own ad set and says nothing about what the browser received.
2. **Why not a few bytes in front of each speaker segment?** Anchors
   placed by segment are unevenly spaced and often fall on silence. A
   whole-episode track of one anchor per second is 36 KB per hour and can locate
   any part of the live file. Segment and word times are then mapped by
   arithmetic.
3. **Why frame hashes first, perceptual fingerprint second?** Frame
   hashes are exact, need no audio decoding, and worked on every tested
   host. They break when a publisher re-encodes or re-uploads. The
   perceptual fingerprint survives that, and costs a decode.

## Design

### 1. The sync track

One binary artefact per episode, written once, never changed:

| Field | Content |
|---|---|
| Header | algorithm version, codec, sample rate, frame count, byte length, duration of the transcribed copy |
| Frame anchors | one per second: frame index (4 bytes) + hash of 4 consecutive compressed frames (6 bytes) |
| Perceptual track (Phase 3) | Haitsma-Kalker sub-fingerprints, every 8th frame, about 155 KB per hour |

Rules for frame anchors:

- An anchor covers 4 consecutive frames (about 104 ms at 44.1 kHz), so a
  single silent frame cannot match.
- An anchor whose hash appears more than once in the transcribed copy is
  dropped at write time. This removes silence and repeated stings.
- If fewer than half the seconds yield a unique anchor, the track is
  written with `frame_anchors: none` and only the perceptual track
  applies.

At 10 bytes per anchor a one-hour episode is 36 KB before compression.

### 2. Pipeline: write the track at download

The download stage has the original file on disk. A new step parses its
MP3 frames, writes the track through `PathManager`
(`sync_tracks/<podcast>/<episode>.sync`), and records the path on the
episode. It runs before `DELETE_AUDIO_AFTER_PROCESSING` removes the file
([task_handlers.py:382](../thestill/core/task_handlers.py#L382)).

- A failure here logs `sync_track_failed` with `episode_id` and does not
  fail the download. The episode simply has no track.
- Non-MP3 enclosures get no frame anchors until Phase 3.
- `thestill reprocess --from transcribe` re-downloads, so it rewrites the
  track from the copy it transcribes. Track and transcript always
  describe the same copy.

Schema: one nullable column `episodes.sync_track_path`, added by an
Alembic migration for both SQLite and Postgres.

### 3. API

`GET /api/episodes/{id}/sync-track` returns the artefact as
`application/octet-stream` with `Cache-Control: immutable`, or 404. It
has the same visibility as the transcript. The episode payload gains
`has_sync_track: boolean` so the client does not probe.

### 4. Client: build the map

All work runs in a Web Worker so playback and scrolling are unaffected.

1. **Gate.** Run only when the episode has a track, the engine is the
   native audio engine, and the playing asset is the RSS audio.
2. **Cheap check.** If the live duration equals the transcribed duration
   within 0.5 s, fetch three 256 KB ranges (start, middle, end) and look
   up their anchors. If all sit at offset 0, the map is the identity and
   the worker stops.
3. **Full pass.** Otherwise `fetch()` the audio URL as a stream with
   `mode: 'cors'`. Parse MP3 frame headers as bytes arrive, keep a
   rolling 4-frame hash, and look each one up in the anchor table. Bytes
   are discarded after hashing, so memory stays flat.
4. **Fit.** Each hit gives one offset sample: listener frame index minus
   transcribed frame index. The fitter keeps a run only when at least 5
   consecutive anchors agree on the same offset, and drops isolated
   hits. The result is a list of spans `{src_start, src_end, offset}`.
   Gaps on the source side are ads only in the transcribed copy; gaps on
   the listener side are ads only in the listener's copy.
5. **Verify.** The stream's total frame count must match the audio
   element's `duration` within 0.5 s. A mismatch means the fetch got a
   different ad set than the element did, and the map is discarded.
6. **Publish.** The map goes to the player as it grows, so the first
   minutes are usable while the rest downloads. It is cached in
   `sessionStorage` keyed by episode and content length.

Confidence: the map is applied only when matched spans cover at least
90% of the transcribed copy's non-`ad_break` duration.

### 5. Player: a piecewise timeline

Spec #61 maps logical time to asset time with one number,
`timeline_offset`. This spec generalises that to a `TimelineMap` with
two functions:

- `toAsset(t)`: transcript time to audio time. A time inside a source-side
  gap maps to the start of the next span.
- `toLogical(a)`: audio time to transcript time, or `null` inside a
  listener-side ad.

Every caller that adds `playback_time_offset_seconds` today goes through
the map instead: segment seek, citations, chapters, listening-position
resume, karaoke. When `toLogical` returns `null` the karaoke highlight
holds its last word.

Listening positions (#90) are stored in transcript time once a map is
present, so resuming on another device with different ads lands on the
same word.

### 6. Reader

- Map applied: no banner.
- Map building: the existing banner text, with "Aligning…" beneath it.
- Map failed or not attempted: today's banner, unchanged.

### 7. Logging and analytics

- Server: `sync_track_written` (`episode_id`, `anchors`, `bytes`,
  `duration_ms`), `sync_track_failed`.
- Client (PostHog, per #95): `drift_alignment_result` with `outcome`
  (`identity`, `aligned`, `no_cors`, `variant_mismatch`,
  `low_confidence`, `unsupported_codec`, `error`), span count, largest
  offset, and bytes fetched. This is the measure of how often the banner
  is replaced.

## Edge cases

- **Redirect hop without CORS.** `fetch()` rejects; outcome `no_cors`.
  The host is remembered for the session so later episodes skip the
  attempt.
- **Variable bit rate.** Frame parsing reads each header, so VBR is
  handled. Range offsets for the cheap check are estimates; a miss
  falls through to the full pass.
- **Publisher re-uploaded the episode.** No frame anchors match. Outcome
  `low_confidence` until the perceptual track of Phase 3 exists.
- **Same ad in both copies at different positions.** Ads are not in the
  unique-anchor set often enough to form a run of 5, and a run that
  breaks monotonic source order is rejected by the fitter.
- **Content edited by the publisher** (a segment cut or added). It shows
  up as a source-side or listener-side gap, the same as an ad.
- **Metered connections.** The full pass downloads the file a second
  time. It is skipped when `navigator.connection.saveData` is set.

## Execution plan

### Phase 0: verify in a browser

A throwaway page on a branch. Answer the questions the curl probe could
not:

1. Does `fetch()` from the same browser get the same ad set as the
   `<audio>` element, on Megaphone, Acast, Captivate and Art19?
2. What is the real CORS pass rate from a browser, including preflight?
3. How long does the full pass take on a phone?

Exit: variant match on at least 3 of the 4 hosts. Otherwise the client
must play from the fetched bytes (Alternatives, single fetch) and the
plan changes before any pipeline work.

### Phase 1: sync track in the pipeline

| # | Where | What |
|---|---|---|
| 1.1 | `thestill/core/sync_track.py` (new) | MP3 frame parser, anchor selection, binary writer and reader |
| 1.2 | `thestill/core/task_handlers.py` | write the track after download, before audio cleanup |
| 1.3 | `thestill/utils/path_manager.py`, migration | `sync_tracks/` directory, `episodes.sync_track_path` |
| 1.4 | `thestill/web/routes/` | `GET /api/episodes/{id}/sync-track`, `has_sync_track` |

Acceptance: a contract test that builds a track from a fixture MP3,
splices a second MP3 into a copy of it at two points, and recovers both
offsets from the track alone.

### Phase 2: client map and piecewise timeline

| # | Where | What |
|---|---|---|
| 2.1 | `src/workers/driftAlign.worker.ts` (new) | stream fetch, frame hashing, fitter, verification |
| 2.2 | `src/contexts/playback-engine/` | `TimelineMap` with `toAsset` / `toLogical` |
| 2.3 | `src/contexts/PlayerContext.tsx`, `EpisodeReader.tsx` | route seeks and karaoke through the map; banner states |
| 2.4 | analytics | `drift_alignment_result` |

Acceptance: a Playwright test serves two spliced variants of one fixture
and asserts that a segment click lands on the same audio in both.

### Phase 3: perceptual fallback and AAC

Add the Haitsma-Kalker track at ingest, and a client path that decodes
with WebCodecs `AudioDecoder` when frame anchors fail. Add ADTS and MP4
frame parsing for AAC enclosures. Scope this from Phase 2's
`low_confidence` and `unsupported_codec` rates.

### Phase 4: ad map of our own copy

At ingest, download the episode a second time with a different user
agent and diff the two tracks. Frames that are not in both copies are
ads. This gives `ad_break` tagging an audio-level signal and resolves
the `net` versus `served` chapter timelines of #97. It doubles download
bandwidth, so it is opt-in per show.

### Phase 5: old episodes

Episodes without a track need forced alignment of the existing
transcript against a fresh download (WhisperX align is already in the
repo), after which a track is written from that download. This is the
same aligner #110 Phase 4 needs. Scope it there.

## Alternatives considered

- **Chromaprint anchors with a bounded search window.** Plausible and
  untested. It needs a decode on both sides, a WebAssembly build in the
  browser, and its 124 ms hop limits precision. A bounded window is
  unnecessary: whole-file search is cheap, and measured offsets ran from
  −73 s to +40 s.
- **Single fetch: play from the fetched bytes** through Media Source
  Extensions. One download instead of two and no variant mismatch. It
  replaces the native engine's source handling and loses the browser's
  own buffering. Held in reserve for a failed Phase 0.
- **Forced alignment per listener.** Exact, and needs speech recognition
  in the browser or a server copy of each listener's audio.
- **Server proxy.** Ruled out under Non-goals.
- **Pre-roll scalar plus user nudge.** Corrects only the first break.
  Three of five measured episodes had more than one.

## Open questions

1. **Second download.** The full pass fetches the file again. IAB
   measurement de-duplicates by IP and user agent within 24 hours, so
   publisher counts should not inflate. Is the listener's bandwidth
   acceptable on mobile, or should the full pass wait for Wi-Fi?
2. **Listener-side ads in the karaoke view.** Hold the last word (this
   spec), or show a neutral "Ad" marker?
3. **`anchor.fm` and Substack.** No CORS, and no size variance observed.
   Treat equal duration as identity for those hosts without
   verification?
4. **Server consumers.** `get_episode_clip` and the clip work of #114
   hand out transcript times against the publisher URL. They cannot know
   the recipient's ad set. Is a "times are approximate" note enough
   there?

## Rollback plan

The client path is behind a feature flag; off restores the banner. The
sync track is an inert file and a nullable column. Neither needs
removal.

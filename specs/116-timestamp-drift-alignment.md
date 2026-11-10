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
- **Eager backfill of every old episode.** Their transcribed copy is
  gone, so there is nothing to fingerprint. Phase 5 aligns them lazily,
  one episode at a time, when someone plays one.
- **Re-cleaning or re-summarising old episodes.** Phase 5 never touches
  stored transcripts, summaries, citations or edits.
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

4. **Why does Thestill compute the track on the Dalston path, and not
   Dalston?** (Decided 2026-11-10.) The sync track is a playback concern
   of Thestill's reader; no other Dalston client has a use for it.
   Dalston's contract stays generic: transcribe this input, and let the
   caller read back the input that was processed, which it already
   does. Thestill then has one frame parser and one track format for
   every provider, and can change the algorithm version without a
   Dalston release. The cost is one read of the audio per episode from
   Dalston's storage.

## Design

### 1. The sync track

One binary artefact per episode, written once, never changed:

| Field | Content |
|---|---|
| Header | algorithm version, codec, sample rate, frame count, byte length, duration of the transcribed copy |
| Frame anchors | one per second: frame index (4 bytes) + hash of 4 consecutive compressed frames (6 bytes) |
| Base spans (Phase 5 only) | a short list of `{src_start, src_end, offset}` mapping transcript time onto the copy the anchors were taken from; empty when that copy is the transcribed one |
| Perceptual track (Phase 3, flagged shows only) | Haitsma-Kalker sub-fingerprints, every 8th frame, about 155 KB per hour |

Rules for frame anchors:

- An anchor covers 4 consecutive frames (about 104 ms at 44.1 kHz), so a
  single silent frame cannot match.
- An anchor whose hash appears more than once in the transcribed copy is
  dropped at write time. This removes silence and repeated stings.
- If fewer than half the seconds yield a unique anchor, the track is
  written with `frame_anchors: none` and only the perceptual track
  applies.

At 10 bytes per anchor a one-hour episode is 36 KB before compression.

The track is written for every episode, including shows that have never
carried ads. A publisher can switch on ad insertion for a back catalogue
at any time, and 36 KB is cheaper than finding out later. The
perceptual track is different: at 155 KB per hour it is written only for
shows flagged `needs_perceptual_track` (section 8).

### 2. Pipeline: write the track from the bytes that were transcribed

The rule: the track is built from the exact bytes the transcript came
from, and a transcript never keeps a track built from other bytes.

There are two paths to those bytes:

- **Local path** (Whisper, WhisperX, and any episode that goes through
  the download stage). The original file is on disk. A step after
  download parses its MP3 frames and writes the track, before
  `DELETE_AUDIO_AFTER_PROCESSING` removes the file
  ([task_handlers.py:382](../thestill/core/task_handlers.py#L382)).
- **Dalston URL path** (production today). With
  `transcription_provider == "dalston"` an RSS episode skips download
  and downsample, and Dalston fetches the publisher URL itself
  (`starting_stage_for` in
  [queue_manager.py](../thestill/core/queue_manager.py),
  `_transcribe_entry_stage` in
  [episode_reprocess.py](../thestill/services/episode_reprocess.py)).
  Thestill never sees the bytes. A download made by Thestill at that
  point would be a different request and possibly a different ad set,
  so it must not be used. Dalston keeps the fetched file as a job
  artefact and already serves it: `GET
  /v1/audio/transcriptions/{job_id}/audio` returns a signed URL.
  Thestill builds the track itself, inside the transcribe task, right
  after the job completes: it streams that artefact through the frame
  parser without writing it to disk. Doing it in the same task keeps it
  ahead of Dalston's retention window. Dalston is not changed
  (Clarification 4).

In both paths:

- The track is written through `PathManager`
  (`sync_tracks/<podcast>/<episode>.sync`) and its path recorded on the
  episode.
- Whenever a transcribe task replaces an episode's raw transcript, the
  existing track is deleted first and `sync_track_path` cleared. The
  new track is written only from the new job's bytes. A reprocess that
  cannot rebuild the track leaves the episode with none.
- A failure logs `sync_track_failed` with `episode_id` and does not fail
  the stage. The episode simply has no track.
- Non-MP3 enclosures get no frame anchors until Phase 3.

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
2. **Identity shortcut.** If the live duration equals the duration in
   the track header within 50 ms, the map is a *provisional* identity
   and nothing is fetched yet. This needs no CORS, so it also covers
   hosts the full pass cannot reach. Equal duration is not proof: a
   longer break followed by a shorter one gives the same total with a
   shifted stretch in between. So:
   - for a show without `ads_seen_at` (section 8), the worker stops
     here and logs `identity_assumed`. This is what the reader does
     today, since equal durations show no banner;
   - for a show with `ads_seen_at`, the full pass still runs in the
     background and replaces the provisional map if it finds a shift.
3. **Cheap check.** If the durations differ by less than 0.5 s, fetch
   three 256 KB ranges (start, middle, end) and look up their anchors.
   Offsets of 0 at all three give a provisional identity under the same
   two rules. Three samples cannot see a shift that opens and closes
   between them, so they never end the work for a show that inserts
   ads.
4. **Full pass.** Otherwise `fetch()` the audio URL as a stream with
   `mode: 'cors'`. Parse MP3 frame headers as bytes arrive, keep a
   rolling 4-frame hash, and look each one up in the anchor table. Bytes
   are discarded after hashing, so memory stays flat.
5. **Fit.** Each hit gives one offset sample: listener frame index minus
   transcribed frame index. The fitter keeps a run only when at least 5
   consecutive anchors agree on the same offset, and drops isolated
   hits. The result is a list of spans `{src_start, src_end, offset}`.
   Gaps on the source side are ads only in the transcribed copy; gaps on
   the listener side are ads only in the listener's copy.
6. **Verify.** The map describes the fetched copy. It is valid for
   playback only if the audio element is playing the same copy. Two
   checks, both required:
   - **Length.** The stream's total frame count must match the audio
     element's `duration` within 50 ms. A mismatch means a different ad
     set, and the map is discarded.
   - **Content.** Equal length does not prove equal content: two
     variants can carry breaks of different lengths that sum to the same
     total. Phase 0 must establish one of the following before any map
     is applied, per host:
     1. the host serves one variant to both requests from the same
        browser session, shown by measurement (the curl probe saw one
        variant per IP and user agent on 42 of 42 episodes, across
        repeats and after 15 minutes); or
     2. a spot check through the element itself: with
        `crossOrigin="anonymous"`, capture a few seconds of what the
        element plays through Web Audio at one point inside each span,
        and compare it with the fetched bytes decoded at the mapped
        position; or
     3. single fetch: the element plays the fetched bytes (see
        Alternatives), so there is only one copy.

   Hosts with none of the three keep the banner. Length alone never
   applies a map.
7. **Publish.** The map goes to the player as it grows, so the first
   minutes are usable while the rest downloads. It is cached in
   `sessionStorage` keyed by episode and content length.

Confidence: the map is applied only when matched spans cover at least
90% of the transcribed copy's non-`ad_break` duration.

When the track carries base spans (Phase 5), the worker composes the two
maps before publishing: transcript time to the anchored copy, then the
anchored copy to the listener's copy. The player sees one map.

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
  (`identity_assumed`, `identity`, `aligned`, `no_cors`, `variant_mismatch`,
  `low_confidence`, `unsupported_codec`, `error`), span count, largest
  offset, and bytes fetched. This is the measure of how often the banner
  is replaced.

### 8. Per-show ad profile

Two facts are kept per podcast so that shows without ads cost nothing at
playback and little at ingest:

| Field | Set when | Used for |
|---|---|---|
| `ads_seen_at` | the ingest probe sees two sizes, or a client reports `aligned` with a non-zero offset | client eagerness |
| `needs_perceptual_track` | clients report `low_confidence` on episodes that do have frame anchors, above a threshold; or set by hand | writing the 155 KB perceptual track (Phase 3) |

- **Ingest probe.** At download, two `Range: bytes=0-1` requests with
  different user agents read the total size from `Content-Range`. Two
  sizes mean the show inserts ads. This is two tiny requests per
  episode, not a second download.
- **Client report.** `POST /api/episodes/{id}/drift-outcome` carries the
  outcome of section 4. It updates the two fields and, in Phase 5,
  enqueues alignment for an old episode.
- **Client eagerness.** For a show with `ads_seen_at` set, the worker
  starts when playback starts. For a show without it, the worker waits
  until the listener first uses a timestamp (a segment click, a
  citation, karaoke on screen), and the identity shortcut usually ends
  it there.

The profile never suppresses the frame-anchor track. It only decides how
much work happens around it.

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

4. Which content check of section 4 step 6 holds for each host: a
   measured single variant, the Web Audio spot check, or single fetch?
5. How much does the element's reported `duration` vary for identical
   bytes, across browsers and for VBR files?

Exit: for at least 3 of the 4 hosts, one content check is shown to work
and same-session requests are shown to receive one variant across at
least 50 plays. If same-session variants differ on any host, that host
uses single fetch or keeps the banner, and the plan is revised before
any pipeline work.

### Phase 1: sync track in the pipeline

| # | Where | What |
|---|---|---|
| 1.1 | `thestill/core/sync_track.py` (new) | MP3 frame parser, anchor selection, binary writer and reader |
| 1.2 | `thestill/core/task_handlers.py` | local path: write the track after download, before audio cleanup; delete the old track whenever a transcribe task replaces the raw transcript |
| 1.2b | `thestill/core/dalston_transcriber.py`, transcribe handler | Dalston URL path: after the job completes, stream the job's stored original (signed URL from Dalston's audio endpoint) through the frame parser |
| 1.3 | `thestill/utils/path_manager.py`, migration | `sync_tracks/` directory, `episodes.sync_track_path` |
| 1.4 | `thestill/web/routes/` | `GET /api/episodes/{id}/sync-track`, `has_sync_track` |
| 1.5 | download stage, migration | two-user-agent size probe; `podcasts.ads_seen_at`, `podcasts.needs_perceptual_track` |

Acceptance: a contract test that builds a track from a fixture MP3,
splices a second MP3 into a copy of it at two points, and recovers both
offsets from the track alone. A second test runs a transcribe task on
the Dalston URL path with a stubbed job and asserts that the old track
is gone and the new one comes from the job's artefact.

### Phase 2: client map and piecewise timeline

| # | Where | What |
|---|---|---|
| 2.1 | `src/workers/driftAlign.worker.ts` (new) | stream fetch, frame hashing, fitter, verification |
| 2.2 | `src/contexts/playback-engine/` | `TimelineMap` with `toAsset` / `toLogical` |
| 2.3 | `src/contexts/PlayerContext.tsx`, `EpisodeReader.tsx` | route seeks and karaoke through the map; banner states |
| 2.4 | analytics | `drift_alignment_result` |
| 2.5 | `thestill/web/routes/` | `POST /api/episodes/{id}/drift-outcome` updating the per-show ad profile (rate-limited) |

Acceptance: a Playwright test serves two spliced variants of one fixture
and asserts that a segment click lands on the same audio in both.

### Phase 3: perceptual fallback and AAC

Add the Haitsma-Kalker track at ingest for shows flagged
`needs_perceptual_track`, and a client path that decodes with WebCodecs
`AudioDecoder` when frame anchors fail. Add ADTS and MP4 frame parsing
for AAC enclosures. Scope this from Phase 2's `low_confidence` and
`unsupported_codec` rates.

### Phase 4: ad map of our own copy

At ingest, download the episode a second time with a different user
agent and diff the two tracks. Frames that are not in both copies are
ads. This gives `ad_break` tagging an audio-level signal and resolves
the `net` versus `served` chapter timelines of #97. It doubles download
bandwidth, so it is opt-in per show.

### Phase 5: old episodes, lazily

Episodes transcribed before Phase 1 have no track, and the copy they
were transcribed from is gone. A fresh download is transcribed once and
used only as a ruler: the new words are matched against the stored raw
transcript to learn how the old copy maps onto the new one. The new
transcript is then thrown away.

A new task type, `BUILD_SYNC_TRACK`, separate from the reprocess chain
of #106 (which would run on through clean and summarize):

1. Obtain audio and transcribe it by whichever path the provider uses
   (section 2). The sync track must come from those same bytes, so on
   the Dalston URL path it is built from the job's stored original.
2. The transcript goes to a scratch path. The stored raw transcript, the cleaned
   transcript, the summary, citations, entity mentions and the edit log
   of #110 are not read for writing and not replaced.
3. Normalise both word lists (case, punctuation) and align them as
   sequences. Each matched word gives one offset sample: new start
   minus old start. Words the two engines render differently ("2024"
   against "twenty twenty four") simply do not match.
4. Fit the same step function as section 4. The offset is constant
   between ad breaks, so each span takes the median of its samples.
   Single-word timing error (about 0.1–0.2 s) averages out.
5. Write the sync track from the new download, with the fitted spans as
   its base spans. Delete the scratch transcript and the audio.

Acceptance thresholds: at least 60% of the old words matched, and spans
covering at least 90% of the old transcript's non-`ad_break` duration.
Below that no track is written and the episode is marked
`sync_track_unalignable` so it is not retried on every play.

Trigger and limits:

- A client that would show the drift banner on an episode without a
  track reports it through the section 8 endpoint. The server enqueues
  `BUILD_SYNC_TRACK` at the lowest priority, de-duplicated per episode.
  The next visit gets the map.
- A daily cap on these tasks, a runtime setting, keeps a crawler or one
  curious user from re-transcribing the catalogue.
- `thestill sync-track --podcast-id X [--episode <slug>] [--dry-run]`
  runs the same task by hand.

Cost per episode: one download and one transcription. No LLM calls.

Acceptance: for a fixture episode with a stored transcript, a spliced
variant of its audio yields base spans within 0.2 s of the known splice
offsets, and no stored artefact changes on disk.

Word timings for edited segments still need forced alignment. That stays
with #110 Phase 4 and is not required here.

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
- **Forced alignment for old episodes** (CTC alignment of the stored text
  against a fresh download). It gives the same result as Phase 5 and
  needs a second model path. Re-transcribing reuses the pipeline that
  already runs, and its output is discarded either way.
- **Skip the track for shows without ads.** Saves 36 KB per hour and
  leaves the episode uncorrectable if the publisher adds ads later.
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
3. **Identity tolerance.** The shortcut assumes the audio element
   reports the same duration for the same bytes within 50 ms, across
   browsers and for VBR files. Phase 0 measures this.
4. **Server consumers.** `get_episode_clip` and the clip work of #114
   hand out transcript times against the publisher URL. They cannot know
   the recipient's ad set. Is a "times are approximate" note enough
   there?
5. **Phase 5 cap.** What daily number of lazy re-transcriptions is
   acceptable, given the Mac and GPU pool?

## Rollback plan

The client path is behind a feature flag; off restores the banner. The
sync track is an inert file and a nullable column. Neither needs
removal.

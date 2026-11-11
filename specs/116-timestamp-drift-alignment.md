# Timestamp Drift Alignment: Map the Transcript onto the Listener's Copy

> **Status:** 🚧 Phase 1 in production (v1.38.0, 2026-11-11): tracks are written at transcription, 629 recent episodes backfilled. Phase 2 built for sessions holding `drift.align` (admins): the browser aligns on first play, checks the playing audio through a service worker, and applies the map to seeks, the clock and karaoke when both checks pass. Not built: resume correction, the audit command, the per-show ad profile. Phase 0 run on desktop Chrome and desktop Safari (see Browser check and Safari check)
> **Created:** 2026-11-10
> **Updated:** 2026-11-11
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

Not tested: Chromaprint (the `fpcalc` binary was not available) and AAC
enclosures.

### Browser check (Phase 0, 2026-11-10)

A throwaway page on `localhost`, driven in desktop Chrome 157. For the
latest episode of 18 shows it loaded an `<audio crossorigin="anonymous">`
element, fetched the same URL with `fetch()`, and used a service worker
to read the first 256 KB of every response the element received. The
element was then seeked to 20%, 45%, 70% and 93%, and each range it
requested was compared byte for byte with the fetched copy. Eight
further rounds, about a minute apart with the HTTP cache bypassed,
compared the total size each of the two requests was served.

| Check | Result |
|---|---|
| CORS: `fetch()` and the `crossorigin` element both load | 15 of 18 |
| Element and fetch served the same total bytes | 15 of 15 |
| Element's bytes identical to the fetched copy at every sampled range | 15 of 15 (71 ranges) |
| Rounds in which the two requests were served different totals | 0 of 111 completed (of 120: 7 lost the element's size to a measurement gap, 2 hit a network error) |
| Distinct sizes seen per episode across about 10 minutes | 1 for all 15 |
| Full fetch on a desktop connection | 0.5–11 s for 10–313 MB |
| Parsing frame headers of the whole file | 4–47 ms |

Hosts that passed: Megaphone (7 episodes), Acast (3), Art19,
Simplecast (2), Audioboom, Triton/Omny, Flightcast. Failed on CORS:
Captivate, one podtrac chain (The Ezra Klein Show), and the BBC.

What this settles:

- **One variant per session.** No host served the element and the fetch
  different copies. Megaphone has 63 matching plays; Acast 25 and Art19
  9, short of the 50 the exit asked for.
- **A service worker is a working content check.** It sees the exact
  bytes the element receives, so the comparison is exact and needs no
  audio decoding. Two conditions: the element must carry
  `crossorigin="anonymous"`, and the worker must return the original
  response. A response rebuilt from a teed stream loses `Content-Range`
  and Chrome rejects it with a format error.
- **`crossorigin` breaks playback where CORS fails.** The three failing
  hosts did not load at all with the attribute set. It must be set only
  after a probe `fetch()` has succeeded; those failed within 0.6 s.
- **Reported duration is not a reliable length.** For identical bytes
  the element's `duration` differed from the summed frame durations by
  under 1 ms on 7 episodes, by 8 ms on 1, by about one frame (25 ms) on
  4, by 52 and 62 ms on 2, and by 9.9 s on one (Flightcast, The Diary Of A CEO).
  Byte totals are exact and are used for the length check.

Not covered: Firefox, any phone, hashing cost, and playback over hours
or across networks. The 9.9 s outlier is unexplained; if Chrome's
time-to-byte seeking is off on that file, seeks land wrong there
whatever the map says.

### Safari check (desktop, 2026-11-11)

The same page, rebuilt, run once in Safari 27.2 on macOS against 8
episodes, one per host, from `localhost`. One play per episode.

| Measurement | Result |
|---|---|
| Service worker controls the page and sees the element's requests | 8 of 8, all with `destination: "audio"` |
| `fetch()` with CORS succeeds | 6 of 8 |
| Element's bytes identical to the fetched copy at every sampled range | 6 of 6 (19 ranges) |
| Element's total equals the fetched total | 6 of 6 |
| Element `duration` minus summed frame durations | 0 ms on 3, one frame (±26 ms) on 2, 52 ms on 1 |
| Full fetch | 0.4–9.2 s for 10–86 MB |

Hosts that passed: Megaphone, Acast, Art19 (through podtrac),
Audioboom, Omny/Triton (through podtrac), Flightcast. Failed on CORS:
Captivate, and one podtrac chain ending at Simplecast whose final
response carries no `Access-Control-Allow-Origin`. That chain fails the
same way in Chromium, so it is the chain and not the browser. On both,
the element with `crossorigin` failed to load and the plain element
played.

What this settles, and what it changes in section 4:

- **The content check works in desktop Safari.** The worker sees every
  media request and can read the bytes on hosts with CORS.
- **`Content-Range` is usually unreadable.** It is not a CORS-safelisted
  response header, and only 1 of the 6 hosts exposed it. The worker
  must take the offset of a response from the `Range` header of the
  request. `Content-Length` is always readable.
- **Requests cannot be matched by the enclosure URL.** Safari sends
  `bytes=0-1` to the enclosure URL, then sends every later range
  straight to the final URL of the redirect chain (a session URL on
  Megaphone, Acast, Art19, Audioboom and Triton). The worker matches on
  the request's destination and client, not its URL.
- **Safari asks for closed ranges.** After `bytes=0-1` it asks for
  `bytes=0-(N−1)`, where N is the total it read from `Content-Range`.
  The element's total is therefore read from its own request. Chrome
  asks for `bytes=0-`, and the total is the response's `Content-Length`.
- **The probe fetch can fail slowly.** Captivate failed in 0.2 s; the
  Simplecast chain took 3.0 s. The 0.6 s seen in Chrome is not a bound.
- **Duration.** The Flightcast show that was 9.9 s off in Chrome was
  0 ms off here, on a different episode. Open question 3 stands.

A side observation on how common inserted ads are: the copies served to
this browser were longer than the feed's declared `itunes:duration` by
61 s to 317 s on 6 of the 8 episodes (Megaphone, Art19, Audioboom, Omny,
Captivate, Flightcast) and equal on 2 (Acast, Simplecast). The declared
duration is the publisher's figure, not the copy Thestill transcribed,
so this measures inserted audio, not drift against a transcript.

A browser check of this kind shows what a host does on the day it is
run. It cannot show that a host without ads today will have none
tomorrow, or that a host will keep serving one variant per session. The
design does not depend on either: the map is built for every play from
the copy that play receives, and the content check of section 4 runs in
every session.

Not covered: Safari on iOS (the page needs HTTPS to run on a phone),
Firefox, more than one play per episode, and private browsing.

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
| Header | algorithm version, transcript generation, codec, sample rate, frame count, byte length, duration of the transcribed copy |
| Frame anchors | one per second: start time in milliseconds (4 bytes) + digest of 4 consecutive compressed frames (6 bytes) |
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

The digest uses only CRC-32 and Adler-32, so the browser can compute the
same value without a crypto library and each byte is hashed once:

- frame digest: `crc32(frame)` and `adler32(frame)`, unsigned 32 bit;
- anchor digest over four consecutive frames: pack the eight values
  little-endian (crc, adler per frame, in order), then
  `crc32(packed) << 16 | (adler32(packed) & 0xFFFF)`, 48 bits.

An anchor stores a time, not a frame index, because a spliced file can
mix sample rates. The binary layout is defined by `SyncTrack.to_bytes`
in [sync_track.py](../thestill/core/sync_track.py).

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
  parser without writing it to disk. Dalston keeps job audio for 30
  days by default, and Thestill does not override that, so a failed
  read can be retried later by `thestill sync-track` without
  re-transcribing. Dalston is not changed (Clarification 4).

In both paths:

- A track and the raw transcript it describes form one **generation**,
  a random id minted when the track is built. The generation is in the
  track header and in its path, written through `PathManager`
  (`sync_tracks/<podcast>/<episode>.<generation>.sync`).
- A track built before its transcript is stored (the local path builds
  it at download) is **pending** and is not visible to readers. When
  the transcribe task has stored the raw transcript it **publishes**:
  the pending track becomes the episode's current one and the previous
  one is deleted. With no pending track the previous one is deleted
  anyway, because the transcript it described has just been replaced.
- Publishing checks that track and transcript describe the same audio:
  on the local path the track's duration must equal the measured
  duration of the transcribed file within 2 s, and on every path no
  transcribed segment may end after the track's audio does. A track
  that fails is discarded and logged as
  `sync_track_transcript_mismatch`.
- **The cleaned transcript records its track.** Readers see the cleaned
  transcript, and during a reprocess the old cleaned transcript stays
  visible while the episode's current track already belongs to the new
  raw transcript. The clean stage therefore stamps
  `sync_track_generation` into the cleaned transcript, and a reader
  uses a track only when its generation equals that stamp. In the
  window between re-transcribing and re-cleaning the two differ, so no
  correction is applied.
- A failure logs `sync_track_failed` with `episode_id` and does not fail
  the stage. The episode simply has no track.
- Non-MP3 enclosures get no frame anchors until Phase 3.

Schema: a table `episode_sync_tracks` (Alembic 0028, SQLite and
Postgres), not a column on `episodes`. One row per stored track:
`episode_id`, `generation`, `state` (`pending` or `current`, at most one
of each per episode), `path`, `source`, `frames`, `anchors`,
`audio_bytes`, `audio_duration_s`, `created_at`. A separate table keeps
the pending/current bookkeeping out of the episode row's many write
paths and deletes with the episode. Phase 5 adds its status here.

### 3. API

`GET /api/podcasts/{podcast}/episodes/{episode}/sync-track/{generation}`
(next to the transcript route, with the same session requirement)
returns the artefact as
`application/octet-stream` with `Cache-Control: immutable`, or 404 when
that generation is not the episode's current one. It has the same
visibility as the transcript. The transcript payload carries
`sync_track_generation: string | null`, the stamp of the cleaned
transcript (section 2), and only when it equals the current track's
generation; otherwise null, so the client does not probe. Because the generation is in the URL, an
immutable cached track can never accompany a newer transcript.

### 4. Client: build the map

All work runs in a Web Worker so playback and scrolling are unaffected.

1. **Gate.** Run only when the episode has a track, the engine is the
   native audio engine, and the playing asset is the RSS audio.
2. **Identity shortcut.** If the copy being played has the byte length
   recorded in the track header, the map is a *provisional* identity
   and nothing more is fetched. The byte length comes from the service
   worker (step 6). Where CORS fails it cannot be read, and the
   shortcut falls back to comparing the live duration with the header's
   within 100 ms; the browser check measured up to 62 ms of difference
   on identical bytes, and one host far outside that, which simply
   misses the shortcut. Equal duration is not proof: a
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
   rolling 4-frame hash, and look each one up in the anchor table. In
   the same pass, keep an 8-byte hash of every 64 KB block of the
   stream (the block digest, about 7 KB for a 60 MB file). The bytes
   themselves are discarded, so memory stays flat.
5. **Fit.** Each hit gives one offset sample: listener frame index minus
   transcribed frame index. The fitter keeps a run only when at least 5
   consecutive anchors agree on the same offset, and drops isolated
   hits. The same audio can occur twice in a listener's copy (two of
   the five measured pairs had a promo played both early and late), so
   runs can overlap in source time; the fitter keeps the heaviest chain
   of runs that moves forward in both copies. A run also ends when more
   than two of the track's anchors in a row go unmatched: an ad replaced
   by another of the same length leaves the offset unchanged on both
   sides, and the stretch between must not be reported as aligned. The
   result is a list of spans `{src_start, src_end, offset}`.
   Missing anchors are not evidence of an ad, so each source-side
   interval between two spans is then refined and classified:

   - **Refine.** Inside the interval, single hits that agree with the
     offset of either neighbouring span extend that span; the offset is
     already established, so no run of 5 is needed.
   - **Confirmed gap.** What remains is treated as content absent from
     the listener's copy only when it is covered by `ad_break` segments
     (#18) or holds no transcript words.
   - **Unresolved.** Anything else is kept as an `unresolved` interval,
     not a gap.

   Gaps on the listener side are ads only in the listener's copy.
6. **Verify.** The map describes the fetched copy. It is valid for
   playback only if the audio element is playing the same copy. Two
   checks, both required:
   - **Length.** The total byte length the element was served must
     equal the length of the fetched stream, exactly. A mismatch means
     a different ad set, and the map is discarded. Durations are not
     compared: the browser check found them unreliable. The element's
     total is read without `Content-Range`: for an open-ended request
     (`bytes=N-`, Chrome) it is N plus the response's `Content-Length`;
     that is authoritative. A closed request (`bytes=A-M`, Safari)
     proves only that the copy has at least M + 1 bytes, so it is used
     differently. Safari's opening `bytes=0-1` request is ignored. Of
     the element's other closed requests, any that ends at or beyond the
     fetched length means a longer copy, and the map is discarded. The
     lengths count as equal only when at least one of them ends exactly
     at the fetched length minus one, was returned in full
     (`Content-Length` equal to the range asked for), and none ends
     beyond it: Safari
     ends its ranges at the last byte of the total it read from
     `Content-Range`. A session that yields neither an authoritative
     total nor that equality (for example a browser that asks for the
     file in fixed chunks) has no length check and gets no map.
   - **Content.** Equal length does not prove equal content: two
     variants can carry breaks of different lengths that sum to the same
     total. A service worker reads the head of every response the
     element receives, hashes the first whole 64 KB block in it
     (blocks are aligned to the start of the file, so a range that
     starts mid-block is checked from the next boundary), and compares
     that hash with the block digest of the fetched copy. The offset of
     a response is the start of the request's `Range` header;
     `Content-Range` is unreadable on most hosts (see Safari check). The
     worker picks out the element's requests by destination (`audio` or
     `video`) and client, because after a redirect the browser may
     request the final URL directly. The digest
     is kept with the map for as long as the map is in use, so ranges
     requested after a later seek are checked the same way. The first
     response and every later range must match; any mismatch discards
     the map, also after it has been applied. A map restored from
     `sessionStorage` is restored with its digest and re-verified
     against the element's first response before it is applied.

   Both checks need the element to load with `crossorigin="anonymous"`,
   and an element with it fails to load on a host without CORS. The
   attribute must be present before the element loads; changing it
   reloads the audio. Playback never waits for that decision:

   - The verdict is kept per final host of the redirect chain, in
     `localStorage`, for 7 days: `cors_ok` or `no_cors`.
   - A host with `cors_ok` loads with the attribute from the start.
   - A host with no verdict loads without the attribute and plays as
     today. A probe `fetch()` runs beside it with a 4 s timeout and
     stores the verdict. That play gets no map and keeps the banner;
     the next load of an episode from the host gets one.
   - A host with `cors_ok` whose element then fails to load is reloaded
     once without the attribute, and the verdict becomes `no_cors`.

   The worker must pass the original response through
   unchanged. Desktop Chrome and desktop Safari both route media
   requests through the worker. Where the service worker is unavailable
   or does not see media requests (Firefox and iOS are not yet checked;
   private browsing may have no worker), the worker detects it at run
   time, by seeing no request from the element, and the
   fallback is single fetch (see Alternatives) or the banner. Length
   alone never applies a map.
7. **Publish.** The map goes to the player only after the stream has
   ended, both checks of step 6 have passed and the confidence threshold
   below is met. While it is building the worker reports progress, not
   spans: the player keeps today's behaviour (no offset, banner with
   "Aligning…"), so seeks, highlighting and saved listening positions are
   never derived from a map that later fails verification. A provisional
   identity (steps 2–3) applies no offset, which is today's behaviour.
   It is cached in
   `sessionStorage` keyed by episode, track generation and content
   length.

Confidence: the map is applied only when matched spans cover at least
90% of the transcribed copy's non-`ad_break` duration.

When the track carries base spans (Phase 5), the worker composes the two
maps before publishing: transcript time to the anchored copy, then the
anchored copy to the listener's copy. The player sees one map.

### 5. Player: a piecewise timeline

Spec #61 maps logical time to asset time with one number,
`timeline_offset`. This spec generalises that to a `TimelineMap` with
two functions:

- `toAsset(t)`: transcript time to audio time. A time inside a confirmed
  source-side gap maps to the start of the next span. A time inside an
  `unresolved` interval maps with the preceding span's offset and is
  flagged approximate; the reader shows the drift note on those
  segments only.
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

### 7. Logging, audit and proof of correctness

A log of what the aligner decided does not show that the decision was
right. This section has three layers: what each step records, what the
listener's behaviour shows, and an independent audit that checks the
result against the audio itself.

All server events use `structlog` with `episode_id`, `podcast_id` and the
track `generation`. No event carries a user id, an IP address or a full
audio URL (host only).

#### 7.1 Building the track (server)

| Event | Fields | What it confirms |
|---|---|---|
| `sync_track_written` | `source` (`local`, `dalston`, `realign`), `frames`, `anchors_kept`, `anchors_dropped_duplicate`, `unique_ratio`, `audio_bytes`, `audio_duration_s`, `track_bytes`, `duration_ms` | a track exists and how dense it is |
| `sync_track_transcript_mismatch` (error) | `audio_duration_s`, `transcribed_input_duration_s`, `delta_s` | the track and the transcript do **not** describe the same bytes |
| `sync_track_failed` | `source`, `reason`, `exc_info` | no track, and why |
| `sync_track_switched` | `old_generation`, `new_generation` | transcript and track changed together |

`sync_track_transcript_mismatch` is the key invariant. The audio the
track was cut from and the audio the transcript came from must have the
same duration within 1 s. The comparison uses the input duration the
transcription job reports for its audio, not the time of the last
transcribed word: an episode that ends on music has a last word well
before the end of the file. When the two differ, the track is discarded
rather than published. To verify during implementation: that every
provider reports its input duration.

#### 7.2 Building the map (client, reported to the server)

The worker posts one report per attempt to
`POST /api/episodes/{id}/drift-outcome` (section 8). The server logs it
as `drift_map_reported` and forwards the summary to PostHog as
`drift_alignment_result` (#95). The body is validated and capped at
4 KB; a map is a handful of spans, so the whole map fits.

| Field | Meaning |
|---|---|
| `outcome` | `identity_assumed`, `identity`, `aligned`, `no_cors`, `variant_mismatch`, `low_confidence`, `unsupported_codec`, `error` |
| `spans` | the fitted map: `[{src_start, src_end, offset}]` |
| `gaps_confirmed_s`, `unresolved_s`, `listener_ads_s` | seconds in each class of interval (section 4, step 5) |
| `coverage` | share of non-`ad_break` transcript time inside matched spans |
| `anchors_total`, `anchors_hit`, `anchors_inlier`, `anchors_outlier` | how many landmarks were found, and how many agree with the span they fall in |
| `verify_length_ok`, `verify_ranges_checked`, `verify_ranges_matched` | the two checks of step 6 |
| `audio_host`, `audio_bytes`, `fetched_bytes`, `fetch_ms`, `fit_ms` | where the audio came from and what the pass cost |
| `browser` | family and major version |

What to read from it:

- **Inlier share.** In a correct map nearly every landmark that was
  found agrees with its span. A map with many outliers was fitted to
  noise even if it passed the coverage threshold.
- **Unresolved seconds.** Time where the aligner declined to decide.
  Large values on one show point at a host that re-encodes.
- **Verification counts.** `verify_ranges_matched` below
  `verify_ranges_checked` on an applied map is a bug, and is logged at
  error level.

#### 7.3 Aligning old episodes (Phase 5)

`sync_track_realigned`: `words_old`, `words_new`, `words_matched`,
`match_ratio`, `spans`, and for each span the number of word samples and
the spread of their offsets (median absolute deviation). A tight spread
(under 0.1 s) with a high match ratio is the evidence that the old
timings map cleanly onto the new copy. `sync_track_unalignable` carries
the same fields when the thresholds are missed.

#### 7.4 What listeners do

A segment click that lands in the wrong place is usually followed by a
manual correction. The client records `segment_seek_corrected` when a
seek started from a transcript segment, a citation or a chapter is
followed within 10 s by a manual seek of less than 90 s. The rate is
compared across three groups: episodes with an applied map, episodes
with a provisional identity, and episodes showing the banner. An applied
map that works should bring the rate down to the identity group's.

#### 7.5 Independent audit

The checks above all rest on the same landmarks. The audit does not. It
asks the question directly: is the sentence the transcript promises
actually spoken at the mapped time?

`thestill sync-track audit [--podcast-id X] [--episode <slug>] [--sample N]`:

1. Download the episode afresh. This is a new copy with whatever ads
   the server is given, as a listener would get.
2. Build the map with the same fitter the browser uses (one
   implementation, shared by test vectors with the client).
3. Pick sample points: one shortly after each span boundary, where an
   error would show first, and others spread across the episode.
4. At each point cut 10 s of audio at the *mapped* time, transcribe that
   clip, and compare its words with the stored transcript's words for
   the same 10 s.
5. For each sample, also search the clip's words in the transcript
   within ±120 s to measure how far off the timing is, not just whether
   it matched.

Logged as `sync_track_audit` per episode: `samples`, `samples_matched`,
`median_error_s`, `max_error_s`, `spans`, and the failing sample times.
An episode passes when every sample matches with an error under 0.5 s
(Goal 1).

The audit runs on a schedule over a small random sample of recently
played episodes (a runtime setting, default 5 per day) and on demand. It
costs one download and under two minutes of transcription per episode.
Its pass rate and median error go on the admin analytics surface next to
the outcome mix.

The audit checks the map against a server download, not against one
listener's copy. It proves the method on that episode; the service
worker check of step 6 is what ties a map to a listener.

#### 7.6 Looking at one episode

- `thestill sync-track show --episode <slug>` prints the track header,
  anchor density per minute, and the last reported maps and audit for
  the episode from the logs.
- For admins, the reader takes `?driftdebug=1`: a strip above the
  transcript draws the spans, confirmed gaps, unresolved intervals and
  listener-side ads on the episode timeline, shows the offset applied to
  each seek, and offers "play 5 s from the start of each span" for a
  check by ear.

### 8. Per-show ad profile

Two facts are kept per podcast so that shows without ads cost nothing at
playback and little at ingest:

| Field | Set when | Used for |
|---|---|---|
| `ads_seen_at` | the ingest probe sees two sizes, or a client reports `aligned` with a non-zero offset | client eagerness |
| `needs_perceptual_track` | clients report `low_confidence` on episodes that do have frame anchors, above a threshold; or set by hand | writing the 155 KB perceptual track (Phase 3) |

- **Ingest probe (not built, by decision of 2026-11-11).** At download,
  two `Range: bytes=0-1` requests with different user agents would read
  the total size from `Content-Range`; two sizes mean the show inserts
  ads. Until that decision changes, `ads_seen_at` is set by client
  reports only.
- **Client report.** `POST /api/episodes/{id}/drift-outcome` carries the
  outcome of section 4. It updates the two fields and, in Phase 5,
  enqueues alignment for an old episode.
- **Client eagerness.** For a show with `ads_seen_at` set, the worker
  starts when playback starts. For a show without it, the worker waits
  until the listener first uses a timestamp (a segment click, a
  citation, karaoke on screen), and the identity shortcut usually ends
  it there.

The profile never suppresses the frame-anchor track. It only decides how
much work happens around it. A missing `ads_seen_at` means no listener
has reported a shifted copy yet, not that the show has no ads: any show
can start inserting them, and the first play whose byte length differs
from the track header runs the full pass regardless of the profile.

## Edge cases

- **Redirect hop without CORS.** `fetch()` rejects; outcome `no_cors`.
  The host is remembered for the session so later episodes skip the
  attempt. The rejection took up to 3.0 s in Safari, so the probe has a
  4 s timeout and playback does not wait for it (section 4, step 6).
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

Result (2026-11-10, desktop Chrome only; see Browser check): the
service-worker content check works and no variant mismatch was seen.
Megaphone met the 50-play bar; Acast (25) and Art19 (9) matched on
every play but fall short of it; Captivate fails CORS and keeps the
banner. Questions 1, 2, 4 and 5 are answered for Chrome. Phase 1 does
not depend on the rest and was built.

Result (2026-11-11, desktop Safari; see Safari check): the service
worker sees media requests and the content check works there too, on
one play per episode. Questions 1, 2, 4 and 5 are answered for desktop
Safari. Still open: a phone (question 3, and whether Safari on iOS
behaves like the desktop), Firefox, and more plays on Acast and Art19.
None of these blocks building Phase 2 behind its flag, because a browser
where the worker sees no media request keeps the banner. They are the
checks to pass before the flag is turned on for a browser.

### Phase 1: sync track in the pipeline

| # | Where | What |
|---|---|---|
| 1.1 ✅ | `thestill/core/sync_track.py` | MP3 frame parser, anchor selection, binary writer and reader, and the server-side aligner |
| 1.2 ✅ | `thestill/core/task_handlers.py`, `thestill/services/sync_track_service.py` | local path: pending track after download; publish when the transcribe task has stored the transcript; clean stage stamps the generation |
| 1.2b ✅ | `thestill/core/dalston_transcriber.py`, transcribe handler | Dalston URL path: after the job completes, stream the job's stored original (signed URL from Dalston's audio endpoint) through the frame parser |
| 1.3 ✅ | `thestill/utils/path_manager.py`, migration 0028, repositories | `sync_tracks/` directory, `episode_sync_tracks` table |
| 1.6 ✅ | `thestill/services/sync_track_service.py` | events of section 7.1, including the track/transcript invariant |
| 1.4 ✅ | `thestill/web/routes/api_podcasts.py` | `GET /api/podcasts/{podcast}/episodes/{episode}/sync-track/{generation}`; `segments.sync_track_generation` in the transcript payload |
| 1.7 ✅ | `thestill/cli.py`, `thestill/services/sync_track_backfill.py` | `thestill sync-track backfill` and `thestill sync-track show` |
| 1.5 | deferred to Phase 2 | per-show ad profile (see the note below) |

Rows marked ✅ are implemented (Phase 1a wrote the tracks; Phase 1b
serves them and backfills). Nothing in the reader uses a track yet.
Phase 1 is released on its own, ahead of Phase 2: an episode transcribed
before the release has no track and can only be aligned by Phase 5, and
the backfill reaches back only as far as Dalston still holds the audio.
`SYNC_TRACKS_ENABLED=false` stops new tracks. Only episodes processed by
the task worker get a track; the direct CLI commands (`thestill
download`, `thestill transcribe`) do not.

The two-user-agent size probe of section 8 is not built, by decision
(2026-11-11, open question 6). It would send requests to publishers
under browser and Apple user agents that Thestill is not, on every
episode. The same question is answered without it: Phase 2's client
reports say directly whether a listener's copy differed.

Checked end to end on a local server (2026-11-11), with the track built
on the Dalston URL path from the job's stored MP3:

| Episode | Track | Listener copy | Map found | Check |
|---|---|---|---|---|
| Today in Focus (Acast), 52 min | 3,372 anchors, 33.8 KB, built in 1.6 s | 45 s longer, Chrome and Apple user agents | 0 s up to 2,623 s, +40.124 s from 2,688.7 s; the gap is the transcript's mid-roll `ad_break` | 12 of 12 segments |
| The Prof G Pod (Megaphone through podtrac) | 983 anchors, 9.9 KB | two different copies | +7.967 s (Chrome user agent), +31.974 s (Apple user agent) | 10 of 10 on both |

The check is independent of the track: decoded audio of the transcribed
copy and of the listener's copy, cross-correlated at each sampled
transcript segment after applying the map (correlation at least 0.987,
residual 0 ms; without the map the correlation after the mid-roll was
about 0).

Checked on the five real episode pairs of the Background findings: the
aligner returns the same offsets as the Haitsma-Kalker experiment
(−40.725 s and −72.803 s on Today in Focus, +10.005 s on The News
Agents, +28.824 s on No Such Thing As A Fish), with zero hits against a
re-encoded copy. Building a track took 0.2–0.9 s per episode and
aligning 0.2–0.8 s. Tracks were 14–43 KB.

Acceptance: a contract test that builds a track from a fixture MP3,
splices a second MP3 into a copy of it at two points, and recovers both
offsets from the track alone. A second test runs a transcribe task on
the Dalston URL path with a stubbed job and asserts that the old track
is gone and the new one comes from the job's artefact.

Backfill at ship time: episodes transcribed through Dalston recently
still have their original in Dalston's storage. `thestill sync-track
backfill` builds their tracks from those artefacts, using the job id
recorded with each raw transcript (`provider_metadata.job_id`), newest
episode first. These are exact tracks of the transcribed copy, so they
need none of Phase 5.

- A backfilled track is usable by the reader only once the cleaned
  transcript carries its generation. The backfill writes that stamp when
  the cleaned transcript was written after the raw transcript and has
  no stamp; otherwise it leaves it for the clean stage.
- Episodes are taken most recently transcribed first (the raw
  transcript's modified time), because Dalston's retention runs from
  the job, not from the publication date. `--since-days` (default 45)
  bounds the run.
- The backfill runs beside the pipeline. It skips an episode with a
  task in flight, re-checks after fetching the audio that the raw
  transcript still names the same job, and publishes only if the episode
  still has no track (checked in the same transaction). The pipeline in
  turn publishes only the pending track it built itself.
- The run stops after 20 episodes in a row whose audio Dalston no longer
  has. How far back it reaches is not settled: one URL-fetch job
  returned "audio deleted" at 25.7 days old, while a 21.6-day-old
  file-upload job still had its audio.
- A job submitted as a file upload stores the downsampled WAV, not the
  publisher's MP3, and gets no track (`unsupported_codec`).
- `thestill sync-track show --episode <slug>` prints an episode's track
  and whether the reader may use it.

### Phase 2: client map and piecewise timeline

| # | Where | What |
|---|---|---|
| 2.1 ✅ | `src/workers/driftAlign.worker.ts`, `src/drift/` | stream fetch, frame hashing, fitter, gap classification, block digest, verification |
| 2.2 ✅ | `src/contexts/playback-engine/timeline-map.ts` | `TimelineMap` with `toAsset` / `toLogical` |
| 2.3 ✅ | `src/contexts/playback-engine/native-engine.ts`, `PlayerContext.tsx`, `EpisodeReader.tsx` | the verified map applied inside the native engine, so every seek and every reported time goes through it; banner states |
| 2.4 ✅ | analytics | `drift_alignment_result` (`segment_seek_corrected` comes with 2.3) |
| 2.5 ✅ | `thestill/web/routes/api_podcasts.py` | `POST /api/podcasts/{podcast}/episodes/{episode}/drift-outcome`: validates and logs `drift_map_reported` (rate-limited, 4 KB cap enforced before the body is read) |
| 2.6 | `thestill/core/sync_track.py`, `thestill/cli.py` | ✅ server-side fitter sharing test vectors with the worker (`tests/fixtures/sync_track/align_vectors.json`); `thestill sync-track audit` and the scheduled audit sample are not built |
| 2.7 ✅ | `src/drift/DriftDebugStrip.tsx` | admin-only `?driftdebug=1` strip, with "play corrected" and "play uncorrected" per span |
| 2.8 ✅ | `public/sw.js`, `src/drift/swClient.ts`, `corsVerdict.ts`, native engine | the service worker, the `crossorigin` verdict and the two checks of section 4 step 6 |

Rows marked ✅ are implemented. A map is applied for sessions that hold
`drift.align`, and only when the copy is shifted (`aligned`) and both
checks of section 4 step 6 have passed for the audio now playing. Any
other verdict lifts it, also after it has been applied.

Where the map is applied: inside `NativeEngine`, not at each caller.
While a map is set, every time the engine takes or reports is transcript
time. So segment clicks, citations, chapters, the scrubber, the clock,
karaoke and the saved listening position are corrected by one change and
none of them knows about the map:

- a seek to transcript time t moves the element to `toAsset(t)`;
- the reported time is `toLogicalHeld(elementTime)`: through an ad that
  only the listener's copy has, the clock (and the karaoke highlight)
  waits at the point the ad interrupted;
- the reported duration is the transcribed copy's, so the drift banner,
  which compares durations, goes by itself once a map is in force;
- setting or lifting the map does not move the audio. Only the time
  reported for it changes.

The acceptance test is `tests/drift-alignment.spec.ts`: two real MP3
copies of one episode with different ads, served from a second origin
with CORS and Range, in Chromium. With the listener's copy, a click on
the paragraph at 30 s of the transcript moves the element to 39 s and
that paragraph is highlighted; with the transcribed copy the element goes
to 30 s and no map is set.

Rows 2.4, 2.5 and 2.7 were built first, with the worker, so that every
play reports its outcome from the first admin session.

The service worker is the app's first. Its rules:

- It answers only cross-origin requests whose destination is `audio` or
  `video`, and returns the original response. Every other request is
  left to the browser. It caches nothing and handles no navigation.
- It is registered on the first play of an episode that has a track, by
  a session that holds `drift.align`, and takes control of the open page
  without a reload. Signed-out visitors, public pages and crawlers never
  register it.
- `sw.js` is plain JavaScript with no build step, served from a fixed
  URL with `Cache-Control: no-cache`, and its URL never falls through to
  the HTML shell. A session that does not hold `drift.align` tells the
  worker to stand down, unregisters it and, if the page was under its
  control, reloads once: unregistering alone leaves a controlled page
  controlled. So switching the feature off reaches browsers that already
  have the worker.

How this was built, where it differs from the sections above:

- **Switch.** `DRIFT_ALIGNMENT_ENABLED` is on by default and exists to
  turn the feature off. It grants a named permission, `drift.align`,
  held by admins only for now; widening the rollout is a change to
  `has_permission`.
- **Route.** The report goes to `/api/podcasts/…/episodes/…/drift-outcome`,
  next to the sync track route. Every route under `/api/episodes` is
  operator-only.
- **No separate probe.** The alignment's own `fetch()` of the audio is
  the CORS probe of section 4 step 6: it runs beside playback, and its
  result is the verdict for the next load. There is no 4 s timeout
  because nothing waits.
- **Verdict per redirect chain.** The verdict is keyed by what the
  enclosure URL shows of its chain: its host plus the host names embedded
  in its path (`dts.podtrac.com/redirect.mp3/traffic.megaphone.fm/…`).
  The final host is not known when the element loads, and CORS has to
  hold on every hop: a podtrac chain ending at Megaphone passes and one
  ending at Simplecast does not.
- **Block digest.** CRC-32 and Adler-32 per 64 KB block (8 bytes, as the
  spec asks), the same two functions as the anchors, so the service
  worker needs no other hash.
- **Why a check is missing.** Each report carries `sw_state`:
  `unsupported`, `not_controlling`, `element_not_cors` (the first play of
  a chain), `no_requests`, `opaque` or `ok`. This is the per-browser
  evidence the rollout rule below reads. A map whose check fails is
  reported as `variant_mismatch`. The check is repeated for every later
  response of the same play, and a changed verdict is reported again
  with `revision` above 0, so a copy that differs only at a later ad
  break is found when a seek reaches it.
- **Not corrected yet: resuming.** A position saved while a map is in
  force is transcript time. On the next load the element seeks to it
  before any map exists, so it starts as far off as the ads differ, as it
  does today; the clock then shows the true transcript time. Correcting
  the start needs the map before the first seek (a cached map, step 7).
- **Not built:** the per-show ad profile (section 8) and the client
  eagerness it drives, the identity shortcut and the cheap check (steps
  2 and 3: every attempt is a full pass), the "Refine" step (the
  stretches a run leaves short of a splice are cut along the transcript's
  segments instead), caching a map in `sessionStorage`, the drift note on
  segments inside an unresolved stretch, the `segment_seek_corrected`
  event, and the audit command.

Measured on the built code (2026-11-11): the shipped `sw.js` with a
`crossorigin` element in headless Chromium and headless WebKit, on one
live Megaphone episode through podtrac. Playback worked through the
worker in both; the element's blocks matched the fetched copy on 5 of 5
ranges (Chromium, open-ended requests) and 2 of 2 (WebKit, closed
requests, the later ones sent straight to the final host), with equal
lengths. On production, an admin play of a Prof G Markets episode
reported three spans (0 s, +1.907 s, −30.798 s); a click on the 18:50
paragraph played the 19:20 one, as the last span predicts.

Rollout: admins only, then per browser. Standalone browser probes stop
here; the remaining questions (Safari on iOS, Firefox, more plays on
Acast and Art19, pass time on a phone) are answered by the outcome
reports of real plays on thestill.me. A browser is switched on for
everyone when its reports show no play in which the element's bytes
differed from the fetched copy, over at least 50 plays on each of
Megaphone, Acast and Art19.

Acceptance: a Playwright test serves two spliced variants of one fixture
and asserts that a segment click lands on the same audio in both. The
audit command passes on that fixture, and fails when the fixture's map
is shifted by 2 s on purpose.

### Phase 3: perceptual fallback and AAC

Add the Haitsma-Kalker track at ingest for shows flagged
`needs_perceptual_track`, and a client path that decodes with WebCodecs
`AudioDecoder` when frame anchors fail. Add ADTS and MP4 frame parsing
for AAC enclosures. Scope this from Phase 2's `low_confidence` and
`unsupported_codec` rates.

Episodes ingested before their show was flagged already have a
frame-only track and no original audio, so ingest alone never repairs
them. They are upgraded lazily through Phase 5's task: a client report
of `low_confidence` on an episode whose track has no perceptual data, in
a flagged show, enqueues `BUILD_SYNC_TRACK`. Phase 5 aligns against the
stored raw transcript, not the old track, so it writes a new generation
with a perceptual track and base spans. This repair path ships with
Phase 5.

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
Below that no track is written and `sync_track_status` is set to
`unalignable` so it is not retried on every play.

Failure domain: `BUILD_SYNC_TRACK` never changes the episode's
processing state. Like the entity branch (#28 §6), its outcome lives in
its own column, `episodes.sync_track_status` (`queued`, `built`,
`unalignable`, `failed`), and `_mark_episode_failed` in
[task_worker.py](../thestill/core/task_worker.py) gets an explicit branch
for it, so exhausted retries write `failed` there and never set
`failed_at_stage`. A `failed` episode is not re-enqueued by client
reports; `thestill sync-track` retries it by hand.

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
offsets, and no stored artefact changes on disk. A second test makes the
task fail until retries are exhausted and asserts that the episode's
state is still `summarized` and `sync_track_status` is `failed`.

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
3. **Duration outlier.** On one Flightcast episode Chrome's reported
   duration was 9.9 s longer than the summed frame durations of the
   same bytes. Is time-based seeking on that file accurate, and how
   common is this? Safari reported 0 ms on another episode of the same
   show.
4. **Server consumers.** `get_episode_clip` and the clip work of #114
   hand out transcript times against the publisher URL. They cannot know
   the recipient's ad set. Is a "times are approximate" note enough
   there?
5. **Phase 5 cap.** What daily number of lazy re-transcriptions is
   acceptable, given the Mac and GPU pool?
6. **Probing publishers under other user agents.** Section 8's ingest
   probe needs requests that present as a browser and as Apple's player.
   Acceptable, or rely on client reports only? **Decided 2026-11-11:**
   client reports only; the probe is not built.
7. **Where does `crossorigin` get decided?** The attribute must be on
   the element before it loads, and the probe that decides it took up
   to 3.0 s to fail. **Decided 2026-11-11:** playback never waits. The
   verdict is remembered per host and applied from the next load
   (section 4, step 6).
8. **Other browsers.** Does a service worker see media requests in
   Safari on iOS and in Firefox, and how long does the full pass take on
   a phone? **Decided 2026-11-11:** no further standalone probes; the
   outcome reports of admin plays on thestill.me answer it, and each
   browser is switched on separately (Phase 2, rollout).

## Rollback plan

The client path is behind a feature flag; off restores the banner and
unregisters the service worker on the next page load. The
sync track is an inert file and a nullable column. Neither needs
removal.

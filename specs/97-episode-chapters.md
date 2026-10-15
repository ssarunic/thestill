# Episode Chapters: Ingest, Show, Navigate

> **Status:** 📝 Draft — sources surveyed against live feeds on 2026-10-14, design drafted, not yet scheduled
> **Created:** 2026-10-14
> **Updated:** 2026-10-14 — manual spot check of ten show-note chapters, one ID3 episode and the two chapter-JSON items folded into §1, §6, Edge cases and Testing
> **Priority:** Medium — a quarter of followed shows already publish a table of contents that Thestill throws away; the same data is the cheapest signal available for the timestamp-drift problem
> **Author:** Product & Engineering
> **Related:** [#18 segment-preserving-transcript-cleaning](18-segment-preserving-transcript-cleaning.md) (`ad_break` segments, `playback_time_offset_seconds`), [#23 transcript-playback-sync](23-transcript-playback-sync.md) (click-to-seek, `?t=` deep links), [#42 robustness-and-failure-mode-hardening](42-robustness-and-failure-mode-hardening.md) (FM-1/FM-6/FM-7), [#54 summary-segment-citations](54-summary-segment-citations.md) (drift caveat, `transcript_source_duration_s`), [#62 youtube-video-rendition](62-youtube-video-rendition.md) (Podcasting 2.0 namespace extraction pattern), [#72 now-playing-sheet](72-now-playing-sheet.md) (scrubber tick row reserved for chapters), [#76 episode-detail-page-hierarchy](76-episode-detail-page-hierarchy.md) (header layout), [#87 episode-platform-links](87-episode-platform-links.md) (post-refresh resolver + not-found markers pattern), [#93 native-web-capabilities](93-native-web-capabilities.md) (Media Session chapters deferred), [#96 prompt-injection-hardening](96-prompt-injection-hardening.md) (feed text is untrusted)

---

## Executive Summary

Publishers describe an episode's structure in three places and Thestill reads
none of them: a Podcasting 2.0 chapters JSON linked from the feed, ID3 chapter
frames embedded in the MP3, and a timestamp list typed into the show notes.
The reader shows a flat transcript, the Now Playing sheet shows a scrubber
with entity ticks only, and the "Chapters" block in the show notes is inert
text.

This spec ingests all three sources into one `episode_chapters` table with
explicit provenance, picks the best available set per episode, and surfaces
it in the reader (chapter list under the header, section headings in the
transcript) and in the Now Playing sheet (a second tick series, current
chapter title). Every chapter is a seek target.

It also records, for each chapter set, which **timeline** its times are on.
Hand-typed show-note timestamps and static chapter JSON are *net* of
dynamically inserted ads; ID3 frames rewritten by the host at stitch time are
*gross* for the copy that was fetched. Keeping that distinction is what lets a
follow-up spec use chapters as an alignment signal for the drift problem that
[#54](54-summary-segment-citations.md) scoped out. This spec stores the
signal; it does not solve drift.

Survey of the followed corpus (2026-10-14, 40 random live feeds, 25 recent
enclosures, 625 local episodes since 2026-06-01):

| Source | Prevalence | Timeline |
|---|---|---|
| Show-note timestamp lists (≥3 per episode) | 118 / 625 episodes; 18 of 67 shows do it on most episodes (Huberman Lab, All-In, Diary of a CEO, Lenny's, Dwarkesh, Latent Space, Cal Newport, Football Daily…) | net |
| `<podcast:chapters>` JSON | 2 / 40 feeds (Acquired, Cheeky Pint) | net |
| ID3 `CHAP` / `CTOC` frames | 1 / 25 enclosures (The Skip Podcast — its two "chapters" are the editor's cue points, "Jagjit's Finishing Edit" and "Insert Mid-roll ad here", not a listener table of contents; see §1 *ad markers*) | gross for the fetched copy |

Show notes are by far the most common source and need no network fetch, so
they are Phase 0. The two structured sources are a feed-stage observer and a
small post-refresh fetch, following [#62](62-youtube-video-rendition.md) and
[#87](87-episode-platform-links.md) exactly.

## Why people want this

- **Jumping to the part you came for.** A two-hour interview with a 20-line
  table of contents in the show notes is navigated today by reading the
  timestamps and dragging the scrubber. Clicking the line should seek.
- **Orientation while listening.** "What section am I in" is the question the
  Now Playing sheet cannot answer. The tick row in
  [#72](72-now-playing-sheet.md) was explicitly designed to take chapters as a
  second series.
- **Structure for the transcript.** A 400-segment transcript with no headings
  is a wall. Publisher chapters are editorially chosen section breaks and
  beat anything inferred.
- **A free alignment signal.** When the host rewrites ID3 chapters at stitch
  time, the difference between the chapters in the copy we transcribed and the
  chapters in the copy a listener receives *is* the piecewise ad offset. When
  show notes are net, comparing them with the cleaner's `ad_break` positions
  gives the slot lengths in our own copy. Both need the provenance this spec
  records.

## Current state

- **Feed stage.** [media_source.py](../thestill/core/media_source.py)
  parses the Podcasting 2.0 namespace from raw XML for
  `<podcast:transcript>` and `<podcast:alternateEnclosure>`
  ([media_source.py:1456](../thestill/core/media_source.py#L1456)); the
  refresh batch writer carries the rows
  ([feed_manager.py:440](../thestill/core/feed_manager.py#L440)). No chapter
  tag is read.
- **Episode model.** [podcast.py:190](../thestill/models/podcast.py#L190)
  carries `description` (plain) and `description_html`; nothing parses
  timestamps out of either. The reader renders the HTML through
  `EpisodeHeader` and the timestamps are plain text.
- **Transcript timeline.** The segmented transcript exposes
  `playback_time_offset_seconds`, `ad_break` segments and
  `transcript_source_duration_s`
  ([types.ts:334](../thestill/web/frontend/src/api/types.ts#L334)); the
  reader warns when the live file's duration disagrees by more than 15 s
  ([EpisodeReader.tsx:877](../thestill/web/frontend/src/components/EpisodeReader.tsx#L877)).
- **Now Playing sheet.** `NowPlayingScrubber` takes an optional `ticks`
  series ([NowPlayingScrubber.tsx:17](../thestill/web/frontend/src/components/NowPlayingScrubber.tsx#L17));
  the sheet builds entity ticks from mentions
  ([NowPlayingSheet.tsx:107](../thestill/web/frontend/src/components/NowPlayingSheet.tsx#L107)).
  [#72 §Non-Goals](72-now-playing-sheet.md) reserved chapters as the second
  series.
- **Post-refresh resolvers.** [#87](87-episode-platform-links.md) runs
  `PlatformLinkService.link_podcast` for every refreshed podcast
  ([refresh_service.py:151](../thestill/services/refresh_service.py#L151)),
  with a CLI backfill (`thestill link-platforms`) that shares the service
  (FM-6). Chapter fetching follows the same shape.
- **Tooling.** `ffprobe` is already required
  ([duration.py:278](../thestill/utils/duration.py#L278)) and reads ID3v2
  chapters from a truncated file head: on 2026-10-14 a 512 KB range fetch of
  The Skip Podcast's enclosure yielded both chapters with titles and
  millisecond bounds. No new dependency is needed.

## Design

### 1. Sources and precedence

One episode can have chapters from several sources. All are stored; the API
returns one *preferred* set and names its source.

| Priority | `source` | Where | Timeline | Fetch cost |
|---:|---|---|---|---|
| 1 | `podcasting20` | `<podcast:chapters url="…" type="application/json+chapters">` on the item | `net` | one small JSON GET per episode |
| 2 | `id3` | `CHAP`/`CTOC` frames in the enclosure's ID3v2 header | `served` | one Range GET of the file head |
| 3 | `psc` | `<psc:chapters>` (Podlove Simple Chapters) inline on the item | `net` | none |
| 4 | `show_notes` | timestamp lines in `description_html` / `description` | `net` | none |

Precedence favours structured data over parsed prose. A set is only preferred
if it has at least two chapters; a one-chapter set never beats a four-line
show-notes list. Precedence is evaluated at read time, not stored, so adding
a source later re-ranks without a backfill.

`timeline` is a property of the source, recorded per set:

- `net` — times are relative to the publisher's master, i.e. without
  dynamically inserted ads. Hand-typed lists and static JSON cannot know what
  a given download received.
- `served` — times are relative to the specific file the fetch received. For
  `id3` the set also records `served_content_length` and the ID3-reported
  duration when present, so a later comparison knows which fill it describes.

Rows must say *which copy* they describe or they are worthless for alignment,
so a `served` set without a content length is a bug, not a degraded row.

**Ad markers, not chapters.** The one ID3 set found in the survey is not a
table of contents: The Skip Podcast's enclosure carries two frames,
"Jagjit's Finishing Edit" from 0 s and "Insert Mid-roll ad here" from
1875 s to the end, i.e. the editor's cue points left in the file. On
2026-10-14 that cue sat inside the ad run our cleaner tagged at
1858–1952 s, so it is a precise slot marker that agrees with our tagging.
Each set therefore carries a `role`:

- `toc` — a listener-facing list (default).
- `ad_markers` — at least half of the titles match
  `\b(ad|ads|advert\w*|mid-?roll|pre-?roll|post-?roll|sponsor\w*)\b`
  (case-insensitive), or the set has at most two chapters and one of them
  matches.

`ad_markers` sets are never preferred for display, are excluded from the
reader list and the tick row, and are kept because a host-written slot
marker is the most exact ad-position signal available to the drift
follow-up. The classification is read-time logic over stored titles, so a
pattern change needs no backfill.

### 2. Data model

```sql
CREATE TABLE IF NOT EXISTS episode_chapter_sets (
    id                    INTEGER PRIMARY KEY,
    episode_id            TEXT NOT NULL REFERENCES episodes(id) ON DELETE CASCADE,
    source                TEXT NOT NULL,          -- podcasting20 | id3 | psc | show_notes
    timeline              TEXT NOT NULL,          -- net | served
    status                TEXT NOT NULL,          -- found | not_found | error
    source_url            TEXT,                   -- chapters JSON url, or the enclosure url fetched
    served_content_length INTEGER,                -- id3 only: Content-Length of the fetched copy
    served_duration_s     REAL,                   -- id3 only: TLEN / last CHAP end when present
    parser_version        TEXT NOT NULL,          -- bump to force re-parse (show_notes grammar)
    role                  TEXT NOT NULL DEFAULT 'toc',  -- toc | ad_markers (§1), recomputed on write
    error                 TEXT,                   -- status = error only; classified, never raw bodies
    checked_at            TEXT NOT NULL,          -- ISO-8601 +00:00 (never CURRENT_TIMESTAMP)
    created_at            TEXT NOT NULL,
    UNIQUE (episode_id, source)
);

CREATE TABLE IF NOT EXISTS episode_chapters (
    id            INTEGER PRIMARY KEY,
    set_id        INTEGER NOT NULL REFERENCES episode_chapter_sets(id) ON DELETE CASCADE,
    position      INTEGER NOT NULL,               -- 0-based, by start_s
    start_s       REAL NOT NULL,
    end_s         REAL,                           -- NULL = until next chapter / episode end
    title         TEXT NOT NULL,                  -- ≤ 200 chars, whitespace-collapsed
    url           TEXT,                           -- http(s) only, validated
    image_url     TEXT,                           -- stored, not rendered (see §8)
    in_toc        INTEGER NOT NULL DEFAULT 1,     -- Podcasting 2.0 toc=false → 0
    UNIQUE (set_id, position)
);
CREATE INDEX IF NOT EXISTS idx_chapter_sets_episode ON episode_chapter_sets(episode_id);
```

One `set` row per `(episode, source)` is written on every check, including
`not_found`, so the resolver knows what it already looked at. Re-checks
replace the set's chapters atomically (delete + insert in one transaction),
never append. Both backends get identical DDL: the SQLite bootstrap and
`SCHEMA_SQL` in [postgres_schema.py](../thestill/repositories/postgres_schema.py),
plus Alembic `0015_episode_chapters`.

Repository surface (both backends, contract-tested together):

```python
upsert_chapter_set(episode_id, source, *, timeline, status, chapters, source_url=None,
                   served_content_length=None, served_duration_s=None, error=None) -> int
get_chapter_sets(episode_id) -> List[ChapterSet]                       # all sources
get_preferred_chapters(episode_id) -> Optional[ChapterSet]             # §1 precedence
get_preferred_chapters_for_episodes(episode_ids) -> Dict[str, ChapterSet]
get_episodes_needing_chapter_check(podcast_id=None, *, sources, recheck_before) -> List[Episode]
```

### 3. Feed stage: observe the tags

`RSSMediaSource.extract_chapter_hints(rss_content)` joins
`extract_transcript_links` and `extract_alternate_enclosures` as a third raw
XML pass over the same parsed tree (parse once, pass the root — the current
code re-parses per extractor; fold that into one parse while here). Per item
it returns:

- `podcasting20_url` + `type` from `<podcast:chapters>`;
- the inline `<psc:chapters>` list, already parsed into chapters
  (`psc:chapter start="HH:MM:SS.mmm" title="…" href="…" image="…"`).

The refresh batch writer stores `psc` sets directly (no network) and stores a
`podcasting20` set row with `status = 'pending'`-equivalent semantics: the
URL in `source_url`, no chapters, `checked_at` NULL, so the post-refresh
resolver has a worklist without re-reading the feed. As in
[#62](62-youtube-video-rendition.md), the observer covers the whole feed
window, including episodes inserted in the same batch.

Show-notes parsing (§5) also runs in the batch writer for new episodes and for
episodes whose description changed, because it needs nothing but the row.

### 4. Post-refresh resolver: `ChapterService`

One service, two callers (refresh hook and CLI), mirroring
[#87](87-episode-platform-links.md):

- `resolve_podcast(podcast, *, dry_run=False, force=False) -> ChapterReport`
  runs after `PlatformLinkService.link_podcast` in
  [refresh_service.py:151](../thestill/services/refresh_service.py#L151),
  wrapped in the same try/except so a chapter failure never fails a refresh.
- `thestill resolve-chapters [--podcast-id X] [--dry-run] [--force]
  [--source podcasting20|id3|show_notes]` for backfill and debugging, with
  the same report printed as a table.

Worklist per podcast, bounded to the refresh window (newest N episodes, same
N as #87) unless `--force`:

1. **`podcasting20`**: every set with a `source_url` and no `checked_at`, or
   `status = 'error'` older than 24 h. GET with a 10 s timeout, 1 MB body cap,
   `Accept: application/json+chapters, application/json`. Parse per the
   Podcasting 2.0 chapters spec: `chapters[]` with numeric `startTime`,
   optional `endTime`, `title`, `url`, `img`, `toc`. Drop entries with a
   non-finite or negative `startTime`; sort; dedupe equal starts (keep the
   first); cap at 500. Zero valid entries → `not_found`. Network or parse
   failure → `error` with a classified reason (`timeout`, `http_4xx`,
   `http_5xx`, `invalid_json`, `too_large`), never the body.
2. **`id3`**: every episode with an RSS enclosure (`audio_mime_type` starts
   with `audio/` and the host is not YouTube) that has no `id3` set, or whose
   `audio_url` changed since the set was written. Steps:
   - `GET` with `Range: bytes=0-65535`. If the response is `200` instead of
     `206` the host ignores ranges; read at most the cap and continue.
   - If the body does not start with `ID3`, write `not_found` and stop.
   - Decode the synchsafe tag size from bytes 6–9. If the tag exceeds 64 KB,
     fetch the remainder up to a **4 MB cap** (the survey saw a 653 KB tag on
     a show with embedded artwork). Beyond the cap → `error: tag_too_large`.
   - Run `ffprobe -v error -show_chapters -print_format json` on the head
     written to a temp file. Zero chapters → `not_found`. Chapters → `found`,
     with `served_content_length` from the `Content-Range` total (or
     `Content-Length` on a 200) and `served_duration_s` from the last
     chapter's `end_time`.
   - `not_found` is terminal for the same `audio_url`: ID3 tags do not grow
     chapters later. A changed `audio_url` re-queues.
3. **`show_notes`**: re-parse when `parser_version` differs from the
   current grammar version or the episode's description changed since
   `checked_at`.

Per-episode fetches use the shared media HTTP client with the feed
User-Agent, run inside the refresh worker pool, and are rate-limited to one
host at a time per podcast. A podcast whose host answers 403 to range
requests is not retried that run.

### 5. Show-notes grammar

`parse_show_notes_chapters(description_html, description, duration_s) ->
List[Chapter]`, pure, versioned (`parser_version = "sn-1"`), fixture-tested.

1. **Linearise.** Prefer `description_html`: replace `<br>`, `</p>`,
   `</li>`, `</div>`, `</h1..6>` with newlines, strip remaining tags, unescape
   entities, collapse runs of blank lines. Fall back to `description`.
2. **Focus.** If a line matches `^(timestamps?|chapters?|time ?codes?|in
   this episode|show notes)\s*:?\s*$` (case-insensitive), parse only the lines
   from there to the next blank-line-separated block that contains no
   timestamp. Otherwise parse the whole text.
3. **Line grammar** (leading form only in `sn-1`):

   ```text
   ^\W{0,3}(?P<h>\d{1,2}:)?(?P<m>\d{1,2}):(?P<s>\d{2})\W{0,3}\s+(?P<title>.+?)\s*$
   ```

   The survey's six formats all match: `(00:00) Introduction`,
   `(01:33) — Is open source…`, `00:00:00 — Intro`, `00:5:59 How do you…`
   (a publisher typo; minutes tolerate one digit), `00:03:16 Why Everyone…`,
   `17:00 Harry Kane`. A line that is only a time with no title is skipped.
4. **Title cleanup.** Strip leading/trailing `-–—:|•·` and whitespace,
   collapse internal whitespace, cap at 200 chars. Titles are untrusted feed
   text ([#96](96-prompt-injection-hardening.md)): rendered as text nodes
   only, never as HTML, never fed to an LLM without the fence.
5. **Accept the list** only if: at least 3 entries; strictly increasing
   times; and, when `duration_s` is known, the last start is below
   `duration_s × 1.1 + 60` (dates and scores like `3:1` are filtered by the
   seconds group, times of day like `10:30` survive the grammar but fail the
   monotonic rule in practice; the fixture set guards both).
6. **First chapter.** If the first entry starts after 0 s, do **not**
   synthesise an "Intro" chapter; the list may deliberately start at the
   interview (Football Daily's `INTERVIEWS 17:00 …`).

Rejected lists are recorded as `not_found` with the grammar version so a
grammar bump re-parses exactly the episodes that need it.

### 6. Mapping chapters onto the transcript (Phase 3)

Chapter times live on the source's timeline; the transcript lives on the
transcribed copy's timeline plus `playback_time_offset_seconds`. For a show
with no dynamic ads the two coincide and `mapped_start_s = start_s`. When
they do not, the reader must not scroll the transcript to the wrong place.

The mapping is computed server-side on read (cached with the transcript
ETag) and returned per chapter as `mapped_start_s`, `segment_id`,
`confidence`:

1. **Candidate offset.** For a `net` set, the only plausible offsets are 0
   and the cumulative durations of `ad_break` segments that precede the
   chapter in the transcribed copy (host-read ads are in the master and in
   the transcript alike, so they cancel; only the dynamically inserted ones
   shift the timeline, and the cleaner cannot tell them apart, which is why
   this is a candidate set and not a formula).
2. **Title snap.** For each candidate, take the transcript text in
   `[start_s + offset − 10 s, start_s + offset + 45 s]` and score token
   overlap with the chapter title (lower-cased, stop-words removed, numbers
   kept). Pick the candidate with the best score; `confidence` is the score
   normalised to `[0, 1]`. When every time in a list is a whole minute
   (20VC rounds its chapters to `HH:MM:00`), widen the window to
   `[−30 s, +60 s]`: the publisher's own precision is ±30 s and a tight
   window would miss correct chapters.
3. **Carry forward.** If no candidate scores above the floor (titles like
   "Intro" or "Q&A" carry no signal), reuse the previous chapter's chosen
   offset, `confidence = 0`, and anchor to the segment containing
   `start_s + offset`.
4. **`served` sets** (ID3) are compared by `served_content_length` with the
   transcribed copy's length when known; equal → offset 0 with
   `confidence = 1`; otherwise treated like a `net` set. `ad_markers` sets
   are not mapped at all; they are compared with `ad_break` segments only
   by the drift follow-up.

**Spot-check evidence (2026-10-14).** Ten show-note chapters from ten shows
were checked by reading the cleaned transcript at the stated time. Eight
matched at the stated time (Max Agency, Uncapped, Dwarkesh, How I AI, The
Knowledge Project, Netokracija, Pragmatic Engineer, What's Cooking). Two
were shifted, and in both the shift equalled the ad block the cleaner had
tagged at the start of our copy: Diary of a CEO's "A Journey With
Ayahuasca" at 69:18 begins 53 s later behind a 52 s pre-roll; 20VC's board
chapter at 1:11:00 begins 99 s later behind a 99 s block. Both shifts were
in the candidate set of step 1, so the snap would have landed them exactly.
The Knowledge Project is the counterexample that makes step 1 a candidate
set rather than a formula: 102 s of tagged ads preceded the chapter and the
correct offset was 0, because those ads are host-read and in the master
too. The two Podcasting 2.0 items (Cheeky Pint, Acquired, both generated by
Transistor and both only on the newest episode) had no transcript yet, so
their timing is unverified; their lists are specific and editorial (13 and
22 chapters).

A chapter with `confidence = 0` still seeks to `start_s` (never snapped —
the displayed time is the publisher's, as in
[#54](54-summary-segment-citations.md)) but the transcript scroll uses
`segment_id`, which is the best guess. The eval for this phase is the 131
local episodes with accepted show-notes lists and cleaned transcripts on
disk: report the share of chapters with `confidence ≥ 0.5` and spot-check
ten by hand (feedback rule from the prompt-change routine applies to the
scoring heuristic). The manual baseline above is 10 / 10 reliable, 8 at
offset 0 and 2 at a candidate offset.

Nothing in this phase writes `playback_time_offset_seconds`; the drift spec
owns that.

### 7. API

`GET /api/podcasts/{podcast_slug}/episodes/{episode_slug}/chapters`

```json
{
  "episode_id": "…",
  "source": "show_notes",
  "timeline": "net",
  "checked_at": "2026-10-14T09:12:00+00:00",
  "chapters": [
    {
      "position": 0,
      "start_s": 0,
      "end_s": 85,
      "title": "Introduction",
      "url": null,
      "in_toc": true,
      "mapped_start_s": 0,
      "segment_id": 1,
      "confidence": 1
    }
  ],
  "alternatives": [{ "source": "id3", "timeline": "served", "count": 2 }]
}
```

- `404` when the episode is unknown; `200` with `source: null,
  chapters: []` when no set is `found` (an absent feature is not an error,
  but it is also not an empty success — the body says `checked: [sources]`
  so a client can tell "not fetched yet" from "nothing published", FM-1).
- Served with the same content-hash ETag helper as the transcript.
- Episode list and detail payloads gain `chapter_count: int` so the reader
  can decide whether to render the section before the chapters request
  resolves. `end_s` is filled on read from the next chapter's start when
  the source omitted it.
- MCP: `get_episode_chapters(episode_id)` read-only tool in the default
  `read` scope, returning the same shape; `get_transcript` is unchanged.

### 8. Reader

- **Chapter list** under the header action row, above the Summary |
  Transcript tabs ([#76](76-episode-detail-page-hierarchy.md) hierarchy):
  a single-column list of `clock · title`, collapsed to the first 6 with
  "Show all N" on phones, fully expanded on desktop. Click seeks via
  `PlayerContext.seek(start_s + playback_time_offset_seconds)` and scrolls
  the transcript to `segment_id` through the existing
  `scrollToSegmentId` contract. The current chapter is highlighted from
  `currentTime`.
- **Transcript headings.** `SegmentedTranscriptViewer` accepts
  `sectionBreaks: { beforeSegmentId, title }[]` and renders a heading row
  before the anchored segment. Headings are text nodes; no HTML, no images
  (`image_url` is stored for a later spec, and not rendered now, per the
  image-render gap in [#96](96-prompt-injection-hardening.md)). Chapter
  `url` renders as an external link with `rel="noopener"` and the
  http(s)-only validation already used for show links.
- **Show notes.** The inert timestamp lines stay as they are. Rewriting
  them into links is tempting but doubles the surface; the chapter list
  directly above them is the clickable version.
- **Drift banner.** When the banner is showing, the chapter list gets a
  one-line note: "Chapter times are the publisher's and may be off by the
  same amount." No offsets are applied to chapter seeks in this spec.
- **Deep links.** `?t=` already seeks; `?chapter=N` is not added. A chapter
  link is `?t=<start_s>`.

### 9. Now Playing sheet

- **Tick series.** Chapters become a second `ScrubberTick` series with a
  distinct colour token and a slightly taller mark; label `"{title} at
  {clock}"`, `onSelect` seeks. Rendered above entity ticks so they stay
  visible when mentions are dense. The series uses `mapped_start_s` when the
  active track is the transcribed timeline, `start_s` otherwise (today's
  tracks are always publisher audio, so `start_s`).
- **Current chapter** title under the episode title, replacing nothing,
  hidden when there are no chapters.
- **Prev / next chapter** are *not* added to the ±15 s controls or the
  Media Session handlers in this spec: on iOS registering
  `previoustrack`/`nexttrack` can replace the skip buttons
  ([#93 §4.10](93-native-web-capabilities.md)). Open question 3.

### 10. Logging and failure modes

Checked against [#42](42-robustness-and-failure-mode-hardening.md):

- **Errors are not empty results.** `not_found` and `error` are distinct
  statuses with distinct retry rules; a timeout never writes an empty
  chapter set (FM-1).
- **One code path.** Refresh hook and CLI call the same `ChapterService`;
  the show-notes parser is one pure function used by the batch writer and
  the backfill (FM-6).
- **Untrusted input.** Feed titles, JSON bodies and ID3 frames are bounds-
  checked: title length, chapter count, body size, tag size, `start_s`
  range. ffprobe runs on a temp file with a 10 s timeout (FM-7).
- **Timestamps** in `checked_at`/`created_at` are ISO-8601 with `+00:00`
  on both backends.
- Events: `chapters_resolved` (`episode_id`, `source`, `status`, `count`,
  `elapsed_ms`), `chapters_parse_rejected` (`episode_id`, `reason`), and a
  per-run `chapters_report` with counts by source and status.

## Edge cases

- **Several sources disagree** (show notes say 12 chapters, JSON says 4):
  precedence wins, the API lists the others under `alternatives`, and the
  CLI report flags disagreements over 2× for a human look.
- **Chapters past the end** (publisher typo `1:02:00` on a 40-minute
  show): the acceptance rule in §5 rejects the whole list rather than
  dropping one line, because a wrong list is worse than none; the reject
  reason is logged.
- **YouTube-sourced episodes** have no enclosure to range-fetch; `id3` is
  skipped. YouTube's own chapter markers in the description parse through
  §5 unchanged.
- **Hosts that answer 200 to Range** stream the full file; the reader
  stops at the cap and still finds the tag, which lives at the start.
- **Enclosure URL rotates per refresh** (signed CDN URLs): the "changed
  `audio_url`" re-queue rule would refetch every run. Compare the URL with
  query string stripped; if the path still changes every run, mark the
  podcast `id3_unstable` after three consecutive refetches and stop.
- **Description edited after publication** (a common fix for a typo in the
  timestamps): the batch writer re-parses when the description hash
  changes, so the list follows the correction.
- **ID3 frames that are cue points** ("Insert Mid-roll ad here"): classified
  `ad_markers` by §1 and hidden from every listener surface; a show whose
  only chapter data is cue points renders as having no chapters, which is
  correct. The marker still lands in the table for the drift work.
- **Chapters set `toc: false`** (Podcasting 2.0 lets a publisher hide a
  chapter from the list while keeping its image/link window): stored with
  `in_toc = 0`, omitted from the reader list and the tick row.

## Non-goals

- Writing or editing chapters; user-authored chapters are a feedback-loop
  feature for [#86](86-quality-feedback-loop.md).
- Rendering chapter images.
- Media Session `chapterInfo` (Chrome-only, experimental; #93 verdict).
- Chapter-aware summarisation. Feeding chapter titles to the summariser is
  plausible but it is an LLM prompt change and needs the ten-episode A/B
  routine; it gets its own small spec if wanted.
- Solving timestamp drift. This spec records which timeline every chapter
  set is on; the alignment algorithm (landmark fingerprints or forced
  alignment of the transcript against the live file) is the next spec.
- Inferring chapters from the transcript when the publisher provides none.

## Phases

| Phase | Scope | Gate |
|---|---|---|
| 0 | Data model (both backends + Alembic 0015), show-notes grammar `sn-1` with fixtures from the six observed formats, batch-writer hook, `thestill resolve-chapters --source show_notes` backfill, API endpoint | 118 local episodes parse; zero false positives on a 50-episode no-timestamp sample |
| 1 | Feed observer for `<podcast:chapters>` and `<psc:chapters>`; `ChapterService` with the JSON fetch and the ID3 range fetch + ffprobe; refresh hook; CLI `--source` for all | Acquired / Cheeky Pint resolve from JSON; The Skip Podcast resolves from ID3 with `served_content_length` set; a 403-on-Range host logs once and does not block the refresh |
| 2 | Reader chapter list + transcript section headings; Now Playing tick series + current chapter | Phone fold e2e stays under the 732 px budget; Back restores filters and scroll per the list-page rule |
| 3 | Title-snap mapping (§6) with `mapped_start_s` / `segment_id` / `confidence`; eval report over the local timestamped episodes | ≥ 70 % of chapters at `confidence ≥ 0.5` on the eval set, ten hand-checked; the two known shifted cases (Diary of a CEO 69:18, 20VC 1:11:00) resolve to their ad-block offsets |

Phases 0–2 ship behind no flag: an episode without chapters renders exactly
as today.

## Testing

- **Unit.** `parse_show_notes_chapters` against a fixture directory with one
  file per observed format plus negatives (dates, scores, times of day, a
  single timestamp, non-monotonic lists, a list past the duration). Podcasting
  2.0 JSON fixtures: valid, `toc: false`, negative starts, 1 000 entries,
  non-JSON. ID3 fixtures generated with `ffmpeg -i silence.wav -i
  chapters.txt -map_metadata 1` so `ffprobe` is exercised for real, including
  a truncated head and a tag over the cap.
- **Repository contract tests** for both backends (`TEST_DATABASE_URL`
  path) covering upsert-replaces, precedence, cascade on episode delete.
- **Resolver tests** with a stubbed HTTP client: 206 vs 200, no `ID3`
  magic, timeout → `error` not `not_found`, re-queue on URL change, stable
  under rotating query strings.
- **API** snapshot for the response shape including the empty-but-checked
  case.
- **Frontend.** Chapter list render + seek + scroll contract; tick series
  ordering above entities; `in_toc = false` hidden; phone fold e2e.
- **Eval (Phase 3).** Script under `evals/` that prints confidence buckets
  and the ten spot-check episodes; the ten from the 2026-10-14 manual check
  (§6) are the seed set, with their expected offsets as assertions.
- **Ad-marker classification.** Fixtures: The Skip Podcast's two-frame set
  → `ad_markers`; a 12-chapter list with one "Sponsor break" entry → `toc`;
  a three-chapter list titled "Intro / Ad / Outro" → `ad_markers`.

## Open questions

1. **Trailing-time format** (`Harry Kane — 17:00`) was not seen in the
   survey. Add to `sn-2` only if a followed show uses it; the grammar version
   mechanism makes that a cheap re-parse.
2. **Should the chapter list live in the Summary tab instead of above the
   tabs?** Above the tabs keeps it visible on both; the fold budget decides.
3. **Prev / next chapter controls.** Device-test the Media Session
   behaviour on iOS before deciding whether chapter skip replaces or joins
   ±15 s.
4. **Recording the transcribed copy's `Content-Length`** at download time
   would make the `served` comparison in §6 step 4 exact for Dalston-URL
   episodes too; it is a one-column addition to the download stage and
   belongs here or in the drift spec, whichever lands first.

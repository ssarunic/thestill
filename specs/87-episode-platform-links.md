# Episode Platform Links

> **Status:** 🚧 Phases 1–2 shipped in v1.11.0 (2026-09-29); Phase 3 in progress: core-title rule + Spotify latest-episode probe implemented, curation + verified channel discovery open
> **Created:** 2026-09-28
> **Author:** Product & Engineering
> **Related:** [#62 youtube-video-rendition](62-youtube-video-rendition.md), [#65 apple-deep-history-import](65-apple-deep-history-import.md), [#73 mobile-list-row-density](73-mobile-list-row-density.md), [#76 episode-detail-page-hierarchy](76-episode-detail-page-hierarchy.md), [#79 spotify-link-import](79-spotify-link-import.md)

---

## Executive Summary

Most shows publish every episode on Apple Podcasts, Spotify and YouTube, but
the episode page only links to whatever the publisher put in the RSS item's
`<link>` ("Show notes"), and only half of the episodes carry one. Show-level
Apple and YouTube links exist ([#73](73-mobile-list-row-density.md) copies them from the
chart row), and a handful of feeds emit a per-episode YouTube alternate
enclosure ([#62](62-youtube-video-rendition.md)). Nothing resolves a feed
episode to its Apple, Spotify or YouTube counterpart.

This spec adds a per-episode `episode_platform_links` table and one resolver
per platform, run once per show after a refresh. Apple comes from the
iTunes lookup already used by the import path ([#65](65-apple-deep-history-import.md)):
a show's newest 200 episodes with the feed GUID and the Apple episode page,
so one request per show links every recent episode by exact GUID. Spotify
is **publisher-provided only** (the developer program closed to small apps
in 2026 and the pages no longer expose episode lists). YouTube takes the
publisher's links first, then one flat `yt-dlp` listing of the show's
channel matched on title, date and duration. The Information list on the
episode page gains a "Listen on" row; the podcast page gains a Spotify
row when the publisher states one.

Local numbers on 2026-09-28 (98 podcasts, 6807 episodes): 3374 episodes have
a "Show notes" link, 34 have a YouTube alternate enclosure, 68 podcasts have
show-level Apple and YouTube links, none has a Spotify link.

## Design

### Storage

One table, one row per (episode, platform). A row with `url IS NULL` records
"checked, not found" so the resolver does not re-query every refresh.

```sql
CREATE TABLE episode_platform_links (
    id            identity PRIMARY KEY,
    episode_id    uuid NOT NULL REFERENCES episodes(id) ON DELETE CASCADE,
    platform      text NOT NULL CHECK (platform IN ('apple', 'spotify', 'youtube')),
    url           text NULL,          -- NULL = checked, not found
    external_ref  text NULL,          -- Apple trackId, Spotify episode id, YouTube video id
    match_method  text NULL CHECK (match_method IN ('guid', 'audio_url', 'title_date', 'title_duration', 'publisher')),
    checked_at    timestamptz NOT NULL,
    created_at    timestamptz NOT NULL DEFAULT now(),
    UNIQUE (episode_id, platform)
);
```

`match_method` and `external_ref` make every link explainable and
revertible: a wrong fuzzy link can be found by method and cleared. The
upsert keeps an existing `url` when the incoming row has none, so a
not-found pass can never blank a link. `podcasts.spotify_url` (same
migration) holds the publisher-stated Spotify show link; unlike
`apple_url` / `youtube_url` it is never chart-sourced.

### Candidates and throttle

For a podcast and a platform, the candidates are the episodes among the
show's **newest `window` episodes by `pub_date`** (200, Apple's hard cap)
that have no link for the platform and whose not-found row, if any, is older
than `recheck_before`. The window is taken before filtering so an old episode
that Apple never indexed does not keep the show in the candidate set forever.

- No candidates → no HTTP call. This is the common case after the first pass.
- Candidates → exactly one lookup per show, then found rows and not-found
  rows for every candidate, all stamped `checked_at = now`.
- A lookup error writes nothing; the next refresh retries.

With `PLATFORM_LINKS_RECHECK_HOURS=24` the worst case is one request per
show per day for shows that have unindexed episodes inside the window.

### Apple resolver (Phase 1)

1. **Show id.** `podcasts.apple_url` (chart-sourced, [#73](73-mobile-list-row-density.md))
   gives the collection id. When it is empty, one iTunes search by show
   title (`media=podcast`) is run. A result is accepted outright when its
   `feedUrl` equals the podcast's `rss_url` after normalisation (scheme and
   trailing slash ignored). Otherwise a **unique** result with the same
   normalised title is accepted provisionally, and only kept if its episode
   window (step 2) yields at least one exact GUID or enclosure match, so a
   namesake show can never link; title-date matches are not proof. The
   fallback exists because the same show is often listed under another feed
   host (megaphone vs anchor, `feeds.` vs `rss.buzzsprout`, flightcast vs
   substack): three of the 71 local shows on 2026-09-28. An accepted show
   stores `collectionViewUrl` (tracking query stripped) as `apple_url`,
   which also fills the show-level link on the podcast page. No acceptable
   result writes not-found rows for the candidates, so the search repeats
   at most once per recheck interval.
2. **Window.** `itunes.apple.com/lookup?id=<collectionId>&entity=podcastEpisode&limit=200`.
   Same endpoint, user agent and retry policy as the import resolvers
   ([spotify_resolver.py](../thestill/core/spotify_resolver.py) `itunes_lookup`).
3. **Match.** Exact identifiers for every candidate first, titles only for
   what is left: candidates arrive newest first, and a same-title
   neighbour Apple has not indexed yet must not take, by title, the entry
   that belongs by GUID to the episode after it. Per candidate:
   - `guid`: `episodeGuid` equals `external_id`.
   - `audio_url`: `episodeUrl` equals `audio_url`, exact or with query
     string dropped and scheme normalised.
   - `title_date`: normalised titles equal (case, punctuation and whitespace
     folded) and `releaseDate` within 36 hours of `pub_date`. Skipped when
     the title is ambiguous inside the window.
   Each Apple entry links at most one episode. The stored `url` is Apple's
   `trackViewUrl`; `external_ref` is `trackId`.

### Publisher-provided links (`publisher`)

Per episode, in order of trust: the import that created it (`canonical_id`
`spotify:<id>` / `youtube:<video id>`), a `video/youtube` alternate
enclosure ([#62](62-youtube-video-rendition.md)) and the item `<link>` when
it is a Spotify episode or YouTube video are taken as-is: each identifies
this episode by construction. A link in the description is only a
**claim**, and only when the description names exactly one episode / video
on that platform: show notes routinely link last week's episode or a
guest's video, so uniqueness narrows the field but does not establish
identity. The claim is checked against the linked item's own title, date
and duration (the channel listing entry when the video is on the show's
channel, else one fetch of the video; for Spotify one fetch of the episode
page, the [#79](79-spotify-link-import.md) scraper): the YouTube title
rules below, or, because the publisher vouched for the pairing, date
within tolerance plus agreeing durations alone — that accepts an upload
retitled for YouTube and still rejects a bite-size cut or a trailer. A
claim that fails, or cannot be fetched, becomes a not-found marker and is
retried after the recheck interval.

Per show: a Spotify show link or YouTube channel link in the podcast's own
description / website is taken when unique; the same link in at least
three episode descriptions is taken when no other competes. A discovered
YouTube channel fills `youtube_url`, so the podcast page and the channel
scan below share it.

### Spotify

Publisher-provided links, plus a **latest-episode probe** (Phase 3).
Verified 2026-09-28: Spotify's Web API is not retired but Development Mode
now needs Premium, is capped at 5 users and extended quota requires a
registered business with 250k monthly actives; the show page serves only
Open Graph tags, and Spotify for Podcasters links redirect to a
client-rendered page with no episode id. There is no public listing of a
show's episodes.

What the show's **embed** page still ships is its newest episode: Spotify
id, exact title, release date and duration, served to non-browser user
agents ([spotify_show_probe.py](../thestill/core/spotify_show_probe.py)).
With the show id on `podcasts.spotify_url` (publisher-stated or curated,
below) each pass makes one request and links the one unresolved candidate
the probe describes, through the same title / date / duration rules as
YouTube. Going forward that links nearly every new episode at
publication; the limits are no backfill of older episodes and shows that
publish faster than the refresh cadence polls. Older episodes stay on the
feed's own links, and the row simply does not show otherwise.

### YouTube resolver (Phase 2)

1. **Channel.** `podcasts.youtube_url` (chart-sourced or publisher-stated,
   above). No search fallback: a title search would link namesake
   channels, and no window can prove a channel the way a GUID proves an
   Apple show.
2. **Listing.** One flat `yt-dlp` pass over `<channel>/videos` (newest 300,
   no shorts, no downloads, ~0.5 s). Entries carry id, title, duration and
   an **approximate** upload date ("3 weeks ago"), so the date tolerance
   widens with age: 3 days inside three weeks, 14 days inside eight weeks,
   45 days beyond. The exact duration carries the precision the date loses.
3. **Match**, unique-or-nothing per candidate, each upload links once:
   - `title_date`: normalised titles equal — or their **cores** equal, the
     title with each side's decoration removed: `#NNN` / `Ep NNN` tags and
     the show's own name (with and without a leading "The"). "#502 –
     Psychiatry, Insane Asylums" and "Psychiatry, Insane Asylums | Lex
     Fridman Podcast #502" share a core, so they link even when the feed
     omits the duration (Phase 3). Date within tolerance; when both
     durations are known they must agree (20 %, floor 180 s) — the same
     title at a clearly different length is a clip.
   - `title_duration`: date within tolerance, durations known and agreeing,
     and either one title contains the other (shorter side ≥ 12 chars:
     "#2519 - Scott Eastwood" inside "Joe Rogan Experience #2519 - Scott
     Eastwood") or both carry the same single `#NNN` episode number
     ("#502 – Guest: Topic" vs "Topic | Show #502").
   A listing failure (bot check, private channel) writes nothing for the
   unresolved candidates; the publisher rows are kept.

The playback manifest's YouTube rendition ([#62](62-youtube-video-rendition.md))
still reads alternate enclosures only; feeding it from `publisher` /
`title_date` links is a follow-up once the links have been eyeballed.

### Hooks

- **Queued refresh** ([task_handlers.py](../thestill/core/task_handlers.py)
  `handle_refresh_feed`): after transcript links, best-effort, never fails
  the task. Runs on every refresh, not only when new episodes arrived,
  because Apple indexes with a delay and the throttle above makes the
  no-candidate case free.
- **Inline refresh** ([refresh_service.py](../thestill/services/refresh_service.py)):
  same call for every podcast the refresh covered (the one asked for, else
  every followed feed), whether or not it had new episodes and before the
  no-new-episodes early return, so a 304 still lets an expired not-found
  marker retry. Test doubles that build a bare state or service skip it.
- **CLI** `thestill link-platforms [--podcast-id] [--dry-run] [--force]`:
  the backfill and the debugging surface. `--force` ignores the recheck
  interval; `--dry-run` fetches and matches but writes nothing — it reads
  the show links with the read-only lookup, since the chart sync used on a
  real pass backfills `apple_url` / `youtube_url` from the chart row.

`PLATFORM_LINKS_ENABLED=false` disables the hooks and the CLI in one place.

### API and UI

`GET /api/podcasts/{podcast_slug}/episodes/{episode_slug}` gains
`platform_links: [{platform, url}]`, found rows only, ordered Apple,
Spotify, YouTube. The Information list ([#76 §3.6](76-episode-detail-page-hierarchy.md))
adds a "Listen on" row after "Show notes" with one external link per
platform. No fallback to the show-level link: a show link on an episode row
would read as the episode. `GET /api/podcasts/{slug}` gains `spotify_url`
and the podcast page a "Spotify" row beside Apple Podcasts and YouTube.

## Phases

| Phase | Scope | Status |
|-------|-------|--------|
| 1 | Table, Apple resolver, refresh hooks, CLI, API field, "Listen on" row | ✅ 2026-09-28 |
| 2 | Publisher-provided Spotify + YouTube links, `podcasts.spotify_url`, YouTube channel scan (`title_date` / `title_duration`) | ✅ 2026-09-28 |
| — | Spotify Web API resolver | Dropped: developer program closed to small apps (see "Spotify") |
| 3a | Core-title rule (show name + episode tag stripped) in the YouTube matcher and the single-item rule used for claims and the probe | ✅ 2026-09-29 |
| 3b | Spotify latest-episode probe from the show's embed page, driven by `podcasts.spotify_url` | ✅ 2026-09-29 |
| 3c | Curated show links: a `source` beside `youtube_url` / `spotify_url` (`chart`, `publisher`, `resolver`, `curated`) so a curated value is never overwritten by a chart scrape or a resolver guess; an edit field on the podcast page; ordered by follows, not chart rank (the 39 followed shows without a channel first) | Open |
| 3d | Verified YouTube channel discovery: search by show title, accept a channel only when several of the show's episodes match its uploads on core title and duration — the namesake guard the linker already uses. The chart already carries a channel for 5254 of 7702 charted shows; discovery is for the rest and for off-chart shows | Open |
| 3e | Feed the #62 playback rendition from `youtube` links once they have been eyeballed in production | Open |

## Configuration

| Variable | Description | Default |
|----------|-------------|---------|
| `PLATFORM_LINKS_ENABLED` | Resolve per-episode platform links after refresh and via the CLI | `true` |
| `PLATFORM_LINKS_RECHECK_HOURS` | How long a not-found row suppresses another lookup for that episode | `24` |

## Testing

- Matcher unit tests: each method, ambiguity skip, one-entry-one-episode.
- Service tests with a fake repository and fake iTunes fetchers: no
  candidates → no call; show search accepted on feed URL equality, or on a
  unique title hit proven by the window (an unproven hit stores nothing);
  lookup error writes nothing; dry run writes nothing; force ignores the
  throttle.
- Dual-backend repository contract tests (SQLite and Postgres via
  `TEST_DATABASE_URL`): candidate window, recheck filter, upsert keeps a
  found URL, cascade delete.
- Handler test: the hook never fails the refresh task.
- API and frontend tests for the new field and row.

## Dry run, 2026-09-28

Against the local Postgres database (98 podcasts, 14 235 candidates across
the three platforms):

| Platform | Linked | By method |
|----------|--------|-----------|
| Apple | 4562 of ~4750 | almost all `guid`; `audio_url` for the Goalhanger shows (their GUIDs differ from Apple's), `title_date` for No Such Thing As A Fish and Shameless |
| Spotify | 3 | `publisher` — 21 further description links turned out to be other episodes (a show's bite-size cuts linking their full episode) and were refused once claims were verified |
| YouTube | 943 | 97 `publisher` (34 of them retitled uploads accepted on date + duration), 508 `title_date`, 338 `title_duration` |

Apple's misses are episodes Apple does not list (the Ezra Klein Show
window holds 4 entries; Football Daily's 5 Live reaction episodes are
absent). Three shows (Uncapped, Deep Learning with PolyAI, Latent Space)
resolve only through the window-proven title rule because Apple lists them
under another feed host. YouTube: 39 shows have no channel on record;
of the shows with one, video-first feeds land well (Joe Rogan 33 of 47,
Prof G Markets 119 of 189, Moonshots 50 of 86, Dwarkesh 27 of 35,
Huberman 17 of 32) and the misses are retitled uploads the publisher did
not link ("How Meta Could Quietly Win The AI Race" vs "Meta Just Turned
Your Data Into An AI Advantage") that no safe rule covers. Spot checks of
the paired titles and durations found no wrong link; the rejected
description claims inspected were a bite-size cut, a trailer and a
guest's own video.

## Open questions

- Episodes older than the newest 200 stay unlinked on Apple. The import
  path's page-scrape fallback (#65 Tier 3) works per episode and is not
  worth one request per old episode here.
- 39 shows have no YouTube channel on record: Phase 3c/3d above.
- Spotify show ids have no discovery shortcut (no directory carries them),
  so 3c's curation is the only way to reach the probe at scale; a public
  dataset would be worth a look before hand-curating 500 rows.
- YouTube's approximate dates make the 45-day band the weak point; exact
  per-video dates cost one request per video and are not worth it.
- Whether the list endpoint should carry the links too (a per-row "Listen
  on" is not planned, so not yet).

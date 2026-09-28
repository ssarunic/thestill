# Episode Platform Links

> **Status:** 🚧 Phase 1 (Apple Podcasts) implemented 2026-09-28; Spotify and YouTube phases open
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
per platform, run once per show after a refresh. Phase 1 ships Apple: the
iTunes lookup already used by the import path ([#65](65-apple-deep-history-import.md))
returns a show's newest 200 episodes with the feed GUID and the Apple
episode page, so one request per show links every recent episode by exact
GUID. The Information list on the episode page gains a "Listen on" row.

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
    match_method  text NULL CHECK (match_method IN ('guid', 'audio_url', 'title_date')),
    checked_at    timestamptz NOT NULL,
    created_at    timestamptz NOT NULL DEFAULT now(),
    UNIQUE (episode_id, platform)
);
```

`match_method` and `external_ref` make every link explainable and
revertible: a wrong fuzzy link can be found by method and cleared. The
upsert keeps an existing `url` when the incoming row has none, so a
not-found pass can never blank a link.

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
3. **Match**, first method that hits, per candidate:
   - `guid`: `episodeGuid` equals `external_id`.
   - `audio_url`: `episodeUrl` equals `audio_url`, exact or with query
     string dropped and scheme normalised.
   - `title_date`: normalised titles equal (case, punctuation and whitespace
     folded) and `releaseDate` within 36 hours of `pub_date`. Skipped when
     the title is ambiguous inside the window.
   Each Apple entry links at most one episode. The stored `url` is Apple's
   `trackViewUrl`; `external_ref` is `trackId`.

### Hooks

- **Queued refresh** ([task_handlers.py](../thestill/core/task_handlers.py)
  `handle_refresh_feed`): after transcript links, best-effort, never fails
  the task. Runs on every refresh, not only when new episodes arrived,
  because Apple indexes with a delay and the throttle above makes the
  no-candidate case free.
- **Inline refresh** ([refresh_service.py](../thestill/services/refresh_service.py)):
  same call for each refreshed podcast when a `PlatformLinkService` is wired
  in. Test doubles that build a bare state or service skip it.
- **CLI** `thestill link-platforms [--podcast-id] [--dry-run] [--force]`:
  the backfill and the debugging surface. `--force` ignores the recheck
  interval; `--dry-run` fetches and matches but writes nothing.

`PLATFORM_LINKS_ENABLED=false` disables the hooks and the CLI in one place.

### API and UI

`GET /api/podcasts/{podcast_slug}/episodes/{episode_slug}` gains
`platform_links: [{platform, url}]`, found rows only, ordered Apple,
Spotify, YouTube. The Information list ([#76 §3.6](76-episode-detail-page-hierarchy.md))
adds a "Listen on" row after "Show notes" with one external link per
platform. No fallback to the show-level link: a show link on an episode row
would read as the episode.

## Phases

| Phase | Scope | Status |
|-------|-------|--------|
| 1 | Table, Apple resolver, refresh hooks, CLI, API field, "Listen on" row | ✅ 2026-09-28 |
| 2 | Spotify: show id via search, `GET /shows/{id}/episodes` paged, title/date/duration scorer from [#79](79-spotify-link-import.md); needs `SPOTIFY_CLIENT_ID`/`SECRET` | Open |
| 3 | YouTube: alternate enclosures ([#62](62-youtube-video-rendition.md)) first, opt-in channel scan by title; clips make title matching noisy | Open |

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

Against a scratch copy of the local SQLite database (71 podcasts), the
first pass would link 2737 of 2859 candidates (95.7%), almost all by exact
GUID; `audio_url` carried the Goalhanger shows (their GUIDs differ from
Apple's) and `title_date` carried No Such Thing As A Fish. Three shows
(Uncapped, Deep Learning with PolyAI, Latent Space) only resolve through
the window-proven title rule, since Apple lists them under another feed
host; together they contribute 120 of those links. The remaining misses
are episodes Apple does not list at all (the Ezra Klein Show window holds
4 entries; Football Daily's 5 Live reaction episodes are absent), which the
recheck interval retries at one request per show per day.

## Open questions

- Episodes older than the newest 200 stay unlinked on Apple. The import
  path's page-scrape fallback (#65 Tier 3) works per episode and is not
  worth one request per old episode here.
- Whether the list endpoint should carry the links too (a per-row "Listen
  on" is not planned, so not yet).

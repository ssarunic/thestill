# Episode Platform Links

> **Status:** 🚧 Phases 1–2 shipped in v1.11.0 (2026-09-29); Phase 3 in progress: core-title rule, Spotify latest-episode probe, website-scan discovery, curated show links and the 3f resolver follow-ups implemented; verified channel discovery (3d) in progress; edit field open
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

**Young recheck** (Phase 3f). An episode whose `pub_date` is inside the
last `PLATFORM_LINKS_YOUNG_AGE_HOURS` (48) is a candidate again after
`PLATFORM_LINKS_YOUNG_RECHECK_HOURS` (2) instead of the full interval.
The a16z case showed why: the first pass after publication ran before
Apple had indexed the episode and before Spotify's embed page listed it,
and the 24-hour marker then outlived the episode's turn as the show's
newest — which is the only turn the Spotify probe ever sees. Set the age
to `0`, or the young interval no shorter than the full one, to turn it off.

With `PLATFORM_LINKS_RECHECK_HOURS=24` the worst case is one request per
show per day for shows that have unindexed episodes inside the window,
plus one every two hours per platform for the two days after each new
episode.

### Apple resolver (Phase 1)

1. **Show id.** `podcasts.apple_url` (chart-sourced, [#73](73-mobile-list-row-density.md))
   gives the collection id. When it is empty, the Apple show page the
   publisher links in its own text (podcast description, or the same page
   in at least three episode descriptions on the share rule below) is tried
   first, provisionally: its window must yield an exact GUID or enclosure
   match, and then the link is stored with source `publisher`. Otherwise
   one iTunes search by show title (`media=podcast`) is run. A result is accepted outright when its
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
description / website URL is taken when unique; then (Phase 3c) the
publisher's **website page** — its "listen on" buttons, fetched once per
pass through the SSRF guard and only while a platform is still missing —
on the same terms; then the same link in at least three episode
descriptions when it holds at least 80 % of that platform's votes
(`DOMINANT_SHARE`). The share rule replaced strict exclusivity in Phase
3f: a16z's footer names `@a16z` in all 123 stored episodes while three
episodes also link a guest's channel, which under exclusivity left the
show with no channel at all. A source that names two different shows or
channels (a network site listing all its shows), or votes split between
two, is left for a human, and the channels hosting platforms stamp on
every site (Anchor pages link Spotify's own "for creators" channel) never
count. The same harvest now yields the Apple show page for the Apple
resolver above. Measured on
the 40 most-followed shows with a website that lacked a link: the scan
fills the Spotify id for 18 of 40 and the YouTube channel for 5 of the 17
without one; 6 sites could not be fetched (bot walls, dead domains, a
bare hostname stored as the URL). A discovered link fills `spotify_url` /
`youtube_url` with source `publisher`, so the podcast page, the probe and
the channel scan share it.

### Where a show link came from (Phase 3c)

`podcasts.{apple,youtube,spotify}_url_source` is `chart`, `publisher`,
`resolver` or `curated` (migration 0013; existing links backfilled as
`chart` when they equal the chart row, else `publisher`). The chart sync
stamps `chart` and the resolvers stamp their source, but a `curated` value
only yields to another curated write: `thestill set-show-link --podcast-id
X --platform spotify --url …` (or `--clear`). That is the homework surface
for now — an edit field on the podcast page is the remaining 3c item — and
it is what makes Spotify's probe reachable for shows whose feed and site
say nothing.

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

1. **Channel.** `podcasts.youtube_url` (chart-sourced, publisher-stated or
   curated, above). When it is empty, or is a chart channel that matches
   none of the show's episodes, Phase 3d's verified discovery (below)
   searches for one. A search result is never taken on its name: a
   namesake channel has to match several of the show's own episodes, the
   way a GUID proves an Apple show.
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
   - `title_words` (stored as `title_duration`, PR #382): for uploads
     retitled for YouTube. Date within tolerance and durations agreeing,
     plus half the shorter title's content words shared (at least 3), or
     lengths within 30 s inside 3 days with at least 2 shared words.
     Length and date alone are never enough. Runs last, so it cannot take
     an upload another episode matches more strictly.
   Members-only uploads (yt-dlp `availability: subscriber_only`, the
   "AD FREE" / "MEMBERS' EARLY ACCESS" cuts) are dropped from the listing.
   A listing failure (bot check, private channel) writes nothing for the
   unresolved candidates; the publisher rows are kept.

### YouTube channel discovery (Phase 3d)

**Why.** On 2026-10-19, 24 of the 48 shows with an episode in the last
30 days had no YouTube link at all. Fifteen had no channel on record
(Prof G Pod, Latent Space, How I AI, Deep Questions, …) and three had a
wrong one from the chart (The Times Tech Podcast → `@NYTPodcasts`,
Explora → `@GstaadGuyPodcast`) or the publisher scan (Behind the Craft →
`@RasMic`). The chart carries a channel for 5254 of 7702 charted shows;
discovery is for the rest and for off-chart shows.

**Flow.** Inside the YouTube half of `link_podcast`, before the channel
listing, when the show has unresolved YouTube candidates and either

- no channel on record, or
- a **chart-sourced** channel (never `publisher` or `curated`) that is
  demonstrably wrong: its listing succeeded and matched **none** of the
  verification sample (below, at least 8 episodes), and the show has no
  YouTube link made by a channel listing (`title_date` /
  `title_duration`). Publisher links (import, alternate enclosure, item
  link, verified claim) identify the episode without any channel, so
  they are not evidence for the channel and never block a replacement,

and discovery has not run for this show in the last
`PLATFORM_LINKS_YOUTUBE_DISCOVERY_DAYS` (7):

0. **Verification sample.** The show's newest 20 episodes with a
   `pub_date`, read regardless of link state or not-found markers. The
   pass's candidates only decide *whether* discovery runs: in steady
   state they are the one or two episodes whose markers just expired,
   too few to reach the threshold, and a failed attempt would then wait
   a week.
1. **Search.** One channel-filtered YouTube search by show title (the
   results page with the channels filter, through yt-dlp, flat), top 5
   results. Generic platform channels and the channel already on record
   are dropped.
2. **Verify.** List each result's uploads (150, same flat listing) and run
   the episode matcher against the sample. Each result ends **verified**
   with a match count, or **unverified**. A channel without a `/videos`
   tab (auto-generated "Topic" channels, shorts-only clip channels) is
   verified with 0 matches: yt-dlp states it definitively. Any other
   listing failure (bot check, network, unavailable channel) is
   unverified, never a zero.
3. **Accept** the best channel only when every result in the set was
   verified, the best matches **at least 3** episodes, and the runner-up
   matches **at most a third** as many. An unverified result defers the
   attempt: it could be the real channel, and scoring it 0 would let a
   cross-posting channel pass the runner-up guard and be stored as
   `resolver`. A publisher-run channel posting several shows (Stripe for
   Cheeky Pint, SVPG for Product Therapy) is accepted the same way.
4. **Fallback query.** When nothing is accepted and the show has an
   `author` the title does not contain, search once more with
   "title author" ("Possible" alone finds Project Possible and Possible
   History; "Possible Reid Hoffman" finds the right channel).
5. **Store** the accepted channel as `youtube_url` with source `resolver`
   and continue the pass with it: the listing and matching below run on
   the new channel in the same pass. The chart sync no longer overwrites a
   `resolver` YouTube channel (it already yielded to `curated`), or a
   wrong chart channel would come back on the next refresh.
6. **Stamp** `podcasts.youtube_discovery_checked_at` whether a channel
   was accepted, rejected or deferred. A search failure (bot check,
   network) also stamps, so a blocked box does not search every refresh;
   the cost is a week's delay for a show whose attempt was deferred.

One attempt costs one or two searches plus up to ten flat listings,
once a week per show at most. `--force` on `link-platforms` ignores the
weekly stamp; `--dry-run` searches and verifies, uses the channel for the
rest of the pass, and stores neither the channel nor the stamp.
`PLATFORM_LINKS_YOUTUBE_DISCOVERY_DAYS=0` turns discovery off.

**Prototype, 2026-10-19** (public episodes, live channel listings, the
PR #382 matcher): of the 15 shows without a channel, 11 have a clear winner
on the title search (4–14 matches, every rival 0–1), and the author query
adds Possible (Reid Hoffman, 6), The Product Experience (Mind the
Product, 6) and Behind the Craft (Peter Yang, 10). The Knowledge Project
finds its channel but matches only 1 episode (heavily retitled), and
Unsupervised Learning has only 2 recent episodes; both stay unlinked.
Five control shows with a correct channel on record found that same
channel with every rival at 0–1. The guard that matters is the minimum
of 3: The Econoclasts' own channel matched none, while UnHerd matched 2
and Glenn Diesen 1 through guest cross-posts.

#### Clarifications (2026-10-19)

- *Replace wrong channels, or fill empty ones only?* Replace, guarded:
  only a chart-sourced channel that matched 0 of the sample and has made
  no channel-listing link for the show, and only when a different
  channel passes verification. Publisher-stated and curated channels are never
  replaced (Behind the Craft's `@RasMic` stays a `set-show-link` fix).
- *How often may a show search?* A per-show timestamp, retried every 7
  days. Riding the episode not-found markers would search daily and
  every 2 h for 48 h after each new episode, up to about 150 YouTube
  requests per episode for a show without a channel, from the EC2 box.
- *Acceptance threshold?* At least 3 matches and a clear lead (runner-up
  at most a third). 2 is exactly what UnHerd scored for The Econoclasts;
  5 loses Prof G Pod, a16z, Dwarkesh, Masters of Scale and Times Tech.
- *Base?* Stacked on PR #382: its retitled-upload rule carries several
  of the wins (Prof G Pod, Deep Questions).

Plan review (2026-10-19), folded in above:

- A failed listing is *unverified*, not a zero, and defers acceptance
  (a real channel's failed listing would otherwise let a cross-poster
  pass the runner-up guard).
- Verification runs on a stable sample of the newest 20 episodes, not on
  the pass's retry candidates (one newly eligible episode cannot reach 3
  matches, and the failed attempt would block a week).
- Evidence for the recorded channel is a channel-listing link, not any
  YouTube link: a publisher-provided link exists independently of the
  channel and must not keep a wrong chart channel forever.
- Rollback deletes the links a discovered channel made, not only the
  channel (below).

#### Execution plan

**Phase A: search and pick (pure, no I/O in tests).**

| # | File | Change |
| --- | --- | --- |
| A1 | `thestill/core/youtube_channel_discovery.py` (new) | `search_channels(query, limit)` (yt-dlp flat, channel filter, returns url + title); `verify_listing(...)` classifying a listing as verified count / verified 0 (no `/videos` tab) / unverified; `pick_channel(results)` (all verified, ≥ 3, runner-up ≤ a third); `search_queries(title, author)`; constants |
| A2 | `tests/unit/core/test_youtube_channel_discovery.py` (new) | `pick_channel` thresholds, ties and deferral on any unverified result; the no-videos-tab error is a verified 0 and other errors are unverified; query fallback; result parsing from a recorded flat payload |

Gate: the new unit tests and ruff pass.

**Phase B: storage.**

| # | File | Change |
| --- | --- | --- |
| B1 | `thestill/migrations/versions/0023_youtube_channel_discovery.py` (new) | `podcasts.youtube_discovery_checked_at timestamptz NULL` |
| B2 | `postgres_schema.py`, SQLite schema + column migration | Same column |
| B3 | `podcast_repository.py` + both backends | `get/set_youtube_discovery_checked_at`; `get_platform_link_sample(podcast_id, limit)` (newest episodes as candidates, link state ignored); `count_platform_links(podcast_id, platform, match_methods)`; `forget_discovered_youtube_channels(podcast_id=None)` (rollback, below); chart backfill keeps a `resolver` YouTube channel |
| B4 | `tests/integration/test_platform_links_repository_contract.py` | Stamp round trip; the sample includes linked and recently marked episodes; counting by method ignores `publisher` rows and not-found markers; forget deletes listing links and markers on `resolver` shows only, keeps `publisher` rows and other shows; chart sync keeps `resolver` but still fills an empty row |

Gate: contract tests on SQLite and on Postgres via `TEST_DATABASE_URL`.

**Phase C: service, config, wiring.**

| # | File | Change |
| --- | --- | --- |
| C1 | `thestill/services/platform_link_service.py` | `search_channels` injectable; `_discover_youtube_channel` before the listing; wrong-chart detection on the source of the channel *used in the pass* (a publisher-stated channel found this pass counts as `publisher` even in a dry run, where it is never stored); dry-run / force behaviour; a `platform_links_channel_discovery` log line (query, per-result scores, outcome) and `channel_discovered` on `platform_links_resolved` |
| C2 | `thestill/utils/config.py`, `cli.py`, `web/app.py` | `PLATFORM_LINKS_YOUTUBE_DISCOVERY_DAYS` (7, 0 = off); `thestill link-platforms --forget-discovered [--podcast-id X] [--dry-run]` |
| C3 | `tests/unit/services/test_platform_link_service.py` | Empty channel found and used in the same pass; verification uses the sample when only one candidate is eligible; an unverified result defers even when a rival has 3+ matches; nothing accepted stamps and writes not-found markers; throttle respected and ignored under `--force`; dry run stores nothing; a wrong chart channel is replaced even beside an unrelated publisher link; a chart channel with a channel-listing link, or a failed listing, is never replaced; publisher / curated never replaced; search failure stamps |
| C4 | `docs/configuration.md`, this spec, `specs/README.md` | Document the setting and the outcome |

Gate: the platform-link unit and contract suites, ruff, and a live
`thestill link-platforms --dry-run` against the local database on the
prototype shows.

#### Alternatives considered

- **No discovery, curate by hand** (`set-show-link`). Works for a handful
  of followed shows but does not scale to off-chart shows, and the chart
  is wrong often enough (3 of 32 channels on record) that "correct by
  hand" also means finding the errors.
- **Search videos (`ytsearch`) and vote on uploaders.** One request
  returns uploaders of matching videos, but a guest's or a clip
  channel's upload ranks as high as the show's own; the channel filter
  ranked the right channel first for every prototype show.
- **Accept the top search result by name.** Exactly the namesake risk
  Phase 2 refused ("How I AI" has four namesake channels; "Possible"
  returns Project Possible first).
- **A separate discovery job or CLI.** Would need its own scheduling and
  candidate selection; the refresh pass already knows which shows have
  unresolved YouTube candidates.

#### Rollback

Set `PLATFORM_LINKS_YOUTUBE_DISCOVERY_DAYS=0` to stop searching, then
run `thestill link-platforms --forget-discovered` (`--podcast-id` for one
show, `--dry-run` to count first). Clearing `youtube_url` alone is not
enough: episode links are separate rows, and the candidate query skips
every found URL even under `--force`, so wrong episode links would stay
visible and never relink.

What a discovered channel made is attributable without a new column.
`youtube_url_source = 'resolver'` marks every discovered channel (no
other YouTube writer uses `resolver`). On such a show every YouTube row
with `match_method` `title_date` / `title_duration` came from that
channel's listing: discovery only runs on a show with no channel, or
replaces a chart channel only when the show has no listing-made link.
`--forget-discovered`, per `resolver` show and in one transaction:

1. deletes the show's YouTube rows with `match_method IN ('title_date',
   'title_duration')` and its YouTube not-found markers, so every
   affected episode is a candidate again at once;
2. keeps `publisher` rows, which never depended on a channel;
3. clears `youtube_url`, `youtube_url_source` and
   `youtube_discovery_checked_at`.

The next `link-platforms` pass (or refresh) relinks from what is left:
the chart channel comes back through the chart sync (its link source is
`chart` again), and the publisher rows stand. With discovery still
enabled, the cleared stamp means the show is searched again on that
pass; leave it disabled while rolling back. The migration only adds a
nullable column; its downgrade drops it.

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
| 3c | Show-link provenance (`chart` / `publisher` / `resolver` / `curated`) with the curated guard on both backends, website-scan discovery with the uniqueness and generic-channel guards, `thestill set-show-link` | ✅ 2026-09-29 |
| 3c′ | An edit field on the podcast page for the curated links; the manual pass ordered by follows, not chart rank (followed shows without a channel or Spotify id first) | Open |
| 3d | Verified YouTube channel discovery: channel search by show title (then title + author), accept a channel only when at least 3 of the show's episodes match its uploads and the runner-up has at most a third as many; also replaces a chart channel that matches nothing; weekly per-show stamp. See "YouTube channel discovery (Phase 3d)" | 🚧 In progress (2026-10-19) |
| 3e | Feed the #62 playback rendition from `youtube` links once they have been eyeballed in production | Open |
| 3f | Resolver follow-ups from the a16z case: dominant-share vote for show links, young-episode recheck, publisher-stated Apple show page tried before the title search | ✅ 2026-09-29 |

## Configuration

| Variable | Description | Default |
|----------|-------------|---------|
| `PLATFORM_LINKS_ENABLED` | Resolve per-episode platform links after refresh and via the CLI | `true` |
| `PLATFORM_LINKS_RECHECK_HOURS` | How long a not-found row suppresses another lookup for that episode | `24` |
| `PLATFORM_LINKS_YOUNG_RECHECK_HOURS` | The shorter suppression for an episode still inside its young window | `2` |
| `PLATFORM_LINKS_YOUNG_AGE_HOURS` | Hours after `pub_date` during which the young interval applies; `0` disables | `48` |
| `PLATFORM_LINKS_YOUTUBE_DISCOVERY_DAYS` | Days before a show without a verified YouTube channel is searched again (Phase 3d); `0` disables discovery | `7` |

## Testing

- Matcher unit tests: each method, ambiguity skip, one-entry-one-episode.
- Service tests with a fake repository and fake iTunes fetchers: no
  candidates → no call; show search accepted on feed URL equality, or on a
  unique title hit proven by the window (an unproven hit stores nothing);
  lookup error writes nothing; dry run writes nothing; force ignores the
  throttle; the young thresholds reach the repository unless forced or
  disabled; a publisher-stated Apple show is tried before the search and
  falls back to it when its window does not prove it.
- Repository contract (SQLite and Postgres): a young episode's marker
  expires at the short interval while an old one waits for the full one.
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

**2026-09-29, Phase 3a/3b rules, forced pass over the same data:** Apple
4560, YouTube 953 (688 `title_date`, 168 `title_duration`, 97
`publisher`), Spotify 4. The YouTube total barely moves because the
core-title rule mostly upgrades matches that already landed as
`title_duration`, but shows that prefix the number and suffix the show
name change a lot: Joe Rogan 47 of 47 (was 33), Huberman 25 of 32 (was
17), Lex Fridman 12 of 17 (was 5). Spotify waits on 3c: no followed show
has a Spotify id on its row yet, so the probe never ran; it is verified
live on Lex's show.

## Open questions

- Episodes older than the newest 200 stay unlinked on Apple. The import
  path's page-scrape fallback (#65 Tier 3) works per episode and is not
  worth one request per old episode here.
- 39 shows had no YouTube channel on record on 2026-09-28: Phase 3c/3d above. Shows whose uploads are heavily retitled (The Knowledge Project) or that publish too rarely to reach 3 matches stay unlinked by 3d.
- Spotify show ids have no discovery shortcut (no directory carries them),
  so 3c's curation is the only way to reach the probe at scale; a public
  dataset would be worth a look before hand-curating 500 rows.
- YouTube's approximate dates make the 45-day band the weak point; exact
  per-video dates cost one request per video and are not worth it.
- Whether the list endpoint should carry the links too (a per-row "Listen
  on" is not planned, so not yet).

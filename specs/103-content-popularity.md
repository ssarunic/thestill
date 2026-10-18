# Content Popularity

> **Status:** ✅ Phases 0–2 implemented (2026-10-18)
> **Created:** 2026-10-17
> **Priority:** Medium. Phase 0 needs no migration and can ship with the next deploy.
> **Author:** Product & Engineering
> **Related:** [#29 per-user-inbox-fanout](29-per-user-inbox-fanout.md) (read state), [#31 import-arbitrary-episodes](31-import-arbitrary-episodes.md) (synthetic podcasts), [#42 robustness-and-failure-mode-hardening](42-robustness-and-failure-mode-hardening.md), [#90 continue-listening](90-continue-listening.md) (listening progress), [#94 public-pages](94-public-pages.md), [#95 product-analytics](95-product-analytics.md) (Layer A, the page and CLI this extends), [#98 genre-aware-summaries](98-genre-aware-summaries.md) (categories), [#99 top-chart-movement-and-topics](99-top-chart-movement-and-topics.md) (Apple charts, not our readers)

---

## Executive summary

Spec #95 says who signs up and whether they come back. It does not say
what they read. Nobody can answer which podcasts, episodes and
categories are popular with Thestill's own readers.

PostHog can't answer it either, by design. Spec #95 keeps podcast and
episode identifiers out of every event: `$pageview` carries the route
pattern (`/podcasts/:podcastSlug`), never the slug, and
`public_page_view` carries only `kind` and `referrer_host`. PostHog
also counts only readers who share usage, so a ranking from it would be
skewed by opt-outs and blockers.

The answer is already in product tables: follows, inbox reads and
listening progress. So this spec extends Layer A of #95 with a
**Content** section on `/analytics` and in `thestill analytics`: top
podcasts, top episodes and categories for the selected window, with
change against the previous window.

Two gaps, each closed by storing one new fact once (the #95 §1 rule):

| Phase | Gap | Fix |
|---|---|---|
| 0 | None; ships on today's tables, with known undercounts listed on the page | Content section from `podcast_followers`, `user_episode_inbox`, `user_episode_progress` |
| 1 | An episode opened outside the inbox, or after it was saved or dismissed, records nothing; progress rows can't tell playback from a "Mark as played / unplayed" | `user_episode_views`: the first time each user opened each episode; `user_episode_progress.listened_at`: the last write that came from playback |
| 2 | Anonymous public page views have no object identity anywhere durable | `public_page_daily_views`: a per-day counter per public page, no visitor id |

Nothing new is sent to PostHog.

## Outcomes

| # | Outcome | Verified by |
|---|---|---|
| O1 | For any 7/28/90-day window an admin sees the top podcasts by readers, with followers, new follows, listeners, completions, follower reach and change against the previous window | Postgres contract tests on a seeded fixture |
| O2 | The same window shows the top episodes by readers, with listeners and completions | same |
| O3 | Categories roll each show up to its top-level Apple category and add up: every non-synthetic show is in exactly one row, "Uncategorised" included | contract test: per-category show counts sum to the total |
| O4 | Every count uses #95's population (no admins, no excluded emails). No row links to the people behind it. No identifier reaches PostHog. | contract test with an admin in the fixture; review checklist |
| O5 | Phase 1: opening an episode from search, an entity page, a show not followed or a saved row counts as a read | pytest on the view write; contract test on the ranking |
| O6 | Phase 2: public views per podcast and episode per day, browsers only, with no visitor id, IP or referrer stored | pytest on the beacon route |

## Current state

- **Follows.** `podcast_followers (user_id, podcast_id, created_at)`.
  Unfollowing deletes the row, so the current follower count is exact
  but follow history is not: "new follows in the window" counts follows
  that still exist.
- **Reads.** `user_episode_inbox.state = 'read'` with
  `state_changed_at`. `EpisodeReader` calls `POST /api/inbox/{id}/read`
  once the summary has loaded (`useMarkInboxReadOnView`), and
  `InboxService.mark_read` moves a row from `unread` to `read` and
  nothing else
  ([postgres_inbox_repository.py:242](../thestill/repositories/postgres_inbox_repository.py#L242)).
  That leaves three undercounts:
  1. **No inbox row.** Episodes opened from search, an entity page, the
     related rail, or a show the reader doesn't follow record nothing.
  2. **Saved or dismissed first.** Opening a saved row records nothing.
  3. **Read, then saved or dismissed.** The state and `state_changed_at`
     are overwritten, so the read disappears.
- **Listens.** `user_episode_progress` (#90): one row per user and
  episode. `updated_at` moves on every write; `completed_at` is set on
  completion. A listen in the current window therefore hides the same
  user's listen in an earlier window. Not every write is playback
  ([listening_progress_service.py:85](../thestill/services/listening_progress_service.py#L85)):
  "Mark as played" (`mark_played`) and "Mark unplayed" (`mark_unplayed`)
  both store position 0 and move `updated_at` without anyone listening.
  `mark_played` also sets `completed_at` and, like every completion,
  calls `InboxService.mark_read`, so it turns an unread inbox row into a
  read too. A stored row can't tell `mark_played` from a real `ended`.
- **Categories.** `podcasts.primary_category_id` points at
  `categories (id, name, parent_id)`. The id can be a top-level category
  or a subcategory; `COALESCE(parent_id, id)` gives the top level.
  `secondary_category_id` exists too.
- **Imported episodes.** #31 imports hang off synthetic parents
  (`podcasts.synthetic = true`), one per source, not a real show.
- **Public views.** `POST /api/public/pageview`
  ([api_public.py:1009](../thestill/web/routes/api_public.py#L1009))
  classifies the path into a kind, logs `public_page_view` and sends
  `kind` + `referrer_host` to PostHog for browsers. Signed-in readers
  are skipped. Which page was viewed is not stored anywhere.
- **Admin surface.** `/analytics`
  ([Analytics.tsx](../thestill/web/frontend/src/pages/Analytics.tsx))
  renders Signups, Activation, Retention and Engagement from
  `GET /api/dashboard/analytics?range=`. `AnalyticsService.report`
  assembles it from `PostgresAnalyticsRepository`, whose `_REAL_USERS`
  CTE is the population every query counts. SQLite gets
  `UnavailableAnalyticsRepository`.
- **Not this:** the `/top` page shows Apple chart ranks (#57, #99), not
  what Thestill readers read.

## Questions the system must answer

| Question | Phase | Source |
|---|---|---|
| Which podcasts have the most readers in the window, and how is that changing? | 0 | inbox reads ∪ progress, joined to `episodes.podcast_id` |
| Which podcasts are most followed, and which gained the most follows? | 0 | `podcast_followers` |
| Which shows do people follow but not open? | 0 | follower reach: followers who read any episode in the window ÷ followers |
| Which episodes have the most readers, listeners and completions? | 0 | inbox reads, `user_episode_progress` |
| Which categories have the most shows followed, followers and readers? | 0 | the above, rolled up by top-level category |
| The same podcast and episode rankings without the inbox undercount | 1 | `user_episode_views` ∪ progress |
| Which public podcast and episode pages do anonymous visitors view most? | 2 | `public_page_daily_views` |

Anything not in this table is not collected (constitution §10).

## Design

### 1. Definitions

- **Population.** #95's `real_users`: not admin, not an excluded email.
  Every join goes through it.
- **Reader of an episode in a window.** A real user who opened it or
  listened to it in the window. Phase 0's open is an inbox `read`
  transition; from Phase 1 it is `user_episode_views.first_viewed_at`.
  Both are first-time facts, so rereading an old episode counts once,
  when it first happened.
- **Listening.** Playback only, never a "Mark as played / unplayed".
  Phase 0 counts a progress row as listening when `updated_at` falls in
  the window **and** `position_seconds > 0 OR completed_at IS NOT NULL`:
  that drops `mark_unplayed` resets (position 0, no completion) but still
  counts a `mark_played` as a completion, and its inbox flip as a read,
  both of which the page notes. From
  Phase 1 listening is `listened_at` (§4a), which no `mark_*` write
  touches.
- **Readers of a podcast.** Distinct users who were readers of any of
  its episodes in the window.
- **Listeners, completions.** Distinct users who were listening in the
  window, and those of them whose row completed in it. From Phase 1 a
  completion counts only when playback wrote it
  (`completed_at = listened_at`, §4a).
- **Followers.** The current count. **New follows:** surviving follows
  created in the window.
- **Follower reach.** Of the show's current followers, how many were
  readers of it in the window. Low reach on a large follower count is
  the "subscribed, never opens" signal.
- **Change.** Readers in the window against readers in the previous
  window of the same length. Shown as `+3`, `−2` or `new` when the
  previous window had none.
- **Category.** The top-level category of `primary_category_id`.
  `secondary_category_id` is ignored, so a show is counted once and
  category totals add up (decision D2). No category gives
  "Uncategorised".
- **Imported episodes.** Synthetic podcasts are left out of the podcast
  and category tables. Their episodes stay in the episode table with an
  "Imported" label.

### 2. Rankings and limits

- **Podcasts.** The server returns the union of the top 20 by readers
  and the top 20 by followers (at most 40 rows). The page sorts by
  readers by default and by followers on a header click, so each sort
  is an exact top 20 rather than a client sort over a truncated list.
  Ties break on followers, then title.
- **Episodes.** Top 20 by readers, ties on listeners, then publish date.
- **Categories.** Every top-level category with at least one followed
  show **or** at least one reader in the window or the previous one,
  plus "Uncategorised"; about 20 rows. Reading activity alone is enough,
  so a category reached only through search, entity pages or the related
  rail still appears (with `shows = 0` if nobody follows its shows), and
  one that dropped to zero readers stays visible with its `−n`. Sorted by
  readers, then followers.

### 3. Repository and models

`AnalyticsRepository` gains one method; the Postgres implementation runs
one SQL statement per table:

```python
@abstractmethod
def content(self, window: AnalyticsWindow, previous: AnalyticsWindow, *, limit: int) -> ContentReport:
    """Top podcasts, top episodes and categories for ``window``, with
    readers in ``previous`` for the change column."""
```

```python
class PodcastStat(BaseModel):
    podcast_id: str
    slug: str
    title: str
    category: Optional[str]  # top-level name; None = uncategorised
    followers: int
    new_follows: int
    follower_readers: int  # followers who were readers in the window
    readers: int
    readers_previous: int
    listeners: int
    completions: int


class EpisodeStat(BaseModel):
    episode_id: str
    slug: str
    title: str
    podcast_slug: str
    podcast_title: str
    published_at: Optional[datetime]
    imported: bool  # synthetic parent (#31)
    readers: int
    listeners: int
    completions: int


class CategoryStat(BaseModel):
    category: str  # top-level name, or "Uncategorised"
    shows: int  # shows with at least one follower
    shows_read: int  # shows with at least one reader in the window
    followers: int  # distinct users following any show in it
    readers: int
    readers_previous: int


class ContentReport(BaseModel):
    podcasts: List[PodcastStat]
    episodes: List[EpisodeStat]
    categories: List[CategoryStat]
    notes: List[str] = []  # the undercounts of the deployed phase
```

`AnalyticsReport` gains `content: ContentReport`, and
`AnalyticsService.report` computes `previous` as the window of the same
elapsed length ending at `window.start`: the window ends "now", part-way
through today, so a 28-day range checked at noon compares 27.5 days with
the 27.5 days before them. The service sets `content.notes`; the page
shows them under the Content tables and the CLI after its Content block.

The shared reads CTE, Phase 0 shape:

```sql
reads AS (
    SELECT i.user_id, i.episode_id, i.state_changed_at AS at
      FROM user_episode_inbox i JOIN real_users ru ON ru.id = i.user_id
     WHERE i.state = 'read'
       AND i.state_changed_at >= %(prev_start)s AND i.state_changed_at < %(end)s
    UNION ALL
    SELECT p.user_id, p.episode_id, p.updated_at
      FROM user_episode_progress p JOIN real_users ru ON ru.id = p.user_id
     WHERE p.updated_at >= %(prev_start)s AND p.updated_at < %(end)s
       AND (p.position_seconds > 0 OR p.completed_at IS NOT NULL)  -- not a mark_unplayed reset
)
SELECT e.podcast_id,
       count(DISTINCT r.user_id) FILTER (WHERE r.at >= %(start)s) AS readers,
       count(DISTINCT r.user_id) FILTER (WHERE r.at <  %(start)s) AS readers_previous
  FROM reads r JOIN episodes e ON e.id = r.episode_id
 GROUP BY e.podcast_id
```

Phase 1 swaps the first branch for `user_episode_views` and the second
for `p.listened_at` in the window, and leaves the rest alone. Indexes
come from `EXPLAIN` on a production-sized copy, not guesswork, as
in #95 §2; the likely ones are a partial index on
`user_episode_inbox (state_changed_at) WHERE state = 'read'` and one on
`user_episode_progress (updated_at)` (Phase 1: `listened_at`).

### 4. Phase 1: `user_episode_views`

The fact the inbox can't hold: the first time a user opened an episode.

```sql
CREATE TABLE IF NOT EXISTS user_episode_views (
    user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    episode_id uuid NOT NULL REFERENCES episodes(id) ON DELETE CASCADE,
    first_viewed_at timestamptz NOT NULL,
    PRIMARY KEY (user_id, episode_id)
);
CREATE INDEX IF NOT EXISTS idx_episode_views_at ON user_episode_views(first_viewed_at);
```

- **Where it's written.** `POST /api/inbox/{id}/read` already fires on
  every summary view from every route (inbox overlay, standalone page,
  search, entity pages), whether or not an inbox row exists. Its route
  handler records the view, `INSERT … ON CONFLICT DO NOTHING` through a
  new `EpisodeViewRepository`, next to its `InboxService.mark_read`
  call. Not inside `InboxService.mark_read`: the listening service also
  calls that on every completion, `mark_played` included, and a "Mark as
  played" is not an open. The endpoint name stays; its docstring says it
  records the view too.
- **It's a POST, not a side effect of the episode GET**, for the reason
  #95 gave for `opened_at`: React Query prefetches and refetches must
  not count as views.
- **Failure isolation.** The view insert is its own statement. A failure
  is logged at warning with `exc_info` and the inbox transition and
  response go ahead; the client ignores the response anyway.
- **Opt-outs.** Rows are not written for a user with
  `usage_analytics_enabled = false` or a request carrying `Sec-GPC: 1`
  (decision D5). Turning the switch off also deletes the user's rows
  and clears their `listened_at`, in the same transaction
  (`UserRepository.update_usage_analytics`; settled 2026-10-18). Unlike follows and reads, the service doesn't need
  this table to run; it exists to measure.
- **Both backends.** Postgres via an Alembic migration with the DDL
  mirrored in `SCHEMA_SQL`; SQLite via `SqlitePodcastRepository`'s column
  guards, so the two paths don't drift (FM-6) even though SQLite has no
  analytics.
- **Backfill.** From `user_episode_inbox` rows in `read` with
  `state_changed_at`, for users with `usage_analytics_enabled = true`
  only: the same filter as live writes. GPC is per request and can't be
  applied to history; the backfill is the account switch alone. The
  backfill can't tell a `mark_played` read from an open, so it carries
  that overcount into old rows. Views before the migration that the
  inbox lost stay lost; the page says opens before the migration date
  are approximate.
- **Privacy policy.** "What you have read" is already listed as account
  data ([privacy.md:29](../thestill/web/frontend/public/legal/privacy.md)),
  and the usage-analytics paragraph (line 57) stays true because views
  honour the switch. No policy change is expected; review confirms it.
- **One source per metric.** After Phase 1, the Content section reads
  views and progress only. The inbox `read` state stays what the inbox
  UI shows and is no longer a Content input. #95's own metrics
  (engagement, activation, retention) keep reading inbox `read`: #95 D2
  counts reads for everyone, and moving them onto opt-out-filtered views
  would quietly change those numbers (settled 2026-10-18).
- **Implementation.** The insert selects from `episodes`, so an unknown
  or deleted episode writes nothing rather than failing on the foreign
  key. `may_measure(user, request)` in `web/analytics_request.py` is the
  one opt-out check for both writes.

### 4a. Phase 1: `user_episode_progress.listened_at`

A nullable `listened_at timestamptz` on the progress row: the time of
the last write that came from playback. `plan_row` sets it to `now` for
`tick`, `pause` and `ended`, and for a `stop` that completes; it carries
the stored value forward for `seek`, a non-completing `stop`,
`mark_played` and `mark_unplayed`. `tick` only fires while audio plays,
and only a playing episode can be paused or end, so every listen of
15 s or more, and every shorter one ended by a pause, is recorded.

- **Completions.** A playback completion sets `completed_at` and
  `listened_at` from the same `now`. A `mark_played` sets `completed_at`
  only. Analytics counts a completion when `completed_at = listened_at`.
- **Opt-outs.** Like the views table, the column exists only to measure,
  so it is left unchanged for a user with
  `usage_analytics_enabled = false` or a write carrying `Sec-GPC: 1`
  (decision D5). Resume, restore and completion keep working; they read
  the other columns.
- **Backfill.** `listened_at = updated_at` where
  `position_seconds > 0 OR completed_at IS NOT NULL`, for users with the
  switch on: the Phase 0 rule, so old windows read the same before and
  after the migration.
- **Both backends**, as for the views table.
- **Implementation.** A write that isn't playback sends no value and the
  upsert keeps the stored one; a playback write stores
  `GREATEST(new, stored)`, so the value only moves forward. Nothing is
  carried from the row the service read, which may be stale.
- **Stop is not proof of playback.** The player also sends `stop` for a
  paused session and for a restored bar that never played, so a
  completing `stop` sets `listened_at` only when the same session's
  previous write was playback (`stored.listened_at = stored.updated_at`
  and the same `session_id`). Found in review, 2026-10-18.
- **Opt-out races.** The route's `may_measure` runs before the write, so
  a user can opt out in between. Both writes therefore re-check
  `usage_analytics_enabled` in their own transaction: Postgres with
  `SELECT … FOR SHARE` on the user row, which the opt-out's
  `UPDATE users` waits for and vice versa; SQLite inside the single
  write statement. A write in flight during an opt-out can't restore
  what the opt-out erased.

### 5. Phase 2: `public_page_daily_views`

```sql
CREATE TABLE IF NOT EXISTS public_page_daily_views (
    day date NOT NULL,              -- UTC
    kind text NOT NULL,             -- episode | podcast | entity | top | landing
    object_id text NOT NULL,        -- podcast / episode / entity id; '' for top and landing
    views integer NOT NULL,
    PRIMARY KEY (day, kind, object_id)
);
```

- **Written by** `POST /api/public/pageview` after it has classified
  the path, for the same browsers it already sends to PostHog (not
  crawlers or link previews). Signed-in readers are already skipped.
  One upsert: `views = views + 1`.
- **Keyed by id, not slug**, so a renamed slug keeps its history. The
  route resolves the slug with one indexed lookup; an unknown slug, or a
  show with `public_pages = false`, is not counted.
- **Views, not visitors.** No visitor key, IP, user agent or referrer
  is stored. Referrers stay in PostHog. A few visitors cannot be
  singled out from a daily count per page.
- **Retention.** Aggregates with no personal data are kept
  indefinitely.
- **Load.** One small upsert per public view is fine at current traffic
  and the route is already rate-limited per IP. If the hottest rows
  ever contend, buffer counts in memory and flush once a minute; not
  built now.
- **Surfaces.** The podcast and episode tables gain a "Public views"
  column for the window, and the categories table a total. A show's
  figure is its own page plus its episode pages; an episode's is its
  page. It is a column only: the rankings stay readers and followers, so
  a show with anonymous traffic and no readers does not appear
  (settled 2026-10-18). Entity, top and landing rows are stored but not
  shown yet.
- **Implementation.** The beacon resolves the path with the public API's
  own lookups (`_public_podcast_row`, `_public_episode`,
  `_entity_id_from_path`), so a page counts exactly when the public site
  would serve it; share and legal pages are not counted. The increment
  is its own statement; a failure is logged and the beacon still
  returns 204.

### 6. Surfaces

**Admin page.** A new `ContentSection` below Engagement on `/analytics`,
driven by the existing range control, with three tables: Podcasts,
Episodes, Categories. Like the other sections it uses tables and inline
bars from the existing tokens, puts the count beside every ratio, and
greys out rows with fewer than five readers instead of hiding them
(#95 D1). Podcast and episode titles link to their signed-in pages. No
row expands into the people behind it. The section's notes list the
undercounts that apply to the deployed phase.

**API.** `GET /api/dashboard/analytics` returns `content` inside the
existing payload; same `require_admin`, same range parameter.

**CLI.** `thestill analytics` prints a Content block: top 10 podcasts,
top 10 episodes, every category. `--json` prints the full payload.

### 7. Why tables, not PostHog

| Option | For | Against |
|---|---|---|
| Add podcast and episode slugs to PostHog events | Exploration in PostHog | Undoes #95's rule that page identity never leaves; counts only readers who share usage; a fact (follows, reads) would be stored twice (FM-6) |
| **Extend Layer A from product tables** | Counts everyone; no vendor; reuses the page, service and population of #95 | Needs Phase 1 to fix the read undercount |
| A BI tool (Metabase) on a read replica | Ad-hoc questions | A second service to run on a single EC2 box (#66), and admin access to raw tables per person |

## Non-goals

- Per-user views: who read what. The page shows aggregates only.
- "Popular on Thestill" for readers or the public site. Showing
  popularity to non-admins needs its own spec and a minimum-count rule.
- Unique visitors, time on page or scroll depth for public pages.
- Listening minutes. `position_seconds` is a position, not time
  listened; a seek inflates it.
- Popularity of entities, quotes or search terms.
- Recommendations built on these numbers.

## Failure modes

| Risk | Guard |
|---|---|
| The ranking is read as exact while reads are undercounted (Phase 0) | Notes on the page and in the CLI name the three undercounts; Phase 1 removes them |
| `readers_previous` is low for listeners because `updated_at` moves | Noted on the page; the change column is a trend, not an exact count |
| "Mark as played / unplayed" counted as listening or reading | Phase 0 drops resets and notes `mark_played`; Phase 1 counts only `listened_at` and views written by the route handler, never by `InboxService.mark_read` |
| Opted-out readers counted through a backfill | Both Phase 1 backfills apply the same `usage_analytics_enabled` filter as live writes |
| A category read only through discovery is missing | Categories are listed for follows **or** reading activity (§2) |
| Imported episodes crowd the podcast table | Synthetic podcasts excluded from podcast and category tables |
| Category totals don't add up | Primary category only, plus "Uncategorised"; contract test sums shows |
| A small count points at one person | Admin only, aggregates only, no drill-down, counts under five greyed; follows #95 D1 |
| The queries slow the page as tables grow | One statement per table, indexes from `EXPLAIN` on a production-sized copy; contract test at 10× the current row count stays under 1 s |
| The view write breaks reading | Own statement after the inbox transition; failure is logged, the response is unaffected |
| The same fact counted twice | After Phase 1, analytics reads views, not inbox `read` (§4) |
| Bots inflate public counts | The same user-agent classifier that gates PostHog; per-IP rate limit; unknown slugs not counted |

## Phases

| Phase | Scope | Ships alone? |
|---|---|---|
| 0 ✅ | `ContentReport` models; `AnalyticsRepository.content` (Postgres) and the stub; `previous` window in `AnalyticsService`; `content` in `GET /api/dashboard/analytics`; `ContentSection`; CLI Content block; notes; indexes as `EXPLAIN` decides (none needed, see below) | Yes; no migration |
| 1 ✅ | `user_episode_views` and `user_episode_progress.listened_at` (Alembic + `SCHEMA_SQL` + SQLite guards); opt-out-filtered backfills; view write in the `POST /api/inbox/{id}/read` handler and `listened_at` in `plan_row`, both honouring opt-outs; switch the reads CTE; privacy policy check | Yes |
| 2 ✅ | `public_page_daily_views`; upsert in `POST /api/public/pageview`; "Public views" columns | Yes |

**Phase 0 measurement (2026-10-18).** Production holds 99 shows and
6.3k episodes. A synthetic set well past 10× (2,000 users, 1,000 shows,
65,000 episodes, 400,000 inbox rows, 60,000 progress rows, 20,000
follows) runs the whole content report in 0.15 s (7d), 0.31 s (28d) and
0.57 s (90d). The partial `user_episode_inbox (state_changed_at) WHERE
state = 'read'` and `user_episode_progress (updated_at)` indexes made no
difference (the windows cover too much of each table for an index scan
to win), so Phase 0 adds no index.

## Testing

**pytest** (Postgres contract tests via `TEST_DATABASE_URL`, as #95):

- A seeded fixture with three shows (one synthetic, one uncategorised,
  one in a subcategory), followers with follows inside and outside the
  window, inbox reads, progress rows and completions, and an admin who
  reads everything. Assert every count exactly, the admin never
  counted, the synthetic show absent from podcasts and categories, and
  its episode present with `imported = true`.
- The subcategory show lands in its parent's row; per-category show
  counts sum to the number of non-synthetic shows with followers.
- A category whose only activity is a read of an unfollowed show appears
  with `shows = 0`, `shows_read = 1` and its reader.
- Phase 0: a `mark_unplayed` row is neither a listener nor a reader; a
  real partial listen (position > 0) is both.
- `readers_previous` counts the previous window only; a user reading in
  both windows counts once in each.
- The podcast list is the union of both top-N lists with no duplicates.
- Phase 1: `POST /api/inbox/{id}/read` on an episode with no inbox row
  writes a view and returns as before; a second call leaves
  `first_viewed_at` unchanged; an opted-out user or `Sec-GPC: 1` writes
  nothing; a failing view insert still marks the inbox row read; a
  `mark_played` progress write creates no view; the backfill copies
  `read` rows of opted-in users only and skips an opted-out user's.
- Phase 1 `listened_at`: `tick`, `pause` and `ended` set it; `seek`,
  `mark_played` and `mark_unplayed` leave it unchanged; a `mark_played`
  is not a completion and `ended` is; an opted-out user's writes leave
  it unchanged; the backfill follows the Phase 0 rule and the opt-out filter.
- Phase 2: a browser view increments the right row; a crawler, a
  signed-in reader, an unknown slug and a `public_pages = false` show
  increment nothing; no IP, user agent or referrer column exists.
- SQLite and single-user mode: the report stays "unavailable" and the
  CLI exits 0.

**Vitest:** `ContentSection` renders the three tables, switches the
podcast sort, shows `+n` / `−n` / `new`, greys rows under five readers,
and links titles to the signed-in pages.

## Decisions

Accepted 2026-10-18 as proposed.

1. **D1. Rank by readers, not follows.** Follows show intent, reads show
   use. Both columns are shown and both sorts are exact (§2).
2. **D2. Primary category only.** Counting the secondary category too
   would count most shows twice and make the totals meaningless.
3. **D3. Small counts are greyed, not hidden**, as in #95 D1. At current
   user numbers a cutoff would hide nearly every row.
4. **D4. No identifiers in PostHog.** The #95 rule holds; this spec adds
   nothing to the event allowlist.
5. **D5. `user_episode_views` and `listened_at` honour the usage
   opt-out and GPC, in live writes and in backfills.**
   Follows and reads count everyone because the service needs them to
   run (#95 D2); these two facts exist only to measure, so they follow
   the measurement opt-outs. The cost is a small undercount of opted-out
   readers and listeners, who from Phase 1 appear only as followers. The
   alternative is to count everyone and say so in the Settings copy.

## Open questions

Settled for Phase 0 (2026-10-18); revisit once there is real data.

- ~~Should the episode table also offer "top episodes published in the
  window"?~~ Not now: the page shows only "most read in the window".
- ~~Is 20 rows the right cut?~~ Yes: 20 per table on the page, the first
  10 in the CLI, no paging.

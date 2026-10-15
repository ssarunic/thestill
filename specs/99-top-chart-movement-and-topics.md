# Top Chart: Rank Movement, Categories and Topics

> **Status:** 📝 Draft (2026-10-15)
> **Created:** 2026-10-15
> **Priority:** Medium. The public `/top` page (spec #94) ships without this; these features make it worth revisiting and give every chart row a door into the entity pages.
> **Author:** Product & Engineering
> **Related:** [#57 eea-top-podcast-regions](57-eea-top-podcast-regions.md), [#94 public-pages](94-public-pages.md), [#100 quote-ranking-and-featured-quotes](100-quote-ranking-and-featured-quotes.md) (the "Most quoted people" box shares the chart's side column), [#101 list-row-enrichments](101-list-row-enrichments.md) (per-episode entity tags use the same type colours and key)

---

## Executive summary

The public UX brief for spec #94 and its aspirational Stitch mockups (project "Thestill Public Pages UX Design", 2026-10-15) show three things on the top chart that thestill cannot do today:

1. **Rank movement**: ▲2, ▼1, a dash, or **New** beside each rank, and a **Movers this week** box listing the biggest climbs.
2. **A category filter** next to the region switch (Business, Technology, News…).
3. **What each show talks about**: two or three entity names under each chart row, for shows in our corpus, each linking to its entity page.

Item 2 mostly exists already. Items 1 and 3 need new data: a ranking history and a per-podcast topic table. This spec adds both, makes chart ingestion a scheduled job instead of a hand-run script, and is honest about which ranks are real: past position about 200 the chart is our own round-robin over genre charts, not Apple's ranking, so movement is shown only where the rank is real.

## Current state

**Ingestion is manual.** [scripts/build_top_podcasts.py](../scripts/build_top_podcasts.py) reads Apple's legacy iTunes RSS (`itunes.apple.com/{region}/rss/toppodcasts/limit=200[/genre={id}]/json`, L102-113), enriches each show with `itunes.apple.com/lookup`, and writes `data/top_podcasts_<region>.{json,csv}` for 31 regions. Nothing schedules it; the local files are from July. `data/` is gitignored and not copied into the Docker image.

**Seeding happens at process start.** `PostgresPodcastRepository.__init__` calls `_seed_top_podcasts()` ([postgres_podcast_repository_podcasts.py:308-449](../thestill/repositories/postgres_podcast_repository_podcasts.py#L308-L449)). For each region whose file mtime differs from `top_podcasts_meta`, it deletes the region's rows from `top_podcast_rankings` and inserts fresh ones. `top_podcasts` keeps `first_seen_at` and `last_seen_at`; nothing else survives a reseed.

**No history.** `top_podcast_rankings` has the primary key `(region, top_podcast_id)` ([postgres_schema.py:593-600](../thestill/repositories/postgres_schema.py#L593-L600)). Yesterday's rank is gone once today's file is seeded.

**Most ranks past 200 are synthetic.** `collect_chart_entries` (build script L217-288) takes Apple's overall top 200 (`source_genre = NULL`), then round-robins one show at a time across 19 genre charts to reach 500. The JSON has no `rank` key; the seeder uses list position (`rank = row.get("rank") or i`, L381). In the US file 198 rows come from the overall chart and each genre adds about 16. Position 340 means "16th genre pick in round 8", not an Apple rank.

**The category filter exists.** `GET /api/top-podcasts` takes `region`, `limit` (1-500), `q` and `category` ([api_top_podcasts.py](../thestill/web/routes/api_top_podcasts.py); filter at [postgres_podcast_repository_podcasts.py:729-735](../thestill/repositories/postgres_podcast_repository_podcasts.py#L729-L735)), and returns `available_categories`. [TopPodcasts.tsx](../thestill/web/frontend/src/pages/TopPodcasts.tsx) already renders a Category `<select>` (L164-168) and a Region `<select>` (L182). The filter matches the show's own Apple genre (`top_podcasts.category_id`), not a genre chart, so "Business" today means "business shows that made our 500", ordered by overall position.

**No per-podcast topic data.** `entity_mentions` is indexed by entity and by episode, not by podcast ([postgres_schema.py:417-438](../thestill/repositories/postgres_schema.py#L417-L438)). `entity_cooccurrences` is entity-to-entity with no podcast dimension. The nearest query, `get_entity_summary`'s `most_discussed_on`, goes the other way (entity → podcasts).

**Postgres `categories` has no seeder**; it is filled only by the one-shot `db_promotion` copy. An empty table makes every category resolve to NULL.

## Design

### 1. Scheduled chart ingestion

Move the fetch out of the script into a service, `TopChartService`, with the script becoming a thin CLI wrapper (`thestill refresh-top-charts [--region X]`). The web server's existing scheduler runs it once a day per region, staggered over an hour, under the same lock pattern as `rebuild-cooccurrences`.

Each run:

1. Fetches the overall top 200 for the region (one request).
2. Fetches each genre's top 200 (19 requests) when categories are enabled (section 3); otherwise only the genre slices the round-robin needs today.
3. Writes the snapshot to the database directly, not to a JSON file. The JSON path stays for local development and as a seed fallback.

A failed fetch for a region keeps yesterday's rows and logs `top_chart.fetch_failed` with the region; it never writes an empty chart (FM-1, errors-as-empty-results).

### 2. Ranking history

New table, one row per show, region, chart and day:

```sql
CREATE TABLE top_podcast_rank_history (
    region        text   NOT NULL,
    chart         text   NOT NULL,  -- 'overall' or an Apple genre id, e.g. '1321'
    chart_date    date   NOT NULL,  -- UTC date of the fetch
    top_podcast_id bigint NOT NULL REFERENCES top_podcasts(id) ON DELETE CASCADE,
    rank          smallint NOT NULL,  -- Apple's position in that chart, 1..200
    PRIMARY KEY (region, chart, chart_date, top_podcast_id)
);
CREATE INDEX ON top_podcast_rank_history (region, chart, chart_date DESC);
```

Only real Apple ranks are stored: the overall chart and each genre chart, each 1 to 200. The round-robin "position 201-500" list is not history; it is how today's page fills its tail. Retention: 400 days, pruned by the same job.

`top_podcast_rankings` stays as the read model for "today", now written from the latest history snapshot plus the tail fill, so the existing API keeps its shape.

**Movement** for a row is computed at read time against the previous snapshot of the same region and chart:

| Today | Previous snapshot | Shown |
| --- | --- | --- |
| rank r | rank p | ▲(p − r), ▼(r − p) or a dash |
| rank r | absent, show seen in this chart before | **Back** |
| rank r | absent, never seen in this chart | **New** |
| tail position (no real rank) | any | nothing |

"Previous" is the latest snapshot strictly before today's `chart_date`, so a missed day compares against two days ago rather than showing everything as new.

**Movers this week**: the five largest climbs between today and the snapshot nearest to seven days ago, within the overall chart, ignoring shows that were outside the top 200 seven days ago (a jump from "absent" is **New**, not a climb). Shown as "BG2 Pod, up 14 places".

### 3. Categories that are real charts

Add `chart` to the API: `GET /api/top-podcasts?region=gb&chart=1321` returns Apple's Business chart for the UK in Apple's order, with movement. `category` keeps its current meaning (the show's own genre, any chart position) for the signed-in page and is deprecated for public use. The public `/top` page shows one **All categories** dropdown that switches `chart`.

`categories` gets a Postgres seeder from [podcast_categories.json](../thestill/utils/podcast_categories.json) at schema bootstrap (idempotent upsert on `apple_genre_id`), so a fresh Postgres has the 19 genres without `db_promotion`.

### 4. What each show talks about

New materialised table, rebuilt nightly and after each show's entity stage completes:

```sql
CREATE TABLE podcast_top_entities (
    podcast_id    uuid   NOT NULL REFERENCES podcasts(id) ON DELETE CASCADE,
    entity_id     text   NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    episode_count int    NOT NULL,
    mention_count int    NOT NULL,
    rank          smallint NOT NULL,
    window_days   smallint NOT NULL DEFAULT 90,
    computed_at   timestamptz NOT NULL,
    PRIMARY KEY (podcast_id, entity_id)
);
```

Ranking per podcast, last 90 days of episodes by `pub_date`:

- Count only `resolution_status = 'resolved'` mentions.
- Exclude the podcast's own anchors (`host_entity_ids`, `recurring_entity_ids`): a show "talking about" its own host is noise.
- Exclude speaking rows (`role = 'speaking'`), which record who spoke, not what was discussed.
- Rank by `episode_count`, then `mention_count`. Keep the top 10.
- Drop entities flagged by the public-page quality gate (spec #100 §2) so generic words never show.

Chart rows read the top 3 for the matched podcast (`LEFT JOIN podcasts ON rss_url`). Shows not in the corpus show nothing on that line. The show page's "What they talk about" side module (UX brief) reads the top 10 from the same table, so both surfaces agree.

### 5. API and frontend

`GET /api/top-podcasts` row additions: `movement` (`{kind: up|down|same|new|back, places}` or null), `topics` (up to 3 `{id, type, name}`), and top-level `movers` (up to 5) when `chart` is overall. Public router per spec #94; no per-user fields.

`TopPodcasts.tsx`: movement under the rank number (▲ green-700, ▼ red-700, both with text alternatives "up 2 places"), topic names with type dots on the row's third line, the Movers box in the side column, the chart dropdown. The colour key for type dots is spec #101 §5.

## Failure modes

| Risk | Guard |
| --- | --- |
| Apple RSS endpoint fails or returns an empty feed | Keep previous snapshot; never write zero rows; warning log with region |
| Legacy iTunes RSS is retired | Service isolates the fetcher behind one interface; the marketing-tools RSS (`rss.applemarketingtools.com`) is a candidate fallback |
| Movement shown for synthetic tail ranks | History stores only Apple ranks 1-200; tail rows have `movement = null` by construction |
| A missed day turns the whole chart "New" | Compare against latest earlier snapshot, not strictly yesterday |
| Topic tags show the host's own name or junk | Anchor and speaking-row exclusion, quality gate from #100 |
| `podcast_top_entities` goes stale | Rebuilt nightly and per show after entity stage; `computed_at` exposed in admin status |

## Non-goals

- Charts from Spotify or YouTube.
- Thestill's own popularity ranking (follows, reads). That is a different chart with different privacy questions.
- Historical chart pages (`/top/2026-10-01`). The history table makes them possible later.

## Phases

| Phase | Scope |
| --- | --- |
| 1 | `TopChartService` + scheduled daily job + `refresh-top-charts` CLI; Postgres `categories` seeder; history table written for the overall chart; `movement` in the API; arrows on `/top` |
| 2 | Genre charts fetched and stored; `chart` parameter; public category dropdown; Movers box |
| 3 | `podcast_top_entities` + topic tags on chart rows + show page "What they talk about" |

Movement needs at least two snapshots, so Phase 1 shows nothing for its first day by design.

## Testing

- Unit: movement table above, including Back, New, missed day, tail rows.
- Unit: movers ignore entries from outside the top 200.
- Repository: history insert is idempotent per `(region, chart, chart_date)`; pruning keeps 400 days.
- Service: a fetch error leaves today's read model unchanged (provider stub raises).
- Repository: `podcast_top_entities` excludes anchors and speaking rows, respects the 90-day window, contract-tested on SQLite and Postgres.
- Vitest: arrows have accessible text; rows without a corpus match render no topic line.

## Open questions

1. Is a daily fetch enough, or do movers need two fetches a day around Apple's update time?
2. Should the public chart show positions 201-500 at all, given they are not Apple's ranks? Default here: yes, without numbers past 200, labelled "More shows in this category".
3. Do we keep fetching all 31 regions daily (20 requests each, 620 a day), or only regions with at least one user?

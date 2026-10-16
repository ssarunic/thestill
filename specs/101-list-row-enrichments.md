# List Row Enrichments: Read Time, Summary Date, Entity Tags and Keys

> **Status:** 📝 Draft (2026-10-15)
> **Created:** 2026-10-15
> **Priority:** Low to medium. Small, independent pieces; read time and the summary date also fix gaps spec #94 already depends on.
> **Author:** Product & Engineering
> **Related:** [#28 corpus-search-and-entities](28-corpus-search-and-entities.md) (type colours), [#69 performance-hardening](69-performance-hardening.md) (stored summary preview), [#73 mobile-list-row-density](73-mobile-list-row-density.md), [#94 public-pages](94-public-pages.md), [#99 top-chart-movement-and-topics](99-top-chart-movement-and-topics.md), [#100 quote-ranking-and-featured-quotes](100-quote-ranking-and-featured-quotes.md)

---

## Executive summary

The aspirational mockups for the public pages (UX brief and Stitch project, 2026-10-15) add six small things to lists and pages. None is in spec #94. Each is cheap on its own; together they make a list row say more without a click:

| # | Feature | Where | Needs |
| --- | --- | --- | --- |
| 1 | **Read time**, "9 min read" | Show page episode rows, landing "Fresh this week", inbox rows | A stored summary word count |
| 2 | **Summarised on** date | Episode page reader tab bar | A stored summary-written time |
| 3 | **Entity tags**: 2-3 names per episode row | Show page rows, landing rows | One batched query |
| 4 | **Counts on filter buttons** ("Nvidia 38", "Scott Galloway (4)") | Person page topic filter, episode page speaker filter | Counts from spec #100 |
| 5 | **Colour key** for entity type dots | Any list that uses the dots | A component |
| 6 | **Explore top podcasts** secondary link | Landing hero | Nothing |

Items 1 and 2 add two episode columns. Spec #94 needs item 2 anyway: its `recent-summaries` feed is "ordered by the time the summary was written" and its sitemap `lastmod` is that time, but no such column exists.

## Current state

- **No summary word count, no summary-written time.** The `Episode` model ([podcast.py:190](../thestill/models/podcast.py#L190)) has `summary_path` (a filename resolved by `PathManager.summary_file()`), `summary_preview` (first section, spec #69), `created_at`, `updated_at` and `published_at`. The summary manifest sidecar stores language, sha256 and schema version only ([summary_artifacts.py:70-84](../thestill/core/summary_artifacts.py#L70-L84)).
- **`published_at` is the closest thing.** The summarize handler sets it after writing the summary ([task_handlers.py:766](../thestill/core/task_handlers.py#L766)), but only when NULL ([postgres_podcast_repository_episodes.py:958-970](../thestill/repositories/postgres_podcast_repository_episodes.py#L958-L970)), so a re-summary never moves it. `updated_at` changes on any write.
- **The episode list reads every summary file.** `GET /api/podcasts/{slug}/episodes` ([api_podcasts.py:271-311](../thestill/web/routes/api_podcasts.py#L271-L311)) builds `EpisodeWithIndex` in `get_episodes` ([podcast_service.py:557-660](../thestill/services/podcast_service.py#L557-L660)), which reads the full summary file to build the preview (L608-614) instead of using the stored `summary_preview`, and makes two `exists()` probes per row. On S3 storage that is a network read per row.
- **Rows.** PodcastDetail renders `EpisodeCard` (date, duration, preview), not `ListRow`. Inbox rows use `ListRow` with an overline of podcast title and date.
- **Entities per episode** come one episode at a time: `GET /api/episodes/{id}/entities` ([api_entities.py:306-420](../thestill/web/routes/api_entities.py#L306-L420)) loads up to 5,000 mentions and ranks them in Python. No batched method exists; `entity_mentions` is indexed on `episode_id`.
- **No legend.** [entityColors.ts](../thestill/web/frontend/src/utils/entityColors.ts) defines the four type styles; dots are drawn inline in several components; nothing explains them.
- **Filters have no counts.** The KeyEntitiesStrip type toggles and the inbox All / Unread / Saved buttons show labels only.

## Design

### 1. Read time

New column `episodes.summary_word_count integer NULL`, written by the summarize handler from the final summary text (words outside Markdown syntax and citation markers), and by the translation writer for the canonical language only.

Displayed read time is `max(1, round(words / 230))` minutes, rendered "9 min read". 230 words a minute is a common silent-reading average for non-fiction; it is a constant in one place.

Backfill: `thestill backfill-summary-stats` reads each summary file once and fills the column. Rows with NULL show no read time, never "0 min".

### 2. Summarised on

New column `episodes.summary_written_at timestamptz NULL`, set to `now()` **every** time a canonical summary is written, including re-summaries. `published_at` keeps its meaning (first delivery).

- Backfill: `summary_written_at = published_at` where NULL; the backfill command above also compares the summary file's storage mtime and takes the later of the two.
- Spec #94 `recent-summaries` orders by it; the sitemap `lastmod` uses it.
- Episode page: muted text at the right of the reader tab bar, "Summarised 15 Oct 2026", with the full timestamp in a `title` attribute. Re-summarised episodes read "Updated 15 Oct 2026".

### 3. Entity tags on episode rows

One repository method, `top_entities_for_episodes(episode_ids, limit=3) -> dict[episode_id, list[EntityRef]]`, one SQL statement:

- `entity_mentions` where `episode_id = ANY(%s)`, `resolution_status = 'resolved'`, `role <> 'speaking'`.
- Excludes the podcast's anchors (hosts and recurring) and the episode's guests; guests are shown separately on the row ("With Ed Elson", from `episodes.guest_entity_ids`).
- Excludes `entities.public_hidden` (spec #100) on public routes.
- Ranks by mention count, then distinct segments, then name; window function keeps the top 3 per episode.

The episode list endpoints (`GET /api/podcasts/{slug}/episodes`, spec #94 `recent-summaries`, the inbox list) add `key_entities` and `guests` per item from this one call per page. The per-episode salience rail stays as is; this is a cheaper, simpler ranking for rows, and a test asserts both agree on the top entity for a fixture episode where they should.

Rows render names with type dots in 13 px muted text, comma-free, separated by middle dots; each name links to its entity page.

While in this code, `get_episodes` switches to the stored `summary_preview` and drops the per-row file read and `exists()` probes (an existing column already holds what it computes).

### 4. Counts on filter buttons

- Person page topic filter: quote counts per topic from `public_quote_candidates.about_entity_ids` (spec #100), "Nvidia 38". "All" shows the total.
- Episode page speaker filter: quote counts per speaker in that episode, "Scott Galloway (4)".
- Counts are tabular figures in muted weight inside the button; a zero-count option is not shown.

### 5. Colour key

`EntityTypeKey` component built from `ENTITY_STYLES`: four dots with labels (Person, Company, Product, Topic), 11 px muted, one line, wraps on phones. Shown once under any list or rail module whose rows use type dots: show page "What they talk about", entity page "Often discussed with", the top chart (once, under the list), the episode page "Mentioned in this episode". Only the types present in that list are shown.

### 6. Explore top podcasts

Spec #94's landing masthead gains a secondary text link "Explore top podcasts →" to `/top`, beside **Continue with Google**. It gives a visitor who will not sign in yet a next step. No other change to the landing.

## Failure modes

| Risk | Guard |
| --- | --- |
| Read time wrong because word count includes Markdown or citations | Counter strips syntax and `[mm:ss]` markers; unit tests on real summaries |
| Summary date never updates on re-summary | Separate column from `published_at`; handler test re-summarises and asserts it moved |
| Episode list gets slower with entity tags | One batched query per page on the `episode_id` index; budget test: 20 episodes under 50 ms on the Postgres contract fixture |
| Tags show the host or guest | Anchor and guest exclusion in the query |
| Counts disagree with the stream below | Both read the same candidates table with the same filters |

## Non-goals

- Listening time left or progress (spec #90).
- Per-user read-time estimates.
- Tags on inbox rows in this spec (added to the response, rendered later if the inbox wants them).

## Phases

| Phase | Scope |
| --- | --- |
| 1 | `summary_word_count`, `summary_written_at`, backfill command, read time on rows, "Summarised" on the episode page, `get_episodes` stops reading files |
| 2 | `top_entities_for_episodes` + tags on show page and landing rows; `EntityTypeKey`; Explore top podcasts link |
| 3 | Filter counts, after spec #100 Phase 1 |

## Testing

- Unit: word counter on 5 real summaries (with tables, citations, emoji headings) against hand counts within 2%.
- Handler: first summary sets both columns; re-summary moves `summary_written_at` and leaves `published_at`.
- Repository contract (SQLite and Postgres): `top_entities_for_episodes` ranking, anchor and guest exclusion, hidden entities on public routes, 3 per episode.
- API: episode list items carry `summary_word_count`, `summary_written_at`, `key_entities`, `guests`; no summary file is opened while building the list (storage stub raises on read).
- Vitest: read time hidden when the count is null; key shows only types present.

## Open questions

1. 230 words a minute, or measure from how long signed-in users actually spend on summaries once spec #95 exists?
2. Should "Updated" (re-summarised) episodes say why (new model, genre profile from spec #98)? Default here: no.

# Per-Feed Processing Budget

> **Status:** 📝 Draft (2026-11-17)
> **Created:** 2026-11-17
> **Updated:** 2026-11-17
> **Priority:** Critical (#1 of the anti-abuse series 118–123). One follow of a self-hosted feed commits the server to transcribing and summarising every episode that feed publishes, with no limit, so thestill can be used as a free transcription and summarisation API.
> **Related:** [#104 abuse-prevention-and-user-admin](104-abuse-prevention-and-user-admin.md) (quotas, ledger, alerts, console this builds on), [#63 universal-follower-gate](63-universal-follower-gate.md) (processed = followed), [#48 refresh-feed-stage](48-refresh-feed-stage.md), [#74 refresh-on-open](74-refresh-on-open.md), [#88 import-outcomes-and-arriving-soon](88-import-outcomes-and-arriving-soon.md), [#75 llm-call-tracing](75-llm-call-tracing.md), [#122 introduced-feed-visibility](122-introduced-feed-visibility.md) (owns the `FeedTrust` predicate this spec consumes), [#121 signup-trust-and-account-farming](121-signup-trust-and-account-farming.md) (probation, `independent_followers`), [#119 audio-duration-cap](119-audio-duration-cap.md), [#120 spend-guardrails](120-spend-guardrails.md), [#123 abuse-triage-sweep](123-abuse-triage-sweep.md)

---

## Executive summary

Spec #104 limits how many feeds a user can bring in (`feeds_introduced_per_day`, default 10). It does not limit what a feed costs after it is in. Once a feed has a follower, every episode published after the current follow run began goes through the full paid pipeline: transcription, cleaning, summarising and entity extraction. A user who controls the feed controls the spend.

The attack is cheap. Host an RSS feed, follow it, publish audio. Thestill transcribes and summarises each episode, and the user reads the result on the web or through MCP. That is the "reseller / relay" pattern: a free door that costs compute, used to serve someone else's workload.

This spec adds a **per-feed processing budget**. Each feed gets a rolling 24-hour allowance of automatically processed episodes and audio minutes, set by the feed's trust tier from the shared `FeedTrust` predicate of spec #122. A feed only one user follows, and that user brought in, gets a tight allowance. A feed on a top chart, or one an admin approved, has no limit. Episodes over the allowance are **held**, not dropped. They are released when the allowance refills or when an admin approves the feed, and they expire after a week. Audio minutes are attributed to the user who brought the feed in, with a cap across all their feeds, so bringing in more feeds does not multiply the allowance. A new `feed_volume` alert tells the operator when a feed is being held a lot.

## Current state

| Area | Today | Reference |
|---|---|---|
| Refresh enqueue | Both refresh paths call `enqueue_discovered_episodes`, which enqueues the full pipeline for each episode `_discovered_episodes_to_enqueue` returns | [queue_manager.py:1166](../thestill/core/queue_manager.py#L1166), Postgres copy at [postgres_queue_manager.py:418](../thestill/core/postgres_queue_manager.py#L418) |
| Callers | The queued `REFRESH_FEED` handler and the inline `RefreshService.refresh` (CLI, web, MCP, refresh-on-open) | [task_handlers.py:1970](../thestill/core/task_handlers.py#L1970), [refresh_service.py:191](../thestill/services/refresh_service.py#L191) |
| Selection rule | An unfollowed feed returns nothing. Otherwise an episode published since `follow_started_at` is always enqueued; older ones only within the newest `inbox_seed_on_follow` | [queue_manager.py:510](../thestill/core/queue_manager.py#L510), rule at [:530](../thestill/core/queue_manager.py#L530) |
| Back-catalog marker | The rest are flagged `auto_process_excluded` and never revisited by the sweep | [queue_manager.py:538](../thestill/core/queue_manager.py#L538), column at [postgres_schema.py:328](../thestill/repositories/postgres_schema.py#L328) |
| Candidate query | DISCOVERED, no task row, not failed, not excluded, `created_at` within 2 days | [postgres_podcast_repository_episodes.py:586](../thestill/repositories/postgres_podcast_repository_episodes.py#L586), [sqlite_podcast_repository.py:4000](../thestill/repositories/sqlite_podcast_repository.py#L4000) |
| `follow_started_at` | `MIN(created_at)` over the feed's *current* followers, so it resets when the last follower leaves and someone follows again | [sqlite_podcast_repository.py:4235](../thestill/repositories/sqlite_podcast_repository.py#L4235), [postgres_podcast_repository_podcasts.py:1365](../thestill/repositories/postgres_podcast_repository_podcasts.py#L1365) |
| Follow seed | `seed_on_follow` enqueues the newest `INBOX_SEED_ON_FOLLOW` (default 2) unprocessed episodes. It ignores `auto_process_excluded` on purpose | [inbox_service.py:234](../thestill/services/inbox_service.py#L234), [sqlite_podcast_repository.py:4103](../thestill/repositories/sqlite_podcast_repository.py#L4103) |
| Inbox enqueue loop | `enqueue_pipeline_for` is the one loop behind follow-seed, publish fan-out, ad-hoc send and dedup import | [inbox_service.py:71](../thestill/services/inbox_service.py#L71) |
| Feed introduction | `FollowPolicy` writes a `feed_introduced` usage row when a follow takes a feed from 0 to 1 followers | [follow_policy.py:93](../thestill/services/abuse/follow_policy.py#L93) |
| Usage ledger | `usage_events` counts rows per user. `kind` is CHECK-constrained and there is no quantity column | [usage_event_repository.py:34](../thestill/repositories/usage_event_repository.py#L34), [postgres_schema.py:454](../thestill/repositories/postgres_schema.py#L454) |
| Chart membership | `top_podcasts` (keyed on `rss_url`) and `top_podcast_rankings` (current rank per region). Joined to `podcasts` on `rss_url` | [postgres_schema.py:835](../thestill/repositories/postgres_schema.py#L835), [:849](../thestill/repositories/postgres_schema.py#L849) |
| Curation | No podcast-level approved or curated flag today. "curated" exists only as a show-link source. Spec #122 adds `podcasts.trust_tier` and makes `public_pages = TRUE` mean "admin approved" | [postgres_schema.py:242](../thestill/repositories/postgres_schema.py#L242), [#122](122-introduced-feed-visibility.md) |
| Episode duration | Feed-reported `itunes:duration`, stored as text and parsed on read | [postgres_schema.py:301](../thestill/repositories/postgres_schema.py#L301), [alerts.py:70](../thestill/services/abuse/alerts.py#L70) |
| Spend breaker | Off by default (`ALERT_SPEND_BREAKER_MINUTES=0`). When on, it only pauses *new* feeds. Feeds that already have a follower keep processing | [config.py:580](../thestill/utils/config.py#L580), [alerts.py:254](../thestill/services/abuse/alerts.py#L254) |
| "Arriving soon" | Lists only episodes of followed feeds that have an active task | [inbox_repository.py:32](../thestill/repositories/inbox_repository.py#L32) |

## Threat

A walk-through with today's code:

1. Sign up with a Google account. Add `https://attacker.example/feed.xml`. This uses 1 of 10 `feeds_introduced_per_day` and follows the feed.
2. The follow seeds the newest 2 episodes. `follow_started_at` is now.
3. Publish 100 one-hour episodes a day. Every refresh finds them, each has `pub_date >= follow_started_at`, and every one is enqueued with `run_full_pipeline`.
4. Read transcripts and summaries on the web, or pull them through MCP `get_transcript` and `get_summary`.

Rough cost, one feed: 6,000 audio minutes a day. At Google Speech-to-Text with diarisation (about $0.048 a minute, [transcription-providers.md](../docs/transcription-providers.md)) that is about $290 a day before any LLM call. Cleaning, summarising and entity extraction on 100 hours of transcript add to it. The real per-minute figure for the production provider and the LLM stages should come from spec #75 tracing. The total scales with the number of feeds: 10 a day per account, more with more accounts.

Nothing stops it today. The quota limits the feeds brought in, not their output. The spend breaker is off by default and wouldn't pause a feed that already has a follower. No alert looks at a single feed's volume.

Variants the design must also close:

- **Many short episodes.** 500 two-minute episodes a day stay under a minutes cap but cost 500 LLM runs.
- **Lying durations.** The feed says `<itunes:duration>60</itunes:duration>` and serves three hours.
- **Unfollow and refollow.** The sole follower leaving and coming back resets `follow_started_at` and re-seeds the newest 2.
- **Many feeds.** A per-feed cap alone grows with every feed brought in.
- **Sock puppets.** Several accounts follow the attacker's feed to look popular.

## Goals

1. A feed one user brought in and only they follow can't make the server process more than a small, fixed amount a day.
2. One user can't multiply that amount by bringing in more feeds.
3. Popular, charted and admin-approved feeds, including high-volume daily news, keep processing as today.
4. Nothing over budget is lost. It waits, and an admin can release it.
5. The operator hears about a feed that hits its budget, and can approve, pause or release it from the CLI and the console.
6. Single-user mode behaves exactly as today.

## Non-goals

- **Imports and "Send to my inbox".** They already have `imports_per_day` (spec #104 §2.2). An episode started that way is not counted against its feed.
- **Duration caps per episode.** See [#119 audio-duration-cap](119-audio-duration-cap.md).
- **Dollar budgets.** See [#120 spend-guardrails](120-spend-guardrails.md). This spec limits by episodes and minutes, which are known before any money is spent.
- **Detecting account farming.** See [#121 signup-trust-and-account-farming](121-signup-trust-and-account-farming.md). This spec only uses its result.
- **Public visibility of introduced feeds.** See [#122 introduced-feed-visibility](122-introduced-feed-visibility.md).
- **Operator runs.** `thestill reprocess` (spec #106), the per-stage CLI commands and operator-initiated pipelines are not budgeted.

## Design

### Budget classes

This spec does not define its own trust rule. It reads the feed's tier from `FeedTrust.tier(podcast_id)` in `thestill/services/feed_trust.py`, defined by [122-introduced-feed-visibility.md](122-introduced-feed-visibility.md), and maps it to a budget class. Spec #122 owns the tier enum, how tiers are computed and stored (`podcasts.trust_tier`), and when they are re-evaluated. Spec #121 defines probation and the `independent_followers` helper that "established" depends on.

| `FeedTrust` tier | Meaning (spec #122) | Budget class | Budget |
|---|---|---|---|
| `admin` | An admin approved the show (`public_pages IS TRUE`) | `unlimited` | None |
| `chart` | Its `rss_url` is in `top_podcasts` | `unlimited` | None |
| `followed` | At least `feed_trust_min_followers` (2) established followers: active, account at least 14 days old, not on probation | `shared` | Generous: 48 episodes, 1,440 minutes a day |
| `none` | Everything else, which means almost every newly user-introduced feed | `standard` | Tight: 6 episodes, 240 minutes a day, and the introducer cap |

A processing-only override, `podcasts.budget_override`, beats the mapping:

| Override | Effect | Why it exists |
|---|---|---|
| `unlimited` | No budget, whatever the tier | Process a feed without making it public, which spec #122's approval would |
| `standard` | Tight budget, whatever the tier | A feed that earned `followed` or `chart` trust but is being abused |
| `paused` | Zero. Every new episode is held | Stop a feed without banning anyone |
| `NULL` | Use the mapping | Default |

Sock puppets made this week don't make a feed `followed`, because spec #122 counts only established followers. `shared` still has a finite budget, so a set of aged sock puppets buys more allowance, not unlimited allowance. If spec #122 later splits `followed` by count (its open question 7), this mapping gains a row; it never re-derives the rule.

`admit` reads the stored tier, not a fresh evaluation, so the budget costs one column read. A follow or unfollow that changes the tier is picked up when spec #122's hooks re-evaluate it.

### The two limits

A `standard` feed has two rolling 24-hour limits. An episode is admitted only if it fits both:

- **Per feed:** `episodes_per_day` (6) and `minutes_per_day` (240), counted over the feed's admitted episodes. The episode count catches many short episodes and lying durations. The minutes catch a few very long ones.
- **Per introducer:** `introducer_minutes_per_day` (480), counted over every `standard` feed the same user introduced. Ten feeds share one allowance.

Neither limit resets on a new follow. The window belongs to the feed, so unfollowing and refollowing gains nothing. A refollow from zero also costs a `feed_introduced` (spec #104).

**Audio minutes** come from the feed-reported duration. A missing, zero or unparseable duration counts as `unknown_duration_minutes` (60). Once spec #119 measures real durations, the measured value replaces the reported one on the ledger row, so later admissions see the truth. Until then, the episode count bounds a lying feed.

**Who the introducer is.** The user on the feed's most recent committed `feed_introduced` row, if they are still active and still follow it. Otherwise the earliest active current follower, the spec #104 §1.5 stand-in. Every followed feed therefore has an introducer. If the introducer is banned and other users still follow, the next earliest follower becomes the introducer for new rows.

### Settings

One runtime setting, `feed_budget`, holds overrides merged over env defaults, like `alerts` ([settings_registry.py:130](../thestill/services/abuse/settings_registry.py#L130)). It is edited with `thestill settings set feed_budget '{...}'` or the console.

| Key | Env default | Default | Notes |
|---|---|---|---|
| `enforce` | `FEED_BUDGET_ENFORCE` | `false` in Phase 1, then `true` | Off: rows are recorded and holds are only logged (`feed_budget_would_hold`), like `QUOTA_ENFORCE` |
| `episodes_per_day` | `FEED_BUDGET_EPISODES_PER_DAY` | 6 | `standard` tier. `0` means unlimited |
| `minutes_per_day` | `FEED_BUDGET_MINUTES_PER_DAY` | 240 | `standard` tier |
| `introducer_minutes_per_day` | `FEED_BUDGET_INTRODUCER_MINUTES_PER_DAY` | 480 | Across one user's `standard` feeds |
| `shared_episodes_per_day` | `FEED_BUDGET_SHARED_EPISODES_PER_DAY` | 48 | `shared` tier |
| `shared_minutes_per_day` | `FEED_BUDGET_SHARED_MINUTES_PER_DAY` | 1440 | `shared` tier |
| `unknown_duration_minutes` | `FEED_BUDGET_UNKNOWN_DURATION_MINUTES` | 60 | |
| `hold_max_age_days` | `FEED_BUDGET_HOLD_MAX_AGE_DAYS` | 7 | Older held episodes expire |

The follower threshold and account age are spec #122's settings (`feed_trust_min_followers` and its account-age setting), not this spec's, so visibility and budget can't disagree about what "followed" means.

A second setting, `feed_budget_overrides`, holds per-feed and per-user limits: `{"podcasts": {id: {"episodes_per_day": 20}}, "users": {id: {"introducer_minutes_per_day": 1200}}}`. It is edited through the same locked read-modify-write as `quota_overrides` (spec #104 implementation notes). Admins' own introduced feeds are not exempt: the budget class, not the role, decides. An admin can approve their own feed.

The new `Config` fields go beside the quota block ([config.py:557](../thestill/utils/config.py#L557)) and the env parsing ([config.py:977](../thestill/utils/config.py#L977)).

### Data model changes

| Change | Notes |
|---|---|
| `podcasts.budget_override text NULL CHECK (budget_override IN ('unlimited', 'standard', 'paused'))` | Admin override. `NULL` means use the `FeedTrust` mapping. `trust_tier` itself is added by spec #122 |
| `episodes.processing_held_at timestamptz NULL` | Set while an episode is held. Distinct from `auto_process_excluded`, which means "back catalog, never" |
| New `feed_processing_events` | The budget ledger, below |

`feed_processing_events`:

| Column | Type | Notes |
|---|---|---|
| `id` | uuid | |
| `podcast_id` | uuid, FK `podcasts` ON DELETE CASCADE | |
| `episode_id` | uuid, FK `episodes` ON DELETE CASCADE, UNIQUE | One row per episode |
| `introducer_user_id` | uuid NULL, FK `users` ON DELETE SET NULL | |
| `trust_tier` | text | The `FeedTrust` tier at decision time |
| `budget_class` | text | `shared`, `standard` or `paused` at decision time |
| `state` | text | `admitted`, `held`, `released`, `bypassed`, `expired` |
| `audio_seconds` | integer | Reported, fallback or measured |
| `duration_source` | text | `feed`, `fallback`, `measured` |
| `created_at` | timestamptz | When first decided |
| `counted_at` | timestamptz NULL | Set for `admitted` and `released`. The windows sum rows by this |

Indexes: `(podcast_id, counted_at)`, `(introducer_user_id, counted_at)`, and a partial index on `(podcast_id, created_at) WHERE state = 'held'`.

States:

- `admitted`: enqueued within budget at refresh or seed time. Counts.
- `held`: over budget. `episodes.processing_held_at` is set.
- `released`: was held, enqueued later when the budget refilled. Counts from release time.
- `bypassed`: enqueued outside the budget, by an admin release or by a user's import or "Send to my inbox". Does not count against the feed. An import already counts against `imports_per_day`.
- `expired`: held longer than `hold_max_age_days`. The episode gets `auto_process_excluded = true` and loses the hold.

Only `standard` and `shared` feeds get rows. `unlimited` feeds skip the ledger. `paused` feeds get `held` rows.

**Why not `usage_events`.** That ledger counts one action per row per user, with reserve and commit states, and has no quantity column. Its `kind` CHECK would also need changing, which on SQLite means rebuilding the table. The budget needs per-feed counting, minutes, and a hold life cycle. A separate table keeps both simple. The spec #104 per-user views (`thestill user show`, the console, the digest) read the introducer's minutes from this table. See open question 5.

Migration follows the three-part pattern in spec #104: an Alembic revision in [migrations/versions/](../thestill/migrations/versions/) (the next free number at merge; the head is 0028 today and spec #122 claims 0029), the same DDL in `postgres_schema.SCHEMA_SQL`, and the SQLite `CREATE TABLE` plus `ALTER TABLE` in `SqlitePodcastRepository._run_migrations`. The `processing_held_at` column also goes into the SQLite-to-Postgres promotion column list in [db_promotion.py](../thestill/utils/db_promotion.py), as `auto_process_excluded` does.

The two candidate queries add `AND e.processing_held_at IS NULL`, so a held episode is not picked up again by the refresh sweep. `get_recent_unqueued_unprocessed_episodes` does not, because the seed goes through the budget anyway.

### Where the check lives

A new `FeedBudgetService` in `thestill/services/abuse/feed_budget.py`, built in `build_abuse_services` ([wiring.py](../thestill/services/abuse/wiring.py)). Its one entry point:

```python
admitted = feed_budget.admit(podcast_id, candidates, source="refresh-feed")
# candidates: [(episode_id, audio_url, duration_text)], newest first
# returns the subset to enqueue; the rest are held (or only logged when enforce is off)
```

`admit` runs one short transaction:

1. Take the introducer's lock, then the podcast's lock, the same order as spec #104 (user locks before podcast locks). On Postgres: `pg_advisory_xact_lock(hashtextextended('feed_budget:' || id, 0))`. On SQLite: `BEGIN IMMEDIATE`.
2. Read the budget class (`budget_override`, else the mapping of `FeedTrust.tier()`) and the introducer.
3. Sum the feed's and the introducer's counted rows in the last 24 hours.
4. Walk the candidates newest first. Admit each one that fits both limits, and hold the rest. Newest first means a news feed processes its latest bulletins.
5. Write the rows and set `processing_held_at` on the held episodes. Commit.

The caller enqueues the admitted episodes after the commit. A crash between commit and enqueue leaves an `admitted` row without a task. The next refresh finds the episode again and admits it again, counting it twice. That fails closed and is acceptable.

It is called from every *automatic* path, and only those:

| Path | Call site |
|---|---|
| Queued `REFRESH_FEED` and inline refresh | `_discovered_episodes_to_enqueue` ([queue_manager.py:482](../thestill/core/queue_manager.py#L482)), after the back-catalog split. `enqueue_discovered_episodes` gains a `budget` argument that both callers pass. `None` means no budget, for single-user mode and tests. The worker's handler state needs the budget wired in, since it has no abuse services today |
| Follow seed | `InboxService.seed_on_follow` ([inbox_service.py:259](../thestill/services/inbox_service.py#L259)), with `source="follow_seed"` |
| Publish fan-out safety net | `InboxService.fanout_on_publish` ([inbox_service.py:228](../thestill/services/inbox_service.py#L228)). Published episodes are already processed, so this is normally a no-op, but it stays behind the budget so it can't become a bypass |

Imports, ad-hoc sends and dedup imports call `enqueue_pipeline_for` without the budget. When one of them starts a held episode, it clears `processing_held_at` and marks the row `bypassed`.

Once admitted, an episode runs its whole pipeline. The budget is spent at admission, not per stage.

In single-user mode `admit` returns every candidate and writes nothing.

### Release and expiry

A job on the spec #104 periodic runner ([periodic_runner.py](../thestill/core/periodic_runner.py)) every 10 minutes:

1. For each feed with `held` rows and at least one follower, run `admit` over its held episodes, newest first. Admitted ones become `released`, lose `processing_held_at` and are enqueued with `initiated_by="feed-budget-release"`.
2. Mark held rows older than `hold_max_age_days` `expired` and set `auto_process_excluded` on their episodes.

A feed that lost its last follower releases nothing (spec #63's rule). Its held episodes expire.

When a feed becomes `unlimited` (a spec #122 approval, a chart match, or `budget_override = unlimited`), its held episodes are released at once, as `bypassed`. Spec #122's `FeedTrust.evaluate` logs `feed_trust.changed` on a crossing; the budget service listens for the same crossing, and the release job catches anything missed on its next tick.

### What users see

- **Podcast page.** A held episode shows a "Waiting" label, for signed-in followers only, with the text: "This show published more than thestill processes in a day for a show few people follow. It will be processed by {time}." The time is when the oldest counted row leaves the window. A held episode past its expiry shows as unprocessed, as back catalog does today.
- **Arriving soon.** `ARRIVING_WHERE_SQL` also matches held episodes of followed feeds (`processing_held_at IS NOT NULL`). They show with a "Waiting" stage instead of a task stage. Today the strip shows only episodes with an active task, so a held episode would otherwise disappear without a word.
- **Inbox.** Unchanged. A held episode gets an inbox row when it is processed and published, like any other.
- **Getting it now.** "Send to my inbox" on a held episode works as today and counts against `imports_per_day`. This lets a legitimate user get the one episode they want. An attacker gets 5 a day this way, which is already accepted under spec #104.
- **No new error.** Following and adding a feed never fail because of the budget. The budget acts later, on episodes.

The episode API gains `processing_held: bool` and `processing_expected_by: datetime | null`. The labels come from the server, as spec #104 quota messages do.

### Admin and CLI

New commands in the existing `podcast` group ([cli.py:5554](../thestill/cli.py#L5554)), beside spec #122's `approve`, `hide`, `reset-visibility` and `trust`. Approving a show for the public (`thestill podcast approve`, spec #122) also lifts its budget, since `admin` maps to `unlimited`. Like the spec #104 commands, they run straight against the database and print an explanation and exit 1 in single-user mode.

```bash
thestill podcast budget [--over] [--json]                 # budgeted feeds: trust tier, budget class, 24h episodes/minutes, held count
thestill podcast budget --podcast-id X [--json]           # one feed: introducer, established followers, held episodes
thestill podcast set-budget --podcast-id X unlimited|standard|paused|auto --reason "..."
thestill podcast release-held --podcast-id X [--episode <slug>]... [--all] [--dry-run]
thestill podcast budget-limit --podcast-id X episodes_per_day 20    # or "default"
```

`release-held` enqueues held episodes outside the budget, marked `bypassed`. New audit actions, named like spec #122's `podcast.*` actions, are `podcast.set_budget`, `podcast.release_held` and `podcast.budget_limit`. `target_user_id` is the introducer, and `details` holds `podcast_id`, the old and new values, the reason and the episode count. The audit table has no podcast column, so the console filters these by action.

Console additions (spec #104 Phase 4):

- **Budget view on the "Feeds" tab** that spec #122 adds (multi-user only): one row per budgeted feed, with title, introducer, established followers, trust tier, budget class, 24h episodes and minutes against the limit, and held count. The default sort is held count in the last 24 hours. Actions: Approve (spec #122's), Budget override (unlimited, standard, paused, auto), Release held, Limits.
- **`/admin/users/:id`**: the follows list, which spec #122 already extends with tier and visibility, also shows each introduced feed's budget class and held count, and the usage block shows the introducer's minutes against their cap.

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/admin/feeds/budget` | The list. Query: `trust_tier`, `budget_class`, `over`, `q`, pagination. Sits beside spec #122's `/api/admin/feeds/review` |
| GET | `/api/admin/feeds/{podcast_id}/budget` | Detail with held episodes |
| PUT | `/api/admin/feeds/{podcast_id}/budget` | Body `{override, reason}`. `override` may be `auto` |
| POST | `/api/admin/feeds/{podcast_id}/release` | Body `{episode_ids?}`. Empty releases all |
| PUT | `/api/admin/feeds/{podcast_id}/limits` | Body `{episodes_per_day?, minutes_per_day?}`. Empty clears |

### Alerts

A new rule in `AlertService.evaluate` ([alerts.py:176](../thestill/services/abuse/alerts.py#L176)), with the threshold added to `ALERT_THRESHOLDS`:

| Alert | Fires when | Default | Env |
|---|---|---|---|
| `feed_volume` | One feed has ≥ N `held` rows created in the last 24 hours | 10 | `ALERT_FEED_VOLUME` |

It is de-duplicated per feed (`alerts.last_sent.feed_volume.<podcast_id>`), once an hour like the others. The message names the feed, its introducer's email, the established follower count, and the admitted and held numbers. It suggests the obvious actions: `thestill podcast approve --podcast-id X` (spec #122) for a real show, `thestill podcast set-budget --podcast-id X unlimited` to process it without making it public, or `thestill user ban <email> --remove-introduced-feeds` for abuse. Machine-facing `details` carry ids and numbers only, as spec #104 requires.

With `enforce` off, the rule counts would-hold decisions instead, so Phase 1 shows which feeds the budget would catch.

The digest gains "Feeds over budget (24h)": the top 5 feeds by held count, and the top 5 introducers by minutes.

## Edge cases

- **Legitimate daily news.** An hourly bulletin publishes 24 short episodes a day. If it is on a chart, it is `unlimited`. If several established users follow it, it is `followed`, so `shared`, and fits. If one user brought it in and nobody else follows it, it gets 6 a day, newest first, and `feed_volume` fires. The operator approves it once, or sets `budget_override = unlimited`. This is the main cost of the design, and the reason the alert exists.
- **Chart feed with a different URL.** Spec #122's chart match is on `rss_url`. A charted show added through a redirect or a different scheme is `none`, so `standard`, until an admin approves it. See open question 1.
- **Unfollow and refollow.** The window belongs to the feed and survives. The re-seed of the newest 2 goes through `admit`, and the refollow costs a `feed_introduced`.
- **Several accounts following an attacker feed.** New accounts aren't established, so the feed stays `none` and `standard`. Aged accounts make it `followed` and `shared`, which is still capped, and `feed_volume` still fires on holds. Spec #121 handles the accounts themselves.
- **Ban.** Spec #104 moves the banned user's follows aside. A feed with no followers left stops: the release job skips it and its holds expire. If others still follow, the next earliest active follower becomes the introducer for new rows. `--remove-introduced-feeds` behaves as today. Unbanning restores follows, and held episodes that haven't expired are released on the next tick.
- **Tier changes mid-window.** The window is recomputed at each `admit` from rows with `counted_at` in the last 24 hours. A feed that drops from `followed` to `none` (followers left, or one was banned) may already be over the `standard` limit, and simply holds until it isn't.
- **Feeds brought in before this spec.** They have no rows. Their first refresh after rollout starts the window. Episodes already queued are not touched.
- **Operator-added feeds.** If `thestill add` in multi-user mode leaves a feed with no follower, the follower gate already stops it. If it follows on the operator's behalf, the feed is budgeted like any other until approved (spec #122) or given `budget_override = unlimited`. See open question 6.

## Rollout

1. **Phase 1: record only.** Schema, `FeedBudgetService` with `enforce = false`, the call sites, and the read-only `thestill podcast budget`. Every decision is written, held episodes are still enqueued, and `feed_budget_would_hold` is logged. Run it for a week. Check the defaults against real feeds with `thestill podcast budget --over`. Approve the legitimate high-volume feeds in advance.
2. **Phase 2: enforce.** Holds, `processing_held_at`, the release and expiry job, the user-facing labels and Arriving-soon change, `set-budget`, `release-held`, `budget-limit`, the audit actions. Phase 2 needs spec #122's Phase 1 (`FeedTrust` and `trust_tier`); until then Phase 1 here can only log with every feed treated as `none` except charted ones. Flip `enforce` with `thestill settings set feed_budget '{"enforce": true}'`, then change the env default in a later release.
3. **Phase 3: visibility.** The `feed_volume` alert, the digest section, the budget view on spec #122's Feeds tab and the user-page additions.

`docs/configuration.md` gains the new env vars. `docs/web-server.md` and `specs/02-api-reference.md` gain the admin routes. `CLAUDE.md` gains the `thestill podcast budget`, `set-budget` and `release-held` lines.

## Testing

- **Selection:**
  - A `standard` feed with 10 new episodes admits the newest 6 and holds 4. With `enforce` off, it admits all 10 and logs 4 would-holds.
  - The minutes limit holds a long episode when the count still has room.
  - A missing duration counts as `unknown_duration_minutes`.
  - The introducer cap holds episodes on a third feed when two others used it up.
  - The class follows `FeedTrust.tier()` (stubbed): `admin` and `chart` write no rows and admit everything, `followed` gets the `shared` limits, `none` the `standard` ones.
  - `budget_override` beats the tier in both directions, and `paused` holds everything.
  - The budget never computes trust itself: a test stubs `FeedTrust` and asserts no follower or chart query runs in `admit`.
- **Paths:** the queued refresh, the inline refresh, the follow seed and the fan-out net all go through `admit`. Import and "Send to my inbox" don't, and they mark a held episode `bypassed` and clear its hold.
- **Window:** unfollow and refollow by the sole follower doesn't reset the count. A held episode is not picked up again by the next refresh.
- **Concurrency, against Postgres and SQLite:** two refreshes of the same feed in parallel admit at most the limit between them. Parallel `admit` calls for two feeds of one introducer respect the introducer cap. No deadlock between `admit` and a follow.
- **Release and expiry:** the job releases held episodes newest first once rows leave the window. It releases nothing for a feed with no followers. It expires holds older than `hold_max_age_days` and sets `auto_process_excluded`.
- **Admin:** a spec #122 approval or `set-budget unlimited` releases holds as `bypassed`. `release-held` enqueues outside the budget. Every write produces an audit entry. Routes are admin-only.
- **Alerts:** `feed_volume` fires once per feed per hour at the threshold and names the introducer. With `enforce` off, it counts would-holds.
- **User-facing:** a held episode appears in Arriving soon for followers with `processing_held: true` and an expected time. It appears there for no one else.
- **Single-user mode:** `admit` is a pass-through, nothing is written, and the existing suite passes unchanged.

## Open questions

1. **Chart matching.** Exact `rss_url` equality misses charted shows whose feed URL differs (scheme, trailing slash, redirect). That is spec #122's predicate to fix (for example by also matching `apple_url_source = 'chart'` or a normalised URL); this spec inherits whatever it decides.
2. **Measured duration.** This spec assumes spec #119 records a measured duration early enough (at download, or from the transcription provider) to correct the ledger. Whether Dalston's URL path, which skips the download, reports duration before transcription finishes is unverified.
3. **Default limits.** 6 episodes, 240 and 480 minutes are guesses meant to fit a few daily shows per user. Phase 1 data decides. Should the limits become per-cost rather than per-minute once spec #120 has a dollar figure per stage?
4. **Asking for approval.** Should a follower be able to press "Ask to process this show" on a held episode, which writes to the admin console? This spec leaves the label informational.
5. **One ledger or two.** If spec #120 adds a quantity or cost column to `usage_events`, should `feed_processing_events` fold into it as a new kind? The hold life cycle would still need the episode column.
6. **Operator-added feeds in multi-user mode.** Whether `thestill add` on a hosted server creates a follow, and for whom, wasn't checked. If it follows as an admin, should operator-added feeds be approved (spec #122) on add?
7. **Public pages.** Should held and expired episodes of a `standard` feed appear on public pages at all? This overlaps with spec #122.

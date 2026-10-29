# Product Health Metrics: Activation, Engagement, Retention

> **Status:** 📝 Draft (2026-10-29)
> **Created:** 2026-10-29
> **Updated:** 2026-10-29
> **Priority:** Medium. New readers are arriving (three on 2026-10-29), and the next product changes (a post-sign-in follow step, a tutorial, connector banners) need a number that says whether they worked.
> **Author:** Product & Engineering
> **Related:** [#95 product-analytics](95-product-analytics.md) (Layer A, `/analytics`, the opt-out rules), [#103 content-popularity](103-content-popularity.md) (`user_episode_views`, `listened_at`, decision D5), [#29 per-user-inbox-fanout](29-per-user-inbox-fanout.md) (delivered episodes), [#50 scheduled-briefings](50-scheduled-briefings.md) (reader timezone), [#78 remote-mcp-access](78-remote-mcp-access.md) / [#108 claude-connector-page](108-claude-connector-page.md) / [#109 mcp-oauth](109-mcp-oauth.md) (the connector), [#86 quality-feedback-loop](86-quality-feedback-loop.md) (quality reports)

---

## Executive summary

`/analytics` reports many numbers and none of them decides anything.
This spec picks four, each in the form "% of readers who do X by time
Y", and makes them the top of the page:

| Metric | Readers | Definition |
|---|---|---|
| **Activation** | New | % who, within 7 days of signup, follow ≥1 show **and** get delivered value on a later day than the signup day |
| **Engagement** (main metric) | Existing | % of members with **≥2 value days** in a week |
| **Week-4 retention** | New | % with ≥1 value day in days 28–35 after signup |
| **Weekly return** | Existing | % of readers with a value day last week who have one again this week |

A **value day** is a day in the reader's own timezone on which they
read an episode, opened a briefing, played audio, or made a successful
Claude connector call.

The product tables already hold every fact these need except one:
connector use per day. That lives only in PostHog today, which drops
readers who opt out. This spec adds one small table for it, the
`/analytics` Scorecard, the CLI output, and a light experiment
protocol, since the volume is too low for A/B tests for months.

## Problem and evidence

**The current numbers don't meet the principles below.** From spec #95
as built:

| Today | Problem |
|---|---|
| Activation: separate steps (follow within 24 h and 7 days, first briefing, ≥3 reads in week one) | No single go/no-go number. "Followed ≥1 show" can be gamed: a forced follow in onboarding moves it while nobody gets value. |
| Engagement: episodes read per active user per week | An average. One heavy reader moves it. |
| Engagement: briefings opened ÷ generated | Counted per briefing, not per reader; same problem. |
| Retention: active in weeks 1/2/4/8, where active = a read, a follow or a briefing opened | A follow is setup, not value. Connector use doesn't count, so the more someone uses Claude, the less retained they look. |

**What the first new readers did.** PostHog, 2026-10-28 10:34 to
2026-10-29 10:25 UTC: three non-admin readers signed in for the first
time, all in the UK and all from a public episode page ("Read the full
summary"). They read 7, 8 and 5 times. None followed a show. Each one
landed, read, and left with nothing that would bring them back. The
follow → delivery → return loop never started. The activation
definition below is built to measure that loop.

**Volume.** At 2–3 signups a day on a good day, telling a 20% → 30%
activation rate apart (two-sided α = 0.05, power 0.8) takes about 290
signups per arm, about 580 in all: 7–10 months for one test. A 10% →
40% change needs about 32 per arm, so 3–4 weeks. Until volume grows,
only large effects can be seen, and changes are read cohort by cohort
(§6), not A/B tested.

## Principles

Every metric here must:

1. **Be measurable** from data we already hold or one fact we add once,
   and be able to say "keep this change or not".
2. **Be moveable** by product changes.
3. **Not be an average.** Each one is the share of readers who cross a
   threshold.
4. **Correlate with retention.** Activation and engagement are proxies.
   §7 says how and when we check that they track week-4 retention.
5. **Say whether it's about new or existing readers.** Experiments
   target new readers first.
6. **Be hard to game.** Each metric names the cheap way to move it and
   the clause or guardrail that blocks it.

## Design

### 1. The population

The same as every Layer A query today (`_REAL_USERS`): every user row
that isn't an admin or an excluded email.

- **Opted-out readers are counted.** A reader who turned off usage
  analytics, or whose browser sends GPC, is in every denominator. Their
  value days come only from facts kept for everyone: inbox read
  transitions and briefing opens. So they are undercounted, never
  dropped. This follows spec #95 D2 (state-based metrics count
  everyone) and spec #103 D5 (facts that exist only to measure follow
  the opt-outs).
- **The page shows how many readers are opted out**, so the size of
  the undercount is visible. GPC requests can't be counted per reader
  without tracking them, so that part of the undercount is only
  described.

### 2. Value days

A **value day** is a calendar day in the reader's timezone with at
least one of the following:

| Fact | Source | Kept for |
|---|---|---|
| Read an inbox episode | `user_episode_inbox.state = 'read'`, `state_changed_at` (latest change only) | everyone |
| First opened an episode, from any route | `user_episode_views.first_viewed_at` | readers who allow usage analytics |
| Played audio | `user_episode_progress.listened_at` | readers who allow usage analytics |
| Opened a briefing | `user_briefings.opened_at` | everyone |
| Made ≥1 successful connector call | `user_connector_days.day` (new, §4) | readers who allow usage analytics |

**Not value days:** a follow (setup), a sign-in, a search with no
episode opened, a settings change. They leave the reader no better
informed.

**Timezone.** A day is the reader's local day from
`user_briefing_schedules.timezone_name`, or UTC when the reader has no
schedule. UTC alone would split a California reader's evening into two
days.

**Known undercount.** Re-reading an episode already opened on an
earlier day leaves no new row in `user_episode_views`, so it doesn't
count. Reading new episodes is the value this product gives, so we
accept this.

### 3. The four metrics

#### 3.1 Activation (new readers)

> % of new readers who, within 168 hours of signup, **(a)** follow at
> least one show **and (b)** have a *delivered* value day on a local
> day after their signup day.

- **Delivered** means the value came to the reader rather than the
  reader to it: an episode with a row in `user_episode_inbox` for that
  reader (inbox read, or first open of an inbox episode), a briefing
  opened, or a connector call. Coming back from Google to an unrelated
  episode is welcome, but it isn't the loop.
- **Graded only when mature:** a reader enters the denominator 168 h
  after signup, as the current activation steps do.
- **Reported** per signup week for the last 8 weeks, plus overall for
  the selected window, by `signup_source`, with the activated count
  next to the percentage.
- **Can be completed in the window:** a follow puts up to 2
  back-catalogue episodes in the inbox at once (`INBOX_SEED_ON_FOLLOW`, default 2), so
  there's something to come back for even when a show doesn't publish
  that week.
- **Gaming:**
  - A forced follow is blocked by clause (b).
  - The main remaining risk is making signup harder, which keeps only
    motivated readers and raises the percentage while the number of
    activated readers falls. The guardrail is the **activated count per
    week** plus sign-in completion (§3.5). A change that raises the
    percentage and lowers the count is a loss.

#### 3.2 Engagement: the main metric (existing readers)

> % of members with **≥2 value days** in a week (Monday–Sunday, reader
> local days).

- **Members** are readers who signed up at least 7 days before the week
  starts (new readers are measured by activation) and had at least one
  value day in the 28 days before the week. Without the 28-day clause,
  accounts abandoned long ago would pile up in the denominator and the
  number would fall forever, whatever the product does.
- **Why 2 days:** podcasts publish weekly and the briefing is daily, so
  two days a week is the smallest pattern that looks like a habit
  rather than a visit. §7 checks this against the real distribution.
- **Counts days, not reads,** so one long reading session is one day.
  This is why it's not an average.
- **Includes the connector,** so if Claude replaces some web reading,
  the number doesn't drop.
- **Reported** per week for the last 8 complete weeks, with the
  engaged count. The count is the business number, the percentage the
  product number.
- **Gaming:** extra notifications or emails could buy value days. The
  guardrails are early unfollows and switched-off briefings (§3.5).

#### 3.3 Week-4 retention (new readers)

> % of new readers with ≥1 value day in days 28–35 after signup.

- Uses the week index the cohort table already has (week k = days 7k to
  7k + 7 after signup, filled only once the week has passed).
- This is the long-term truth that activation is meant to predict.
  It's reported per signup week.

#### 3.4 Weekly return (existing readers)

> % of members with ≥1 value day in week W − 1 who have ≥1 value day in
> week W.

This is the week-to-week view of the same habit. It falls before
engagement does when a change annoys existing readers, which is the
failure mode the Netflix lesson warns about.

#### 3.5 Guardrails

These are not goals. A change that moves a main metric and breaks a
guardrail has not succeeded.

| Guardrail | Source | Notes |
|---|---|---|
| Activated readers per week (count) | A | Guards activation against signup-filtering |
| Sign-in completion, `login_started` → `login_completed` | B | Guards against friction added before sign-in |
| Follows undone within 7 days | B (`follow`, `unfollow` by person) | A follow deletes its row on unfollow, so Layer A can't see it |
| Briefing schedules switched off | A, `user_briefing_schedules.enabled = false` with `updated_at` in the window | Approximate: any edit to an off schedule bumps `updated_at` |
| Quality reports per 1,000 reads | A, once spec #86 ships | Guards against pushing more, worse content |

Layer B guardrails are read in PostHog and linked from the page. They
are directional, because opted-out readers send nothing.

### 4. New fact: `user_connector_days`

The one fact no table holds: on which days a reader used the
connector. `mcp_tokens.last_used_at` keeps only the latest use, and
spec #109's OAuth grants have the same shape.

```sql
CREATE TABLE IF NOT EXISTS user_connector_days (
    user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    day date NOT NULL,             -- the reader's local day (§2)
    PRIMARY KEY (user_id, day)
);
CREATE INDEX IF NOT EXISTS idx_connector_days_day ON user_connector_days(day);
```

- **Where it's written.** In `McpHttpRuntime._count_tool_call`
  ([mcp_http.py](../thestill/web/mcp_http.py)), next to the
  `mcp_tool_call` capture, for `ok` calls by a signed-in user, whether
  they authenticate with a personal URL or OAuth. It doesn't depend on
  PostHog being configured. It writes
  `INSERT … ON CONFLICT DO NOTHING` through a new
  `ConnectorDayRepository`.
- **Write volume.** One row per reader per day. An in-process
  `(user_id, day)` set skips the database after the first call of the
  day, and `ON CONFLICT` covers multiple workers.
- **Opt-outs (spec #103 D5).**
  - Rows are not written for a reader with
    `usage_analytics_enabled = false`, or for a call carrying `Sec-GPC: 1`.
  - Turning the switch off deletes the reader's rows in the same
    transaction as the other measurement facts
    (`UserRepository.update_usage_analytics`).
  - The service doesn't need this table to run; it exists to measure.
- **Failure isolation.** The insert is its own statement. A failure is
  logged at warning with `exc_info`, and the tool call returns as
  normal.
- **Both backends.** Postgres via Alembic `0026` with the DDL mirrored
  in `SCHEMA_SQL`; SQLite via `SqlitePodcastRepository`'s column guards
  (FM-6), even though SQLite has no analytics.
- **No backfill.** PostHog holds the history, but it misses opted-out
  readers and can't be joined to tables. The Scorecard says connector
  days start on the migration date.
- **Spec #108's success number** (non-admin readers with ≥1 tool call a
  week) becomes a Layer A count from this table. The PostHog figure
  stays for the `client` breakdown.

### 5. Surfaces

**`/analytics` Scorecard.** A new first section, above Signups:

- Four rows (activation, engagement, week-4 retention, weekly return).
  Each shows the latest graded value as "pct (count / eligible)" and the
  last 8 weeks as inline bars, using the existing tokens with no chart
  library (spec #95 §4).
- Groups under five readers are greyed out, not hidden (spec #95 D1).
- A guardrails row below them, with the Layer A values and links for
  the Layer B ones.
- A footnote with the opted-out count, the connector-days start date,
  and the definitions.

**Existing sections:**

- "Reads per active user per week" and "Briefings opened ÷ generated"
  stay, labelled **diagnostic**. They help explain a change, never
  decide one.
- The retention cohort table switches "active" to "has a value day"
  (§2): follows drop out and connector days come in. The page marks the
  switch date, because older cells were computed the old way.

**CLI.** `thestill analytics` prints the Scorecard first, and `--json`
gains a `scorecard` object. A `--scorecard` flag prints only that
block, for a quick daily check.

**Code.** `AnalyticsRepository.scorecard(as_of)` is one SQL statement
per metric over a shared `value_days` CTE built from the §2 sources. It
returns Pydantic models (`ScorecardReport`, `MetricSeries`,
`MetricPoint`). `AnalyticsService` owns the window, as it already does.

### 6. Experiment protocol (until A/B is possible)

Each product change aimed at a metric gets a row in
`docs/experiments.md` **before** it ships:

| Field | Example |
|---|---|
| Change | After signing in from an episode page, offer to follow that show as the next step |
| Metric and guardrails | Activation; activated count, sign-in completion, follows undone within 7 days |
| Expected effect | 0% → ≥30% of graded readers |
| Shipped | Version and date, plus a PostHog annotation |
| Read on | The first date two full signup weeks after shipping are graded |
| Result | Before vs after cohorts with counts, and a decision |

Rules:

- **One change per metric at a time.** Two activation changes in the
  same weeks can't be told apart.
- **Expect big effects.** At current volume, aim for changes that could
  plausibly double a metric. A null result on a small change means
  "not visible", not "no effect".
- **Look at the readers.** With a handful of new readers a week,
  read each new reader's path (PostHog person timeline, signed-in
  events only) alongside the cohort numbers.
- **A/B later.** Once signups reach about 150 a week, a deterministic
  hash of the user id can split arms. That is a later spec.

### 7. Validating the proxies

Principle 4 has to be checked, not assumed. Once **≥50 readers** have
been graded for both activation and week-4 retention:

1. Compare week-4 retention for activated and not-activated readers. If
   activation doesn't separate them clearly (for example 40% vs 10%),
   the definition is wrong: try its parts (follow alone, return alone,
   ≥3 value days in week one) and keep the one that separates best.
2. Look at the distribution of weekly value days for members. If almost
   everyone is at 0 or ≥4, the 2-day threshold isn't telling readers
   apart; move it.
3. Write the result into this spec's Decisions and update the
   definitions in one place (§3), so the page and CLI follow.

## Phases

| Phase | Scope | Ships alone |
|---|---|---|
| 0 | This spec. Spec #95's question table gains the Scorecard rows and points here. README index. | Yes (docs) |
| 1 | `user_connector_days` (Alembic `0026`, `SCHEMA_SQL`, SQLite guard), write path, opt-out delete; `scorecard()` repository, models and service; `/analytics` Scorecard section; CLI `--scorecard` and `--json`; Postgres contract tests | Yes |
| 2 | Cohort table switches to value days with the marked switch date; diagnostic labels; `docs/analytics.md` definitions; `docs/experiments.md` with its first row (the post-sign-in follow step, built in its own PR) | Yes |
| 3 | Data-gated (≥50 graded readers): §7 validation, threshold review, Decisions updated | Yes (docs, maybe a definition change) |

## Acceptance

Postgres contract tests (scratch database via `TEST_DATABASE_URL`)
with fixtures for each case:

- **Activation**
  - A read on the signup day doesn't satisfy clause (b).
  - Reading an episode not in the reader's inbox doesn't satisfy it.
  - A briefing open or a connector day on day 3 does.
  - A follow with no return isn't activated.
  - A reader 100 h after signup isn't graded.
- **Engagement**
  - Two reads on one local day is one value day.
  - A reader whose last value day was 40 days before the week isn't a
    member.
  - A reader in their first week isn't a member.
- **Timezone**
  - A reader with `America/Los_Angeles` reading at 23:30 and 00:30 UTC
    on the same local evening has one value day.
  - A reader with no schedule is bucketed in UTC.
- **Population**
  - Admins and excluded emails never appear.
  - An opted-out reader appears in denominators with inbox and briefing
    days only.
- **Connector days**
  - Written once per reader per day, only for `ok` calls.
  - Not written for an opted-out reader or a GPC call.
  - Deleted when the switch is turned off.
  - A failing insert doesn't fail the tool call.
- **Week-4 retention and weekly return** match hand-computed values on
  a fixture with three cohorts.

After deploy, check that `thestill analytics --scorecard` on prod
prints all four metrics with counts (`@live`).

## Out of scope

- A/B assignment and feature flags (§6 says when).
- New PostHog events. The Scorecard is Layer A only, and nothing joins
  PostHog to product tables.
- Session replay.
- Per-reader dashboards or streaks shown to readers. `user_connector_days`
  is for measurement, like `user_episode_views`.
- Revenue or paid-plan metrics.

## Open questions

1. **Does a search with results count as value?** Proposed no: it
   leaves no state, and a search that opens nothing didn't inform the
   reader. Revisit if the connector or search becomes the main way
   people use the product.
2. **Should opted-out readers be dropped from the day-based
   denominators** rather than counted with fewer facts? Proposed no,
   while they are a small share (the page shows the count).
3. **Activation window: 7 or 14 days?** A reader who follows a
   fortnightly show may not get a new episode in 7 days, though the
   back-catalogue seed covers most of that. Start at 7 and check in §7.
4. **Is a share a value day?** It's an event only today. Proposed no
   until sharing shows up as a real behaviour.

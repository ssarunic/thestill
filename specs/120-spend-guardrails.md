# Spend Guardrails: Dollars, Not Requests

> **Status:** 📝 Draft (2026-11-17)
> **Created:** 2026-11-17
> **Updated:** 2026-11-17
> **Priority:** High (severity #3 of the anti-abuse series, with #4 "no dollar-level spend tracking" merged in). The only automatic spend stop, the spend breaker, ships switched off, and nothing in the product counts money.
> **Related:** [#104 abuse-prevention-and-user-admin](104-abuse-prevention-and-user-admin.md) (alerts, `spend_breaker`, runtime settings, open question 4 "processing pause"), [#75 llm-call-tracing](75-llm-call-tracing.md) (the observer hook this reuses), [#63 universal-follower-gate](63-universal-follower-gate.md) (why a follow costs money), [#95 product-analytics](95-product-analytics.md) (the admin `/analytics` page), [#66 aws-single-ec2-hosting](66-aws-single-ec2-hosting.md), [#106 episode-reprocess](106-episode-reprocess.md) (backfilling skipped stages). Siblings: [118-per-feed-processing-budget.md](118-per-feed-processing-budget.md), [119-audio-duration-cap.md](119-audio-duration-cap.md), [121-signup-trust-and-account-farming.md](121-signup-trust-and-account-farming.md), [122-introduced-feed-visibility.md](122-introduced-feed-visibility.md), [123-abuse-triage-sweep.md](123-abuse-triage-sweep.md)

---

## Executive summary

Thestill users never prompt a model directly, so request counts say little about cost. Money goes to two meters: audio minutes sent to a transcription provider, and LLM tokens spent cleaning, summarising, extracting entities, translating and narrating. A user who brings in 50 feeds of three-hour episodes costs far more than one who sends 10,000 API requests. The lesson from the "token fraud" incidents is to watch dollars, hourly, and to stop automatically when they run away.

Today the hosted server counts neither in dollars. Spec #104's `spend_breaker` counts queued audio minutes, but it ships off. LLM token counts reach the log as `llm_usage` events, but nothing stores, prices or alerts on them, and some providers' structured calls are not logged at all.

This spec adds, in order:

1. **The spend breaker on by default** in hosted multi-user mode, with a better duration estimate.
2. **A spend meter.** Every billed LLM call and every transcription is recorded with an estimated cost from a configurable price table, attributed to stage, episode, feed and (where possible) the user who brought the feed in or started the work. Aggregated hourly and daily.
3. **A daily dollar budget** with a soft level (alert) and a hard level (pause). The hard level introduces the `processing_paused` switch from #104 open question 4, plus cheaper "degrade" steps before a full stop.
4. **Visibility.** Hourly spend on the admin analytics and status pages and in the daily digest.
5. **An operator runbook** for provider-side hard limits, which hold even if all of the above has a bug.

## Current state

| Area | Today | Reference |
|---|---|---|
| Spend breaker defaults | `alert_spend_breaker_minutes = 0` (off) and `alert_spend_breaker_action = "none"`, from `ALERT_SPEND_BREAKER_MINUTES` / `_ACTION` | [config.py:580](../thestill/utils/config.py#L580), env at [:994](../thestill/utils/config.py#L994) |
| Breaker actions | `none` or `pause_new_feeds` only | [settings_registry.py:138](../thestill/services/abuse/settings_registry.py#L138) |
| Breaker rule | Sums durations of episodes that got a download, downsample or transcribe task in 24h; trips at `>=`; pauses `new_feeds_enabled` as `system(spend_breaker)` | [alerts.py:254](../thestill/services/abuse/alerts.py#L254), [:323](../thestill/services/abuse/alerts.py#L323) |
| Breaker reading | Per-episode `episodes.duration`; missing or junk counts as **0** seconds | [queue_manager.py:1790](../thestill/core/queue_manager.py#L1790), [postgres_queue_manager.py:934](../thestill/core/postgres_queue_manager.py#L934), [alerts.py:68](../thestill/services/abuse/alerts.py#L68) |
| Duration source | Feed `itunes:duration` until download; the download handler then overwrites it with the ffprobe-measured length | [task_handlers.py:300](../thestill/core/task_handlers.py#L300), [:316](../thestill/core/task_handlers.py#L316), [feed_manager.py:1043](../thestill/core/feed_manager.py#L1043) |
| LLM usage | One `llm_usage` INFO log event per billed call: provider, model, call, input/output tokens (Gemini adds thinking and cached tokens). Not stored, not priced | [llm_provider.py:863](../thestill/core/llm_provider.py#L863) |
| Usage coverage gaps | Logged in OpenAI/Anthropic/Mistral `_create_completion`, Ollama chat and all Gemini calls. **Not** logged for OpenAI, Anthropic and Mistral `generate_structured`, or non-Gemini streaming. Anthropic cache tokens not logged | [llm_provider.py:1141](../thestill/core/llm_provider.py#L1141), [:1221](../thestill/core/llm_provider.py#L1221), [:1843](../thestill/core/llm_provider.py#L1843), [:1987](../thestill/core/llm_provider.py#L1987), [:2396](../thestill/core/llm_provider.py#L2396), [:3157](../thestill/core/llm_provider.py#L3157) |
| Attribution | Task worker binds `stage` and `episode_id` as contextvars; narration binds `stage="narration"` and `briefing_id`. Request-time translations carry only `request_id` | [task_worker.py:739](../thestill/core/task_worker.py#L739), [narration_runner.py:160](../thestill/services/narration/narration_runner.py#L160) |
| Cost queries | CloudWatch Insights queries with one hard-coded price (gemini-3-flash-preview) | [logging-cloudwatch-queries.md](../docs/logging-cloudwatch-queries.md#llm-cost) |
| Spec #75 | Draft. No `_emit_trace`, no `data/llm_traces/`; nothing of it is built | [75-llm-call-tracing.md](75-llm-call-tracing.md) |
| Per-stage models | Only cleaning (`CLEANING_PROVIDER/MODEL`) and entity linking (`ENTITY_LINKING_PROVIDER/MODEL`, falls back to cleaning) have their own model. Summarise and the rest use `LLM_PROVIDER` | [config.py:460](../thestill/utils/config.py#L460), [:648](../thestill/utils/config.py#L648), [entity_linking/factory.py:43](../thestill/core/entity_linking/factory.py#L43) |
| Entity branch | Always chained after summarise; no switch | [queue_manager.py:271](../thestill/core/queue_manager.py#L271) |
| Processing pause | None. `read_only` stops user-started spend but followed feeds and scheduled briefings carry on | #104 open question 4 |
| Dispatch gate | The worker already skips claiming when degraded or when the spec #49 breaker is open, per stage | [task_worker.py:593](../thestill/core/task_worker.py#L593), [:599](../thestill/core/task_worker.py#L599) |
| Briefings | Scheduler tick claims due slots and chains narration (LLM only, no TTS) | [briefing_scheduler.py:146](../thestill/core/briefing_scheduler.py#L146), [:343](../thestill/core/briefing_scheduler.py#L343) |
| Who brought a feed in | `feed_introduced` usage row, `subject_id` = podcast id | [follow_policy.py:93](../thestill/services/abuse/follow_policy.py#L93) |
| Digest | Signups, top introducers and followers, quota hits, alerts, switches. No spend | [alerts.py:350](../thestill/services/abuse/alerts.py#L350) |
| Admin analytics / status | `/api/analytics` (spec #95) and `/api/status`; neither shows spend | [api_dashboard.py:217](../thestill/web/routes/api_dashboard.py#L217), [api_status.py:41](../thestill/web/routes/api_status.py#L41) |
| Hosted transcription | `TRANSCRIPTION_PROVIDER: dalston` | [docker-compose.prod.yml:43](../deploy/aws-ec2/docker-compose.prod.yml#L43) |

## Goals

1. In hosted mode, runaway spend stops on its own within an hour, without anyone reading email.
2. The operator can answer "what did we spend in the last hour, on what, and because of whom?" from the admin UI or CLI, in dollars.
3. A budget is set in dollars per day, with a warning level and a stop level.
4. A stop holds paid work, never reads. Readers keep their library, transcripts and summaries.
5. Provider-side limits cap the worst case even if the app is broken or a key leaks.
6. Single-user mode is unchanged unless the operator opts in.

## Non-goals

- **Invoicing or per-user billing.** Estimates are for guardrails, not charging anyone.
- **Exact reconciliation.** Estimates aim for ±15% of the provider invoice, not cents.
- **Per-feed and per-user caps.** Episode and minute budgets per introduced feed are [118-per-feed-processing-budget.md](118-per-feed-processing-budget.md). Maximum episode length is [119-audio-duration-cap.md](119-audio-duration-cap.md). This spec only *attributes* dollars to feeds and users so those specs and [123-abuse-triage-sweep.md](123-abuse-triage-sweep.md) can read them.
- **Full call tracing.** Prompts, responses and replay stay in #75. This spec takes only its seam.
- **Embedding, Wikidata and search costs.** Local or free today.

## Design

### Phase 1: Spend breaker on by default

**1.1 Hosted defaults.** When `MULTI_USER=true` and the env var is unset, `alert_spend_breaker_minutes` defaults to **3000** (50 audio hours in 24h) and `alert_spend_breaker_action` to `pause_new_feeds`. Single-user keeps `0` / `none`. An explicit env value, including `0`, always wins, so an operator can still turn it off. `thestill status` and `/admin/settings` show the effective value and whether it came from the hosted default. 3000 is a starting point; see open question 1 for sizing it from real data once Phase 2 has a week of readings.

**1.2 Better estimate.** The reading already uses measured durations for downloaded episodes, because the download handler overwrites `episodes.duration` with the ffprobe length. Two gaps remain:

- **Queued, not yet downloaded.** These use the feed's claim, which an abuser controls. When spec #119 probes the real duration before download, the reading uses the probed value. Until then, the feed value stands.
- **Missing duration counts as 0.** `_seconds` returns 0 for missing or junk values, so a feed that omits `itunes:duration` is invisible. Change: count a missing or unparseable duration as `SPEND_UNKNOWN_DURATION_MINUTES` (default 60). Over-counting is the safe direction for a breaker.

Precedence per episode: probed (spec #119) > measured (download) > feed > the unknown default. The alert text reports how many episodes used each source, so a breaker tripped by unknowns is easy to spot.

**1.3 Third action.** `SPEND_BREAKER_ACTIONS` gains `pause_processing`, which sets `processing_paused` (Phase 3) as well as `new_feeds_enabled = false`. The hosted default stays `pause_new_feeds`, because the minutes breaker counts work already queued for feeds people follow.

### Phase 2: Spend meter

**2.1 One seam for LLM calls.** `LLMProvider._log_usage` is already called at the point where the raw SDK response, and its usage block, is in hand. That is the same seam #75 chose for `_emit_trace`. This spec adds only the minimal slice:

- `_log_usage` also hands a `UsageRecord`-like value (provider, model, call, input, cached, output and thinking tokens) to a process-wide `SpendRecorder`, if one is installed. When #75 lands, `_emit_trace` and the recorder read the same values; neither changes the other.
- Close the coverage gaps: call `_log_usage` in OpenAI, Anthropic and Mistral `generate_structured`, in each `chat_completion_streaming` once the final usage arrives (or log `usage_unknown=true` where the SDK gives none), and add Anthropic `cache_read_input_tokens` / `cache_creation_input_tokens`.
- The recorder never raises into the call. A failed write logs `spend_record_failed` and the LLM result is returned as normal.
- Context comes from structlog contextvars already bound: `stage`, `episode_id`, `briefing_id`, `request_id`. New: the translation route and the import path bind `user_id` (the session user), so request-time spend is attributable.

**2.2 Transcription.** `handle_transcribe` records one event on success: provider (`dalston`, `elevenlabs`, `google`, `whisper`, `parakeet`), model, and audio seconds from the measured episode duration. Only successes count. A failed attempt that a cloud provider still billed is a known undercount; the quarterly invoice check in the runbook catches it if it matters.

**2.3 Price table.** A shipped default table in `thestill/services/spend/prices.py`, overridable by `SPEND_PRICES_FILE` (a JSON file in the same shape) so a price change needs no release:

```json
{
  "version": "2026-11-17",
  "llm": {
    "gemini/gemini-3-flash-preview": {"input": 0.50, "cached_input": 0.05, "output": 3.00},
    "anthropic/claude-sonnet-4-5-20250929": {"input": 3.00, "cached_input": 0.30, "output": 15.00},
    "ollama/*": {"input": 0, "output": 0}
  },
  "transcription_per_minute": {
    "dalston": 0.0,
    "elevenlabs/scribe_v1": 0.0067,
    "google/*": 0.016,
    "whisper": 0, "parakeet": 0
  },
  "fallback_llm": {"input": 5.00, "cached_input": 0.50, "output": 25.00}
}
```

LLM prices are USD per 1M tokens; thinking tokens bill at the output rate. The values above illustrate the shape; the shipped table must be checked against each provider's price page on the day it is written, and carries that date as `version`. An unknown model uses `fallback_llm`, deliberately pricier than any known entry, and is counted as `unpriced` so the digest names it. Dalston runs on the operator's own AWS GPU (spec #66), so its marginal cost is set by the operator: `0` if the GPU is a fixed cost, or the amortised instance cost per transcribed minute (open question 3).

**2.4 Storage.** A new `spend_events` table (both backends, own repository, like `usage_events`):

| Column | Notes |
|---|---|
| `id`, `occurred_at` | |
| `kind` | `llm` or `transcription` |
| `provider`, `model`, `call`, `stage` | |
| `episode_id`, `podcast_id`, `briefing_id`, `user_id` | Nullable. `podcast_id` resolved from the episode at write time |
| `input_tokens`, `cached_tokens`, `output_tokens`, `thinking_tokens`, `audio_seconds` | |
| `usd_micros` | Estimate at write time, integer millionths of a dollar |
| `price_version`, `unpriced` | Which table priced it |

Rows older than 90 days are pruned by the existing housekeeping loop, beside `prune_usage_events`. Volume is modest: segmented cleaning makes the most calls, on the order of tens to a few hundred rows per episode.

**2.5 Attribution.** Read time, not write time, so it can improve without rewriting rows:

- **Episode → feed:** `podcast_id` on the row.
- **Feed → user:** the earliest `feed_introduced` row for that podcast. Feeds introduced before #104 Phase 2 have no introducer and show as "unattributed".
- **Imports and "send to my inbox":** the user on the `imports_per_day` ledger row for the episode.
- **Translations:** the session user. **Narration:** the briefing's user.
- An episode followed by many users is still charged to its introducer only. That is the right signal for abuse; it is not a fair-share split.

**2.6 Aggregation.** `SpendService.summary(range, by=...)` groups by hour or day and by `stage`, `provider/model`, `podcast_id` or `user_id`, with a SQL `GROUP BY` over `spend_events`. Queries run on demand with a 60-second cache, like `/api/analytics`. No rollup table until a query is shown to be slow.

### Phase 3: Daily dollar budget

**3.1 Levels.** Three readings, each with an env default and an override in the `alerts` runtime setting:

| Setting | Env | Hosted default | Reading |
|---|---|---|---|
| `spend_hourly_usd` | `ALERT_SPEND_HOURLY_USD` | 5 | Estimated spend in the last 60 minutes |
| `spend_soft_usd` | `SPEND_DAILY_SOFT_USD` | 20 | Rolling 24h estimated spend |
| `spend_hard_usd` | `SPEND_DAILY_HARD_USD` | 50 | Rolling 24h estimated spend |

All default to 0 (off) in single-user mode. Rolling 24h, not calendar day, matches the breaker and doesn't reset an attack at midnight. The defaults are placeholders sized for today's traffic; Rollout step 2 replaces them with measured values.

**3.2 Alerts.** Three new rules in `AlertService.evaluate`, de-duplicated hourly like the others:

- `spend_rate`: last hour over `spend_hourly_usd`. This is the "watch it live" alert: a burst shows within one 5-minute tick, long before the daily total.
- `spend_budget_soft`: alert only, naming the top three stages, feeds and introducing users by dollars in the window.
- `spend_budget_hard`: alert, then set `processing_paused = true` and `new_feeds_enabled = false` as actor `system(spend_budget)`. The audit log shows both.

A pause never lifts on its own. The operator resumes with `thestill settings set processing_paused false` once they have looked; the alert says so. If the reading is still over the hard level, the next tick pauses again, so resuming without raising the budget or fixing the cause is a short-lived act. `thestill settings set alerts '{"spend_hard_usd": 80}'` raises it for the day.

**3.3 The `processing_paused` switch.** A new runtime setting (default false) in [settings_registry.py](../thestill/services/abuse/settings_registry.py), answering #104 open question 4:

- **Holds:** the task worker stops claiming tasks for paid stages (`transcribe`, `clean`, `summarize`, `extract-entities`, `resolve-entities`). The check sits in the dispatch loop next to the spec #49 breaker gate, so held tasks stay `pending` and never fail or burn retries. `download` and `downsample` keep running: they cost bandwidth and CPU, not provider money, and leave the backlog ready to resume.
- **Holds:** the briefing scheduler's `tick` claims no slots. Due schedules stay due, and the existing catch-up logic covers the missed window once resumed. Narration and translations refuse with the same `feature_paused` error #104 uses.
- **Holds:** imports and new feeds (it implies `imports_enabled` and `new_feeds_enabled` off, the way `read_only` does).
- **Does not hold:** reads, cached translations, search, playback, the MCP read tools, the admin console, and operator CLI runs (`thestill transcribe` and friends print a warning but run, matching #104's rule that operator paths are not gated).

`read_only` does not imply `processing_paused`; they answer different incidents. The #104 "not at its default" banner and the digest pick the switch up with no extra work.

**3.4 Degrade before stopping.** Between soft and hard, an optional `SPEND_DEGRADE_AT_USD` level applies cheaper behaviour instead of stopping. Each step is its own runtime switch so it can also be set by hand:

- **`entities_enabled = false`.** Summarise stops chaining into `extract-entities`. The entity branch is a sizable share of LLM calls per episode, and summaries and transcripts are unaffected. Skipped episodes are listed, and `thestill reprocess --from entities` (spec #106) backfills them later. This needs a new switch: no setting skips the branch today.
- **Cheaper model ("penalty model").** Only cleaning and entity linking have their own model setting today; summarise uses `LLM_PROVIDER`. So the degrade model applies to those two stages only: `SPEND_DEGRADE_CLEANING_MODEL` (same provider), read when a task starts rather than at process start. Routing summarise to a cheaper model needs a per-stage summarise model first, which is out of scope here. A per-user penalty model (only a flagged user's feeds get the cheap model) belongs with [123-abuse-triage-sweep.md](123-abuse-triage-sweep.md).

Degrade is off by default. Quality drops silently with it, so the alert says which steps are on and the episodes processed while degraded carry the model in their existing cleaning provenance.

### Phase 4: Visibility

- **API.** `GET /api/admin/spend?range=24h|7d|28d&by=stage|model|feed|user` returns hourly (24h) or daily buckets, the budget levels, the current switch states and the `unpriced` count. Admin-only; multi-user and single-user both, since an operator running locally with a cloud LLM wants it too.
- **Admin analytics page.** A "Spend" section on [Analytics.tsx](../thestill/web/frontend/src/pages/Analytics.tsx): an hourly bar chart for the last 24h with the hourly threshold line, the rolling-24h total against soft and hard levels, and top-5 tables by stage, model, feed and introducing user (each feed and user linking to its admin page).
- **Status.** `/api/status` and `thestill status` gain one line: last hour, rolling 24h, budget, and whether processing is paused or degraded.
- **CLI.** `thestill spend [--range 24h|7d|28d] [--by stage|model|feed|user] [--json]`, reading the same service.
- **Digest.** A spend block in `build_digest`: 24h total against budget, the peak hour, top 3 feeds and introducing users by dollars, unpriced calls, and any degrade or pause in effect. A day over the soft level is never "quiet", so the digest is sent even when `ADMIN_DIGEST_ALWAYS` is false.

## Operator runbook

These are **ops steps in provider consoles, not code**. They are the backstop for a bug in this spec's code, a stolen key, or spend outside the pipeline. Console names and options change; check each one when setting it up and record the date in the ops log.

1. **One key per environment.** Separate API keys (or projects/workspaces) for production, staging and local development, so a leaked dev key can't drain production's limit and a limit set on production applies only there.
2. **OpenAI.** In the organisation's project settings, set a monthly budget for the production project and email notification thresholds (for example 50% and 80%). Check whether the account offers a hard stop at the budget; if it only notifies, rely on the app's hard level and the alerts.
3. **Anthropic.** In the Console, put production in its own workspace and set that workspace's monthly spend limit, plus a notification threshold below it.
4. **Google (Gemini, Speech-to-Text).** In Cloud Billing, create a budget on the project with alerts at 50%, 90% and 100% of the monthly figure. A budget alerts but does not cap. For a real cap, wire the budget's Pub/Sub notification to a function that disables billing or the API key on the project, and accept that this takes the service down. Set per-model quota limits in the API console where offered.
5. **ElevenLabs.** Keep usage-based billing (overage) off, so the plan's monthly quota is the cap. Raise the plan rather than enabling overage.
6. **Mistral.** Set the workspace's spending limit in the console, if offered on the account's plan.
7. **AWS Budgets.** A monthly cost budget on the account with alerts at 50%, 80% and 100% (actual) and 100% (forecast), sent to the same address as `ADMIN_ALERT_EMAIL`. Add a budget filtered to the Dalston GPU instances, and consider a budget action that stops them at 100%.
8. **Quarterly check.** Compare the meter's 28-day total per provider with the provider invoice. More than ±15% apart means the price table or the coverage is wrong; fix the table first.
9. **Incident.** If a provider alert fires before the app's: `thestill settings set processing_paused true`, then `thestill spend --range 24h --by feed` and `--by user` to find the source, then the #104 user tools.

## Rollout

1. **Phase 1** ships alone. Hosted gets the breaker at 3000 minutes with `pause_new_feeds`. Watch the digest for a week; a trip on a normal day means the threshold is low.
2. **Phase 2** ships with all budget levels at 0: record only. After 7 days, compare the meter with provider dashboards, correct prices, and set the soft and hard levels from the measured daily p95 (soft at about 2×, hard at about 4×).
3. **Phase 3** ships `processing_paused` and the alerts. Soft level first; the hard level a week later, once soft has not fired on a normal day. Degrade stays off until an incident shows it is wanted.
4. **Phase 4** ships with or after Phase 2; the analytics section is useful from the first day of recording.
5. **Runbook** steps are done at Phase 1 and recorded in the ops log; they don't wait for code.
6. `docs/configuration.md` gains every new variable, `docs/logging-cloudwatch-queries.md` points at `thestill spend` and keeps its queries for log-only installs, and the README spec index gets a row.

## Testing

- **Breaker defaults:** with `MULTI_USER=true` and no env, the effective threshold is 3000 and action `pause_new_feeds`; an explicit `0` keeps it off; single-user stays off.
- **Estimate:** a queued episode with no duration counts the unknown default; a downloaded one counts its measured length; a probed one (once #119 exists) wins over the feed.
- **Usage coverage:** each provider's chat, structured and streaming paths emit one `llm_usage` event and one recorder call, using recorded SDK responses; a recorder that raises never fails the LLM call.
- **Pricing:** known model, wildcard, unknown model (fallback and `unpriced`), thinking tokens at the output rate, cached tokens at the cached rate, `SPEND_PRICES_FILE` override, invalid file refused at startup.
- **Attribution:** episode → feed → earliest introducer; imported episode → importer; translation → session user; narration → briefing user; pre-ledger feed → unattributed.
- **Budget rules:** hourly, soft and hard trip at `>=`; hard sets both switches as `system(spend_budget)` and is audited; a resumed pause re-trips while over budget; each alert sends once an hour across workers.
- **`processing_paused`:** paid stages are not claimed and stay `pending` with retry counts unchanged; download and downsample still run; the briefing tick claims nothing and catches up after resume; narration and translations return `feature_paused`; reads, search and MCP read tools work; operator CLI runs proceed with a warning.
- **Degrade:** with `entities_enabled=false`, summarise does not chain to `extract-entities` and the episode is listed for backfill; the degrade cleaning model is used by tasks that start after the switch.
- **Visibility:** `/api/admin/spend` buckets and totals match seeded rows on SQLite and Postgres; non-admins get 403; the digest includes the spend block and is sent on a day over the soft level.

## Open questions

1. **Thresholds.** 3000 minutes and $5 / $20 / $50 are guesses. Should Phase 1 wait for a week of Phase 2 readings instead of shipping a guessed default? Proposal: ship the guess, since off is worse than a loose limit.
2. **Rolling 24h or calendar day?** Rolling matches the breaker and resists a midnight reset, but providers bill by calendar month. Proposal: rolling for the guardrail, calendar month shown beside it for comparison with invoices.
3. **Dalston's cost.** If the GPU instance is always on, marginal transcription cost is zero and the real limit is capacity (`queue_backlog`). If it scales with load, price it per minute. Which model does #66 run today?
4. **Should `download` be held?** It costs S3 storage and egress, which are small now. Holding it would also stop disk growth during an incident.
5. **Pause scope by feed.** A hard pause stops every feed, including the operator's own. Should it hold only feeds introduced in the last N days (the likely abuse) first, and everything only at a higher level? That overlaps with [118-per-feed-processing-budget.md](118-per-feed-processing-budget.md).
6. **Per-user dollar quotas.** Once spend is attributed, a daily dollar quota per introducing user is a small step. Is it wanted, or do #118's minute budgets cover it?
7. **#75 sequencing.** If #75 is built first, the recorder should hang off its observer rather than `_log_usage`. Either order works as long as both read the same values at the same seam.

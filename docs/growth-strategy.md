# Growth Strategy: Activation, Engagement, Retention

> **Status:** 📝 Draft (2026-11-04)
> **Owner:** Product
> **Scope:** The three Scorecard metrics from [spec #111](../specs/111-product-health-metrics.md), in priority order: activation, then engagement, then week-4 retention. Acquisition is out of scope except where it changes who arrives.
> **Reads:** [analytics.md](analytics.md) (the metrics), [experiments.md](experiments.md) (the protocol every change below must follow)

---

## 1. The one-paragraph strategy

Thestill's retention loop is **follow → delivery → return**. A reader
who follows a show gets new episodes summarised in their inbox and a
briefing every morning; a reader who follows nothing gets a one-off
read and leaves. The first new readers (2026-10-29) did exactly that:
three sign-ins from public episode pages, 5–8 reads each, zero follows,
activation 0%. So the strategy is, in order:

1. **Activation:** make the first follow the obvious next step after
   sign-in, and make day-2 value certain once it happens.
2. **Engagement:** give the product a channel that reaches readers
   outside the app (briefing email, later push), because today nothing
   pulls a member back; the morning briefing exists but waits to be
   visited.
3. **Retention:** widen each reader's footprint (more shows, a second
   surface such as the Claude connector) and keep trust in the summaries,
   so the habit survives a quiet week.

Everything ships under the spec #111 §6 protocol: one change per metric
at a time, a row in `experiments.md` before it ships, read by signup-week
cohort, and only bets that could plausibly double the metric. At 2–3
signups a day, smaller effects are invisible.

## 2. Where the loop breaks today

| Step | What happens now | Why it loses readers |
|---|---|---|
| Land | Public episode page, "Read the full summary" → `/login` | The CTA sells one read, not a subscription. The reader's intent is "finish this summary", so a follow is an interruption afterwards |
| Sign in | Lands back on the episode; `FollowPrompt` offers the show once a reader has zero follows ("Not now" hides it for good) | Good start, but one prompt, one show, and a dismissal is permanent on that browser. Nothing on `/inbox` beyond an empty state ("Follow a podcast to receive new episodes") |
| Follow | Seeds 2 back-catalogue episodes (`INBOX_SEED_ON_FOLLOW`), the show starts polling (spec #63) | Both seeds arrive on the signup day, which the activation definition deliberately doesn't count. A later-day value day needs the show to publish within 7 days, or the briefing |
| Briefing | A daily 08:00 local schedule is created on the first `/latest` call that carries a timezone; email is off by default (`email_enabled=False`), `EMAIL_PROVIDER` defaults to `none`; no push (spec #93) | The briefing is the only reliable day-2 value, and the only way to see it is to remember to come back. Zero outbound channels on by default |
| Return | Inbox + briefing card + Continue listening | Fine once they're here. The problem is getting them here |
| Connector | Any reader can connect Claude; connector calls count as value days | Discoverable only from a Settings card (spec #108). Baseline: 0 non-admin calls a week |

Two structural facts shape what can work:

- **Publish cadence sets the ceiling on activation.** A reader whose only
  follow is a weekly show can go 7 days with no new delivery. Shows that
  publish daily (news, markets) create a value day the next morning.
  Which shows we steer a new reader to is an activation lever, not just
  a taste question.
- **Volume is tiny.** Cohorts are read week by week with counts beside
  the percentages, and every new reader's PostHog timeline is read by
  hand. Qualitative reading is part of the method, not a fallback.

## 3. Targets

Baselines come from `/analytics` on the day this doc is adopted; the
numbers below are the bar for "worked", not forecasts. The first graded
cohort shows activation at 0%.

| Metric | Population | Baseline | Target by week 12 | Guardrails |
|---|---|---|---|---|
| Activation (follow + later-day value within 7 d) | New readers | 0% (n=3) | ≥40% | Activated count; sign-in completion; follows undone within 7 d |
| Engagement (≥2 value days / week) | Members (≥7 d old, active in prior 28 d) | read on adoption | ≥35% | Briefings switched off in 7 d; email unsubscribes |
| Week-4 retention (value day in days 28–35) | New readers | read on adoption | ≥25% | Activated count (no retention via a shrunken, self-selected cohort) |
| Weekly return | Members | read on adoption | ≥60% | same as engagement |

Spec #111 §7 is a gate inside this plan: once 50 readers are graded for
both activation and week-4 retention, check that activated readers retain
at a clearly higher rate (for example 40% vs 10%). If they don't, the
activation definition changes before any more activation work ships.

## 4. Activation plan (weeks 1–12)

Order matters: each item is one experiment row, shipped alone, read
three weeks later. Items 1–3 are the ones most likely to double the
number; 4–5 remove friction once the number is non-zero.

### A1. Follow the show after signing in from its episode (in flight)

Already logged as experiment 1 and built as `FollowPrompt`. Ship it, add
the PostHog annotation, read it. Expected 0% → ≥30%. Two refinements to
fold into the read, not into the change: "Not now" should expire (for
example 7 days, not forever), and the prompt should also appear on the
empty inbox, since a reader who dismissed it on the episode page is sent
to `/inbox` with nothing in it.

### A2. Sell the subscription on the public page, not the read

Change the public episode page's primary call to action from "Read the
full summary" to "Get new episodes of `<show>` in your inbox", carrying
`intent=follow` into `/login` so the follow completes on return
(`pending_follow` already exists for the show page). The full-summary
link stays as the secondary action. This moves the follow decision to
the moment of highest intent and in front of the sign-in, so sign-in
completion is the guardrail to watch: if fewer visitors sign in but
more of those who do activate, the activated **count** decides.

### A3. Make day-2 value certain: the first briefing reaches the reader

A follow makes tomorrow's 08:00 briefing exist (it covers the two seeded
episodes). Nothing makes the reader see it. Turn on briefing email for
new readers by default, asked at follow time as a pre-checked line under
the confirmation ("Email me the morning briefing"), with one-click
unsubscribe in every message. Prerequisites: `EMAIL_PROVIDER` set in
production and spec #51 Phase 4 (bounce handling) done. Expected effect:
activation among followers from "whatever the show's cadence gives" to
≥70%, because a briefing open on day 2 is a value day regardless of
cadence. This is also the first engagement lever, so it is the one change
that serves two metrics; it still ships as one row, graded on activation.

### A4. Follow three shows, one of them daily

After the first follow, offer two more: the region's Top chart filtered
to shows that publish at least three times a week, plus the Related
rail for the show just followed. Three follows give three chances at a
within-week delivery, and a daily show nearly guarantees one. Measure
follows per activated reader as a secondary, and watch follows undone.

### A5. Time to first summary

A follow of a show nobody else follows starts transcription from zero;
the seeded episodes can sit unprocessed for an hour or more and the
inbox shows nothing. Merge spec #88's "Arriving soon" strip so the wait
is visible, and prefer already-processed shows in A4's suggestions so
most new readers' first follow lands summarised episodes at once.

## 5. Engagement plan (≥2 value days a week)

Engagement work targets members, a different population from activation,
so one engagement change can run alongside one activation change.

### E1. The briefing as the daily hook

The briefing is the only product surface that produces value on a day
nothing was published, so it is the engagement metric's natural engine.
Three parts:

- **Delivery.** Email by default (A3), then Web Push for readers who
  install the app (spec #93 §4.2, iOS needs Home Screen install), with
  the notification carrying the briefing's headline only.
- **Never a blank morning.** When no inbox episode arrived since the
  last briefing, still send a short one: a back-catalogue pick from a
  followed show, or the most-read episode across the catalogue that
  week. A skipped day breaks the habit; a thin day doesn't.
- **Quality.** Flip the scheduled-only flag (spec #84) in production so
  briefings are predictable, keep the readiness gate (spec #55) so they
  don't race the pipeline, and read the conversational narration A/B
  rubric (spec #77) for whether readers finish them.

Measure: briefing opens per member-week, email open → briefing-open
rate (counted on our server from a `from=email` path marker, never a
tracking pixel), and briefings switched off as the guardrail.

### E2. The connector as a second surface

A reader who asks Claude about their shows three times a week is engaged
without opening the app, and connector calls already count as value
days. Ship spec #108: the `/claude` page, the measured funnel, and an
in-app nudge after a reader's third read ("Ask Claude about the shows
you follow"). Success number from the spec: non-admin readers with ≥1
connector call a week, baseline 0.

### E3. Less work per visit

The second value day in a week is cheaper when a visit is short and
resumable: Continue listening (spec #90), chapters (spec #97), the
All / Unread / Saved inbox views (spec #88) and inbox search (spec #85)
are the pieces; the gap is a "15 minutes of listening" affordance on the
briefing itself (spec #34's audio briefing) for readers who commute.
This is a Later item unless E1 shows briefing opens are high but reads
are not.

### E4. Cadence coverage for members

Run a one-off query: what share of members follow no show that
publishes ≥3 times a week? For those readers, the Top chart and Related
rails should surface a daily anchor show. Same lever as A4, aimed at the
existing base.

## 6. Retention plan (week 4 and beyond)

Retention is mostly the outcome of the first two sections, so this
section is about the things that only show up after a month.

### R1. Widen the footprint before week 4

The working hypothesis, to be tested at the §7 gate: readers who by day
28 follow ≥3 shows and have used two surfaces (inbox plus briefing or
connector) retain; readers with one follow and one surface don't. If the
data agrees, the week-2 touchpoint becomes "add a show / connect Claude"
rather than another episode.

### R2. Downshift instead of cancel

Every exit (unfollow, briefing off, email unsubscribe) offers the softer
step: weekly instead of daily, a different hour, fewer shows. The
guardrails in §3 count hard exits, so a downshift that keeps the reader
is a win on the metric as well as for the reader.

### R3. Win-back for lapsed readers

A reader with no value day for 10 days gets one email: the single
best-read episode from the shows they follow since they left, and
nothing else. Two in a row with no open and it stops. Read this on
weekly return, with unsubscribes as the guardrail. Do not build this
before E1 exists; a win-back without a daily channel behind it has
nothing to return to.

### R4. Trust in the summaries

A summary that gets a guest's claim wrong costs a reader for good, and
the only signal today is the transcript/summary feedback proposal (spec
#59) and the quality loop (spec #86), neither scheduled. Schedule the
lightest piece: a one-tap "this is wrong" on summary blocks, routed to
the eval runs (spec #53). Read it qualitatively; its job is to catch
systematic errors (speaker priors, hallucinated figures) before they
reach the briefing.

## 7. Measurement and cadence

- **Weekly, Monday:** read the Scorecard with counts, the guardrails,
  and every new reader's signed-in path in PostHog. Update the
  experiment rows that reached their "Read on" date. Fifteen minutes.
- **Before every ship:** the experiments.md row, with the expected
  effect written down.
- **Instrumentation to add, in this order:** (1) briefing open source
  (`in_app` / `email` / `push`) stored on the briefing, since E1 cannot
  be read without it; (2) `mcp_connector_created` and the `/claude`
  funnel from spec #108; (3) follows-per-reader at day 7 and day 28 on
  `/analytics`, for R1.
- **Gates:** §7 proxy validation at 50 graded readers; A/B testing by
  user-id hash at ~150 signups a week (a later spec).
- **What a null result means:** at this volume, "not visible", not "no
  effect". Keep the change if the guardrails are clean and the
  qualitative read is positive; revert only on a guardrail breach.

## 8. Sequence

| Window | Activation (one at a time) | Engagement (one at a time) | Retention / platform |
|---|---|---|---|
| Now (weeks 1–4) | A1 ship + read | Prod `EMAIL_PROVIDER`, spec #51 Phase 4, briefing-open source instrumentation | Flip spec #84 flag; merge spec #88 |
| Next (weeks 5–8) | A3 email-by-default at follow | E1 "never a blank morning" | Spec #108 `/claude` page and funnel |
| Then (weeks 9–12) | A2 public-page CTA, or A4 three follows (whichever A1's read argues for) | E2 connector nudge | §7 proxy validation if 50 readers graded; R2 downshift flows |
| Later | A5 processed-first suggestions | Web Push; audio briefing (spec #34) | R3 win-back; R4 one-tap wrong |

## 9. Risks and what would change the plan

- **Email isn't live in production.** A3 and E1 both depend on it. If it
  slips, A4 (three follows, one daily) becomes the second activation
  bet, because it raises the chance of a natural later-day delivery.
- **Readers opt out of analytics or send GPC.** They stay in every
  denominator with inbox reads and briefing opens only, so email- and
  connector-driven value days are undercounted for them. The page shows
  the share; if it grows past a few percent, read the day-based metrics
  without them (spec #111 D2).
- **Gaming.** A forced follow in onboarding would move follows and
  nothing else; the activation definition's later-day clause blocks it,
  and follows undone within 7 days is the tell. Never pre-follow.
- **Acquisition stays at 2–3 a day.** Then this plan is read on
  cohorts of 15–20 and only doublings are visible. That is acceptable
  for A1–A3, which are doubling-sized bets, and is why nothing smaller is
  on the list.
- **The proxy fails the §7 check.** If activated readers don't retain
  better, stop activation work, pick the component that separates
  retention best, and re-plan from §4 with the new definition.

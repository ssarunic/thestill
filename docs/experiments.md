# Product Experiments

A log of product changes aimed at a Scorecard metric
([spec #111](../specs/111-product-health-metrics.md) §6). Signups are too
few for A/B tests (about 290 per arm to tell 20% from 30%), so each change
is read by comparing signup-week cohorts before and after it shipped.

## Rules

- **Write the row before the change ships.** The metric, guardrails and
  expected effect are fixed in advance, so the result can't be read to
  fit.
- **One change per metric at a time.** Two activation changes in the same
  weeks can't be told apart.
- **Aim for big effects.** At current volume only changes that could
  plausibly double a metric are visible. A null result on a small change
  means "not visible", not "no effect".
- **Look at the readers too.** With a handful of new readers a week, read
  each one's signed-in path in PostHog alongside the cohort numbers.
- **On ship:** add a PostHog annotation with the version, and fill in
  "Shipped" and "Read on" (the first date two full signup weeks after
  shipping are graded, so at least 3 weeks out).
- **On read:** record before vs after with counts, check every guardrail,
  and write the decision (keep, revert, or iterate).

## Log

### 1. Follow the show after signing in from an episode

| Field | Value |
|---|---|
| Change | After signing in from a public episode page, offer to follow that show as the next step ("Get new episodes of `<show>` in your inbox") |
| Why | On 2026-10-29 three new readers signed in from episode pages, read 5–8 times each, and none followed a show, so the follow → delivery → return loop never started |
| Metric | Activation |
| Guardrails | Activated readers (count), sign-in completion, follows undone within 7 days |
| Expected effect | 0% → ≥30% of graded readers |
| Status | Read; replaced by experiment 2 |
| Shipped | v1.31.0, 2026-10-26 (the first-follow panel, `source=episode_prompt`) |
| Read on | 2026-11-17 |
| Result | Not visible. Activation 0 of 10 (signup week 2026-10-26) and 1 of 8 (2026-11-02), against 0 of 11 the week before. Followed a show within 24 h: 0 of 2 in the week to 2026-11-17. The panel only reached readers who followed nothing, and none of the graded readers used it. Decision: iterate (experiment 2) |

### 2. Follow beside the show name on every episode page

| Field | Value |
|---|---|
| Change | [Spec #117](../specs/117-follow-from-episode-page.md): a Follow button beside the show name in the episode header for every reader who doesn't follow that show, signed in (episode page, inbox reader, imports with a real show) and signed out (public episode page, finished after sign-in). A toast explains what Follow does and offers Undo. Replaces the experiment 1 panel |
| Why | Every signup in the week to 2026-11-14 came from an episode page, and the episode page offered Follow only to readers who followed nothing (experiment 1); a signed-out visitor could only follow from the show page |
| Metric | Activation |
| Guardrails | Activated readers (count), sign-in completion, follows undone within 7 days (Undo is in the toast, so watch this one) |
| Expected effect | 0–13% → ≥30% of graded readers; `follow` events with `source` `episode_page` or `episode_pending_follow` from new readers |
| Status | Built on `feat/117-follow-from-episode-page` |
| Shipped | — |
| Read on | — |
| Result | — |

### 3. Welcome and product-tips email

| Field | Value |
|---|---|
| Change | [Spec #118](../specs/118-lifecycle-email.md): a welcome email a day after signup (follow a show, or what happens next for readers who already follow one), a follow nudge on day 3 for readers who still follow nothing, a Claude connector tip on day 7 and a search tip on day 14. Each is skipped when it no longer applies; "Product tips by email" in Settings turns them off |
| Why | Activation stalls at the first follow, and a reader who leaves on day 0 never sees anything in the app again; email is the only way back |
| Metric | Activation |
| Guardrails | Tips unsubscribes (`/unsubscribe/tips` and the Settings switch) per email sent, bounces and complaints from the email provider, briefing-email unsubscribes in the same weeks |
| Expected effect | Readers who follow nothing on day 1 follow a show within 7 days after the welcome or the day-3 nudge; activation at least doubles against the experiment 2 baseline |
| Status | Built on `feat/lifecycle-email-prereqs`, switch off. Turn it on (`lifecycle_email_enabled`) only after experiment 2 is read, so the two activation changes don't overlap |
| Shipped | — |
| Read on | — |
| Result | — |

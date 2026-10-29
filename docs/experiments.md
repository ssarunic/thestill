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
| Status | Planned; built in its own PR |
| Shipped | — |
| Read on | — |
| Result | — |

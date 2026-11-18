# components/analytics

## Overview

Read-only sections of the admin `/analytics` page (spec #95 funnel, spec #103 content popularity, spec #111 Scorecard). Each section renders one slice of an `AnalyticsReport` / `AnalyticsScorecard` already fetched by the page; none of them fetch or mutate.

## Interface

- `ScorecardSection` — spec #111 four threshold-share metrics; latest graded week plus recent-week bars [src/components/analytics/ScorecardSection.tsx:ScorecardSection]
- `ActivationSection` — activation funnel, overall then per signup source [src/components/analytics/ActivationSection.tsx:ActivationSection]
- `SignupsSection` — per-day signup strip and split by signup source [src/components/analytics/SignupsSection.tsx:SignupsSection]
- `RetentionSection` — weekly cohort retention for the last 12 signup weeks [src/components/analytics/RetentionSection.tsx:RetentionSection]
- `EngagementSection` — window-wide activity averages (diagnostic only) [src/components/analytics/EngagementSection.tsx:EngagementSection]
- `ContentSection` — podcasts / episodes / categories read in the window vs the previous window [src/components/analytics/ContentSection.tsx:ContentSection]
- `PctCell` — percentage with its count beside it [src/components/analytics/PctCell.tsx:PctCell]
- `Bar` — decorative inline bar for a share in [0, 1] [src/components/analytics/Bar.tsx:Bar]

## Invariants

- A denominator under `SMALL_GROUP` is greyed out, never hidden; zero renders a dash so "not measurable yet" never reads as 0% [src/components/analytics/PctCell.tsx:PctCell]
- Content rows under `SMALL_GROUP` readers are greyed, never hidden; aggregates only, no row leads to the people behind it [src/components/analytics/ContentSection.tsx:ContentSection]
- Scorecard metrics are each a share of readers crossing a threshold, never an average [src/components/analytics/ScorecardSection.tsx:ScorecardSection]
- Retention cells stay blank until at least one member's week has fully passed [src/components/analytics/RetentionSection.tsx:RetentionSection]
- Activation steps count only users who signed up at least that long ago [src/components/analytics/ActivationSection.tsx:ActivationSection]

## Side Effects

- None (pure presentational components over props).

## Verification

- Run: `cd thestill/web/frontend && npm run test:run && npm run lint`

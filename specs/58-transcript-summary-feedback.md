# Transcript & Summary Feedback (lightweight error reporting)

> **Status:** 💡 Proposal
> **Created:** 2026-07-14
> **Updated:** 2026-07-14
> **Priority:** Medium
> **Author:** Product & Engineering
> **Related:** [#18 segment-preserving-transcript-cleaning](18-segment-preserving-transcript-cleaning.md) (the `source_segment_ids` durable anchor this spec keys off), [#54 summary-segment-citations](54-summary-segment-citations.md) (sidecar + staleness-guard precedent), [#53 eval-runs-and-summary-rubric](53-eval-runs-and-summary-rubric.md) (where verified feedback ultimately lands), [#28 corpus-search-and-entities](28-corpus-search-and-entities.md) (`POST /api/entities/corrections` — the correction-endpoint template), [#06 authentication](06-authentication.md) (user attribution)

---

## Executive Summary

Readers notice pipeline mistakes long before we do: a segment attributed to
the wrong speaker, a garbled product name, an ad chunk bleeding into content,
a summary claim that isn't in the episode. Today that signal evaporates — there
is no way to say "this is wrong" from the reader, so every model/prompt
improvement is flown blind on LLM-judge scores alone
([#53](53-eval-runs-and-summary-rubric.md)).

This spec adds the **lightest possible capture loop**: a flag affordance on
each transcript segment and each summary section that files a structured
feedback record — category, optional free-text note, a **durable anchor** to
the exact spot, and a **snapshot of what the user was looking at**. One table,
one write endpoint, one admin list. No editing, no threads, no votes, no
automatic reprocessing.

The design bet is *capture generously, structure minimally, analyze later*.
We deliberately do not build dashboards or auto-fix pipelines in v1 — we don't
yet know what the feedback distribution looks like. Once a few dozen reports
exist, patterns will emerge (e.g. "diarization swaps speakers on podcast X",
"finance jargon is consistently mis-heard", "section 4 hallucinates on short
episodes"), and *those* patterns decide what Phase 3+ automates: hint-term
lists, cleaning-prompt tweaks, or golden eval items per
[#53](53-eval-runs-and-summary-rubric.md) — mirroring how entity corrections
already emit a paste-ready golden-eval snippet
([api_entities.py:657](../thestill/web/routes/api_entities.py#L657)).

## Motivation

1. **The ground truth we lack is walking past us.** LLM-judge evals (#53)
   measure quality without human reference; human reports of *specific,
   located* errors are exactly the missing complement. A verified
   "wrong speaker here" is a regression test the judge can never invent.
2. **The anchor infrastructure already exists.** Spec #18 built
   `source_segment_ids` as the durable segment anchor and its docstring
   explicitly reserves it for "bookmarks, user edits, future eval ground
   truth" ([annotated_transcript.py:83](../thestill/models/annotated_transcript.py#L83)).
   Spec #54 already solved snapshot-staleness for summaries
   (`summary_sha256` guards). This spec is mostly wiring, not invention.
3. **Segments and sections are already discrete UI elements.**
   `SegmentedTranscriptViewer` renders one element per `AnnotatedSegment`;
   summaries have nine numbered `##` sections
   ([summary_checks.py:34](../thestill/evals/summary_checks.py#L34)).
   Attaching a per-element flag is low-friction.
4. **Fix attribution needs context frozen at report time.** Transcripts are
   re-cleaned and summaries regenerated; a report that only points at live
   artifacts becomes uninterpretable the moment the artifact changes. Snapshot
   at write time makes every report self-contained forever.

## Goals

1. A reader can flag a transcript segment or summary section in **one
   interaction** (pick a category chip, optionally type a note, submit).
2. Every report carries a **durable anchor** (`source_segment_ids` for
   transcript, section number + `summary_sha256` for summary) *and* a
   **context snapshot** (the text, speaker label, timestamps,
   `algorithm_version` as seen at report time), so reports survive re-cleans
   and regenerations.
3. Reports are attributable (`user_id` when authenticated; single-user mode
   works without auth) and triageable (`open → confirmed | invalid | fixed`).
4. An admin can list and filter reports; a CLI can export them for analysis.
5. Verified reports have a clear onward path into #53 eval golden sets —
   designed for, not built, in v1.

## Non-goals (v1)

- **No inline editing/correction application.** Feedback is read-only
  reporting; the editing UI is the separate follow-up #18 reserved
  `user_segment_id` for. Feedback data will *inform* that spec.
- **No automatic reprocessing** (re-clean/re-summarize on report). A report is
  a signal, not a command.
- **No threads, replies, votes, or reactions.** One report = one row.
- **No word-level anchoring.** Segment granularity + free text is enough to
  locate a wrong word; `WordSpan` anchoring can come later if reports show
  it's needed.
- **No public visibility of others' reports.** Reports are private input to
  the operator, not social annotations.

## Feedback taxonomy

A small, closed category enum — broad enough to cover the known failure
surfaces, small enough that chips fit in a popover. Free text carries nuance.

| Category | Target | Typical meaning | Likely fix surface |
|---|---|---|---|
| `wrong_speaker` | segment | Speaker label/name is wrong | Diarization, speaker-mapping prompt |
| `wrong_words` | segment | Mis-transcribed words, names, terms | ASR hint terms, cleaning prompt |
| `bad_boundary` | segment | Segment split/merged in the wrong place | Segmenter |
| `wrong_kind` | segment | Mislabeled `kind` (ad tagged as content, etc.) | Kind classifier prompt |
| `summary_inaccurate` | section | Claim not supported by the episode | Summary prompt (faithfulness) |
| `summary_missing` | section | Important content absent | Summary prompt (coverage) |
| `summary_malformed` | section | Broken structure, bad citation, wrong section | Prompt + deterministic checks (#53) |
| `other` | either | Anything else | — |

## Data model

One table, both backends (SQLite in-place `CREATE TABLE IF NOT EXISTS` in
`_create_schema()`/`_run_migrations()`, plus Alembic `0006_feedback.py`
mirroring `postgres_schema.SCHEMA_SQL` — same dual-track pattern as
`user_briefing_schedules`).

```sql
CREATE TABLE IF NOT EXISTS feedback (
    id            TEXT PRIMARY KEY,        -- uuid4
    user_id       TEXT,                    -- NULL in single-user mode
    episode_id    TEXT NOT NULL,
    target_type   TEXT NOT NULL,           -- 'transcript_segment' | 'summary_section'
    category      TEXT NOT NULL,           -- taxonomy above
    comment       TEXT,                    -- optional free text ("should be Satya, not Sam")
    anchor_json   TEXT NOT NULL,           -- durable anchor, shape per target_type
    context_json  TEXT NOT NULL,           -- snapshot at report time
    status        TEXT NOT NULL DEFAULT 'open',  -- open | confirmed | invalid | fixed
    created_at    TEXT NOT NULL,           -- ISO-8601 UTC
    resolved_at   TEXT
);
CREATE INDEX IF NOT EXISTS idx_feedback_episode ON feedback(episode_id);
CREATE INDEX IF NOT EXISTS idx_feedback_status  ON feedback(status);
```

**Anchor shapes** (validated Pydantic models serialized to `anchor_json`,
never trusted raw — FM-7 [#42](42-robustness-and-failure-mode-hardening.md)):

- `transcript_segment`: `{ source_segment_ids: [int], algorithm_version: str,
  segment_id_hint: int, start_s: float, end_s: float }` —
  `source_segment_ids` is the durable key into the write-once raw transcript;
  the positional `segment_id` is stored only as a re-resolvable hint (same
  split #54 settled on).
- `summary_section`: `{ section_number: int, section_heading: str,
  summary_sha256: str }` — sha binds the report to the exact summary text it
  was filed against (staleness guard per #54).

**Context snapshot** (`context_json`): the segment's text, speaker label, and
`kind` (or the section's markdown, capped at ~2 kB), as rendered when the user
flagged it. Denormalized on purpose — the report must stay interpretable after
any re-clean/regeneration, without needing the artifact history. If the
producing provider/model is cheaply known at write time, include it;
otherwise `created_at` + episode processing timestamps recover it later.

**Repository**: `feedback_repository.py` ABC +
`sqlite_feedback_repository.py` / `postgres_feedback_repository.py`, wired via
`repositories/factory.py` — copy the `briefing_delivery` repository shape.

## API

Follows the existing router → `AppState` → service → repository shape; new
`web/routes/api_feedback.py` registered in `app.py`.

- `POST /api/feedback` — auth-gated when multi-user (`require_auth`), open in
  single-user mode like other write routes. Body: `target_type`, `category`,
  `episode_id`, `anchor`, optional `comment`. The **server** builds
  `context_json` by re-reading the artifact and extracting the anchored
  text — the client never supplies the snapshot (keeps it honest and small).
  Rate-limit lightly (e.g. max N open reports per user per episode) to bound
  abuse.
- `GET /api/feedback?status=&category=&episode_id=&podcast_id=` — admin-only
  list, paginated via the standard envelope.
- `PATCH /api/feedback/{id}` — admin-only status transition
  (`confirmed`/`invalid`/`fixed`), sets `resolved_at`.

## Frontend

Two touchpoints, one shared popover component (`FeedbackPopover`):

1. **Transcript** — a small flag icon in each segment's hover affordance row
   in `SegmentedTranscriptViewer` (next to the existing seek/copy actions).
   Click → popover with category chips (segment categories), optional
   one-line note, Submit. The viewer already has the `AnnotatedSegment` in
   hand, so the anchor is assembled locally with zero extra fetches.
2. **Summary** — a flag icon on each `##` section heading in `SummaryViewer`
   (headings are already the section boundary; #54's citation chips prove the
   renderer can carry interactive elements).

Feedback UX rules: optimistic submit + toast ("Thanks — filed"), flagged
segments get a subtle marker for the reporting user only (session-local is
fine in v1), no counts or badges shown to anyone else.

## Closing the loop (the point of all this)

The capture loop is only worth building because of what verified reports feed:

1. **Triage** — admin skims the list, marks `confirmed`/`invalid`. Free-text
   comments make most reports one-glance decidable against the snapshot.
2. **Aggregate** — `thestill feedback list/export` (JSON/CSV). The first
   interesting queries are trivial GROUP BYs: category × podcast,
   category × `algorithm_version`, reports-per-episode outliers. This is
   where "patterns will start to emerge" — deliberately done offline/ad-hoc
   in v1 rather than pre-building dashboards for unknown distributions.
3. **Feed evals** — a `confirmed` report is a human-labeled failure case.
   Follow the entity-corrections precedent: emit a paste-ready golden-set
   snippet (episode + anchor + expected-vs-observed) consumable by the #53
   golden episode set, so every confirmed report becomes a permanent
   regression guard against the next prompt/model change.
4. **Fix upstream** — clusters map to fix surfaces per the taxonomy table:
   `wrong_words` clusters per podcast → ASR hint terms / cleaning-prompt
   vocabulary; `wrong_speaker` clusters → diarization or speaker-mapping
   prompt; `summary_inaccurate` → faithfulness prompt changes, verified by
   the new golden items before shipping.

## Phasing

| Phase | Scope | Gate |
|---|---|---|
| 1 — Capture (transcript) | `feedback` table + repos + `POST /api/feedback` + segment flag UI | A report filed from the UI survives a re-clean with an interpretable snapshot |
| 2 — Capture (summary) + triage | Section flag UI + admin `GET`/`PATCH` + minimal admin list page | Admin can confirm/invalidate from the browser |
| 3 — Use the data | `thestill feedback export`, golden-snippet emission for `confirmed` reports (#53 linkage) | One confirmed report round-trips into an eval run |
| 4+ — Pattern-driven (unscoped) | Whatever the data says: hint-term suggestions, prompt regression suites, word-level anchors, edit UI (#18 `user_segment_id`) | Decided after ~50–100 real reports |

## Failure modes ([#42](42-robustness-and-failure-mode-hardening.md))

- **FM-1 (isolation):** a failed snapshot extraction degrades to storing the
  anchor + client-declared category with `context_json = {"snapshot_error":
  ...}` — never reject the report because the artifact read hiccuped.
- **FM-7 (unsanitized input):** anchors are range-validated against the live
  artifact at write time (`source_segment_ids` must exist in the raw
  transcript; section number 1–9); `comment` is stored verbatim but always
  rendered as text, never markdown/HTML.
- **Silent-stale:** reports carry `algorithm_version`/`summary_sha256`; the
  admin list badges reports whose artifact has since changed instead of
  silently resolving anchors against the wrong generation.

## Open questions

1. **Single-user attribution** — in no-auth mode, is `user_id = NULL` fine, or
   synthesize a stable local identity? (Leaning: NULL is fine; there's one
   user.)
2. **"What should it be?" structured field** — for `wrong_speaker`, a picker
   of known speakers would make reports machine-actionable, but adds UI
   surface. Leaning: free text in v1, revisit when speaker feedback volume
   justifies it.
3. **Reporter visibility of resolution** — do users ever see "your report was
   fixed"? Nice retention touch, out of scope until multi-user is real.
4. **Briefings** — briefing scripts (#33/#36) are a third flaggable artifact.
   The `target_type` enum leaves room; deferred until transcript/summary
   feedback proves the loop.

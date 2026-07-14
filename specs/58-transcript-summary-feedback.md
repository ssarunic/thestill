# Transcript & Summary Feedback (flag-or-fix corrections)

> **Status:** 💡 Proposal (v2)
> **Created:** 2026-07-14
> **Updated:** 2026-07-14 (v2 — from flag-only to flag-or-fix; per-user diffs + corroboration)
> **Priority:** Medium
> **Author:** Product & Engineering
> **Related:** [#18 segment-preserving-transcript-cleaning](18-segment-preserving-transcript-cleaning.md) (the `source_segment_ids` durable anchor + the reserved `user_segment_id` edit hook), [#54 summary-segment-citations](54-summary-segment-citations.md) (sidecar + staleness-guard precedent), [#53 eval-runs-and-summary-rubric](53-eval-runs-and-summary-rubric.md) (where verified corrections land), [#28 corpus-search-and-entities](28-corpus-search-and-entities.md) (`POST /api/entities/corrections` — the correction-endpoint template), [#06 authentication](06-authentication.md) (user attribution)

---

## Executive Summary

Readers notice pipeline mistakes long before we do: a segment attributed to
the wrong speaker, a garbled product name, an ad chunk bleeding into content,
a summary claim that isn't in the episode. Today that signal evaporates.

This spec adds a **flag-or-fix correction loop**. Every report locates an
error with a **durable anchor** and a **snapshot of what the user saw**; where
the fix is cheap to express, the same interaction also captures a **structured
proposal** — the corrected value, not just "something's wrong here":

- tap a speaker label → pick the right speaker (a relabel diff),
- select text in a segment → type the replacement (a word-level text diff),
- segment boundary wrong → merge-with-prev/next or split-at-word,
- summary problems → category + free text (prose proposals are just text).

Proposals are **per-user overlay diffs**, never mutations of the canonical
artifact. Your own corrections render back to you immediately (your transcript,
fixed); canonical artifacts stay write-once so re-cleans and evals remain
deterministic. Because proposals are structured, **agreement is detectable**:
two independent users producing the same normalized diff on the same anchor
(`fingerprint` match) auto-corroborates the report — crowdsourced confidence
that something is real and worth acting on.

### v2 direction change (2026-07-14)

v1 of this proposal was flag-only. v2 shifts to flag-or-fix after weighing
ElevenLabs-style transcript editors (waveform + word-timestamp alignment +
draggable boundaries). The spectrum is:

| Level | Captures | Build cost | Who uses it |
|---|---|---|---|
| Flag | location + category | trivial | any reader |
| **Structured proposal (chosen)** | location + category + **expected value** | small — reuses existing segment UI | any reader, one extra tap |
| Full editor (waveform, boundary drag) | everything incl. timing | large — new rendering + audio surface | transcriptionists, not digest readers |

The middle level captures ~80% of an editor's data value (the diff) at ~5% of
its cost, and matches the actual persona — readers skimming a digest, not
audio professionals. The full editor is **explicitly deferred**, and if it
ever comes, likely as an **admin triage/golden-set curation tool** first
(where waveform-verified boundary fixes matter), not a reader feature. Timing
data is also the least trustworthy thing to crowdsource: dynamic ad insertion
means users may legitimately hear different audio offsets (#54's drift
caveat), while text and speaker proposals are offset-independent.

The design bet stays *capture generously, structure minimally, analyze later*:
no dashboards, no automatic reprocessing, no reputation system in v1. Once
reports accumulate, patterns pick what Phase 5+ automates — hint-term lists,
prompt tweaks, or golden eval items per [#53](53-eval-runs-and-summary-rubric.md),
mirroring how entity corrections already emit a paste-ready golden-eval
snippet ([api_entities.py:657](../thestill/web/routes/api_entities.py#L657)).

## Motivation

1. **A diff is strictly better data than a flag.** "Wrong speaker" needs an
   admin to re-listen; "SPEAKER_01 → Satya Nadella" is machine-actionable —
   it can relabel an overlay, seed a golden eval item, or feed speaker-mapping
   prompts directly.
2. **Independent agreement is a free verifier.** Structured proposals
   canonicalize, so "several users made the same fix" is computable — a
   strong, automatic signal of what's worth acting on, which pure free-text
   comments can never give.
3. **The anchor and edit hooks already exist.** Spec #18 built
   `source_segment_ids` as the durable anchor (docstring: "bookmarks, user
   edits, future eval ground truth" —
   [annotated_transcript.py:83](../thestill/models/annotated_transcript.py#L83))
   and reserved `user_segment_id` for exactly this editing follow-up.
   Spec #54 solved snapshot staleness (`summary_sha256`).
4. **The UI is already discrete and speaker-aware.**
   `SegmentedTranscriptViewer` renders one element per segment and already
   holds the episode's full speaker set client-side (speaker color map), so a
   speaker picker costs no extra fetches. Summaries have nine numbered `##`
   sections ([summary_checks.py:34](../thestill/evals/summary_checks.py#L34)).

## Goals

1. One interaction to flag; at most one more to fix — category chip, then an
   optional structured proposal (speaker picker / replacement text / merge–
   split choice) or free-text note.
2. Every report carries a **durable anchor** (`source_segment_ids` +
   `algorithm_version` for transcript; section number + `summary_sha256` for
   summary) and a **server-built context snapshot**, so reports survive
   re-cleans and regenerations.
3. Proposals are **per-user overlay diffs**: the reporting user sees their own
   corrections applied in the viewer immediately; canonical artifacts are
   never mutated.
4. **Corroboration is automatic**: identical normalized proposals from
   distinct users on the same anchor are detected via a stored `fingerprint`
   and surfaced first in triage.
5. Reports are attributable and triageable
   (`open → corroborated → confirmed | invalid | fixed`); admin list + CLI
   export; confirmed corrections have a clear onward path into #53 golden sets.

## Non-goals (v1)

- **No waveform/timeline editor.** No word-timestamp dragging, no boundary
  scrubbing against audio. Deferred; revisit as an admin tool if boundary
  complaints dominate the data.
- **No canonical-artifact mutation and no automatic reprocessing.** Overlays
  only; the pipeline's files stay write-once. A corroborated report is a
  signal, not a command.
- **No global (other-users-visible) overlay in v1.** You see your own edits;
  everyone else sees canonical. A "community corrected" global overlay of
  *confirmed* diffs is Phase 4.
- **No reputation/anti-vandalism system.** Self-host is trusted; hosted
  multi-user gets rate limits + the only-confirmed-goes-global rule, which
  bounds abuse until volume justifies more.
- **No threads, replies, or votes.** Agreement is derived from independent
  identical diffs, not from a voting UI.
- **No word-level timing proposals.** Text and labels crowdsource well;
  timings don't (dynamic-ad offset drift).

## Correction taxonomy

| Category | Target | Proposal shape (`proposed_json`) | Fix surface |
|---|---|---|---|
| `wrong_speaker` | segment | `{speaker: str}` — picker over episode speakers + free entry | Diarization, speaker-mapping prompt |
| `wrong_words` | segment | `{old_text: str, new_text: str, word_span?}` — from text selection | ASR hint terms, cleaning prompt |
| `bad_boundary` | segment | `{action: "merge_prev" \| "merge_next" \| "split", split_word_index?}` | Segmenter |
| `wrong_kind` | segment | `{kind: SegmentKind}` | Kind classifier prompt |
| `summary_inaccurate` | section | free text | Summary prompt (faithfulness) |
| `summary_missing` | section | free text | Summary prompt (coverage) |
| `summary_malformed` | section | free text | Prompt + deterministic checks (#53) |
| `other` | either | free text | — |

`proposed_json = NULL` is always allowed — a pure flag remains the zero-effort
path. Proposals are validated server-side against the live artifact
(`old_text` must match the snapshot text; speaker/kind from known vocab or
explicit free entry; split index in range) — FM-7.

## Data model

One table, both backends (SQLite in-place `CREATE TABLE IF NOT EXISTS` in
`_create_schema()`/`_run_migrations()`, plus Alembic `0006_feedback.py`
mirroring `postgres_schema.SCHEMA_SQL` — the `user_briefing_schedules`
dual-track pattern).

```sql
CREATE TABLE IF NOT EXISTS feedback (
    id            TEXT PRIMARY KEY,        -- uuid4
    user_id       TEXT,                    -- NULL in single-user mode
    episode_id    TEXT NOT NULL,
    target_type   TEXT NOT NULL,           -- 'transcript_segment' | 'summary_section'
    category      TEXT NOT NULL,           -- taxonomy above
    comment       TEXT,                    -- optional free text
    anchor_json   TEXT NOT NULL,           -- durable anchor, shape per target_type
    proposed_json TEXT,                    -- structured proposal; NULL = pure flag
    context_json  TEXT NOT NULL,           -- server-built snapshot at report time
    fingerprint   TEXT,                    -- normalized hash(anchor+category+proposal); NULL for pure flags
    status        TEXT NOT NULL DEFAULT 'open',  -- open | corroborated | confirmed | invalid | fixed
    created_at    TEXT NOT NULL,           -- ISO-8601 UTC
    resolved_at   TEXT
);
CREATE INDEX IF NOT EXISTS idx_feedback_episode     ON feedback(episode_id);
CREATE INDEX IF NOT EXISTS idx_feedback_status      ON feedback(status);
CREATE INDEX IF NOT EXISTS idx_feedback_fingerprint ON feedback(fingerprint);
```

**Anchor shapes** (validated Pydantic models, never trusted raw):

- `transcript_segment`: `{ source_segment_ids: [int], algorithm_version: str,
  segment_id_hint: int, start_s: float, end_s: float }` — durable key into the
  write-once raw transcript; positional `segment_id` is a re-resolvable hint
  only (the split #54 settled).
- `summary_section`: `{ section_number: int, section_heading: str,
  summary_sha256: str }`.

**Context snapshot** (`context_json`): built by the **server** re-reading the
artifact — segment text/speaker/kind or section markdown (capped ~2 kB) as
rendered when flagged. Denormalized on purpose: reports stay interpretable
after any re-clean without artifact history.

**Fingerprint** = `sha256(episode_id | target_type | canonical_anchor |
category | normalized_proposal)`, where normalization lowercases and
whitespace-collapses text, and canonical_anchor is the sorted
`source_segment_ids` (offset-independent, so users on different ad-inserted
audio still converge). Two rows, distinct `user_id`, same fingerprint →
both flip `open → corroborated`. Threshold configurable
(`FEEDBACK_CORROBORATION_THRESHOLD`, default 2).

**Repository**: `feedback_repository.py` ABC + SQLite/Postgres impls via
`repositories/factory.py` — copy the `briefing_delivery` repository shape.

## API

New `web/routes/api_feedback.py`, standard router → `AppState` → service →
repository shape.

- `POST /api/feedback` — auth-gated when multi-user; body: `target_type`,
  `category`, `episode_id`, `anchor`, optional `proposed`, optional
  `comment`. Server validates proposal against the artifact, builds
  `context_json`, computes `fingerprint`, checks corroboration. Light
  rate-limit per user per episode.
- `GET /api/feedback?status=&category=&episode_id=&podcast_id=` — admin list,
  paginated, corroborated-first ordering.
- `GET /api/episodes/{id}/feedback/mine` — the reporting user's own
  corrections for an episode, consumed by the viewer to apply the per-user
  overlay.
- `PATCH /api/feedback/{id}` — admin status transition, sets `resolved_at`.

## Frontend

One shared `FeedbackPopover`, three entry points:

1. **Speaker relabel** — the speaker label in each segment becomes tappable →
   picker of the episode's speakers (already client-side in the color map) +
   free-text entry. Files `wrong_speaker` with proposal in one gesture.
2. **Text correction** — select text inside a segment → floating "Suggest
   fix" → inline input pre-filled with the selection. Files `wrong_words`
   with `{old_text, new_text}`.
3. **Flag icon** — per-segment hover action and per-`##`-section icon for
   everything else (`bad_boundary` with merge/split chips, `wrong_kind` with
   kind chips, summary categories with free text, plain flags).

**Per-user overlay**: on episode load the viewer fetches `/feedback/mine` and
merges the user's own proposals into the rendered segments (relabels, text
replacements; boundary proposals render as a marker, not a re-segmentation).
Corrected spans get a subtle "your edit" treatment with revert. This is
client-side merge only — canonical data untouched, other users unaffected.

## Closing the loop

1. **Triage** — admin list, corroborated reports first; snapshots + diffs make
   most decisions one-glance. `confirmed`/`invalid`/`fixed`.
2. **Aggregate** — `thestill feedback list/export` (JSON/CSV); first queries
   are GROUP BYs: category × podcast, category × `algorithm_version`,
   fingerprint cardinality. This is where patterns emerge — deliberately
   offline/ad-hoc in v1.
3. **Feed evals** — a confirmed *proposal* is expected-vs-observed ground
   truth verbatim. Emit the paste-ready golden-set snippet (entity-corrections
   precedent) into the #53 golden episode set, so every confirmed correction
   becomes a permanent regression guard.
4. **Fix upstream** — clusters map to fix surfaces per the taxonomy table;
   confirmed diffs double as before/after test cases for prompt changes.

## Phasing

| Phase | Scope | Gate |
|---|---|---|
| 1 — Capture (transcript) | Table + repos + `POST` + flag popover + speaker-picker & text-selection proposals | A proposal filed from the UI survives a re-clean with an interpretable snapshot + diff |
| 2 — Own-edits overlay + summary | `/feedback/mine` + client-side merge (relabels, text) + section flags + admin `GET`/`PATCH` list | Reporter sees their fix rendered; admin can confirm/invalidate |
| 3 — Use the data | `thestill feedback export`; golden-snippet emission for confirmed reports (#53); corroboration flip live | One confirmed correction round-trips into an eval run; two matching reports auto-corroborate |
| 4 — Community overlay | Global "corrected" toggle rendering **confirmed** diffs for everyone | Confirmed relabel visible to a second user without artifact mutation |
| 5+ — Pattern-driven (unscoped) | Whatever the data says: hint-term suggestions, prompt regression suites, boundary/waveform admin editor, reputation | Decided after ~50–100 real reports |

## Failure modes ([#42](42-robustness-and-failure-mode-hardening.md))

- **FM-1 (isolation):** failed snapshot extraction degrades to storing anchor
  + category with `context_json = {"snapshot_error": ...}` — never reject a
  report because the artifact read hiccuped. A malformed overlay row is
  skipped at render, never breaks the viewer.
- **FM-7 (unsanitized input):** anchors and proposals are range/vocab
  validated at write time; `old_text` must match the live snapshot;
  `comment`/`new_text` stored verbatim but always rendered as text, never
  markdown/HTML.
- **Silent-stale:** reports carry `algorithm_version`/`summary_sha256`; the
  overlay merge and the admin list badge stale reports instead of silently
  resolving anchors against the wrong generation.
- **Offset drift:** fingerprints and anchors are offset-independent
  (`source_segment_ids`, not seconds), so dynamic-ad audio variants still
  corroborate; timing-valued proposals are excluded by design.

## Open questions

1. **Corroboration threshold & scope** — default 2 distinct users; should
   near-matches (overlapping spans, same normalized replacement) count, or
   exact fingerprint only? Leaning: exact-only v1, measure the near-miss rate.
2. **Single-user attribution** — `user_id = NULL` fine in no-auth mode?
   (Leaning: yes.)
3. **Reporter feedback loop** — notify "your correction was confirmed"?
   Nice retention touch once multi-user is real; out of scope now.
4. **Speaker vocabulary** — should the picker also offer resolved entity
   names (#28) rather than only diarization labels? Powerful but couples two
   systems; revisit at Phase 3.
5. **Briefings** — third flaggable artifact (#33/#36); `target_type` leaves
   room; deferred until transcript/summary proves the loop.

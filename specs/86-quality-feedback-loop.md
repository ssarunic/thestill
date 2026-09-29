# Quality Feedback Loop: Report → Case → Guard

> **Status:** 💡 Proposal — problem framed, design drafted, not yet scheduled
> **Created:** 2026-09-24
> **Updated:** 2026-09-24
> **Priority:** High — every quality fix today is a one-off; nothing stops it regressing
> **Author:** Product & Engineering
> **Related:** [#59 transcript-summary-feedback](59-transcript-summary-feedback.md) (capture design for transcript and summary, folded in here), [#53 eval-runs-and-summary-rubric](53-eval-runs-and-summary-rubric.md) (the run/compare machinery this builds on; delivers its Phase 4 gate), [#42 robustness-and-failure-mode-hardening](42-robustness-and-failure-mode-hardening.md), [#81 live-wikidata-entity-linking](81-live-wikidata-entity-linking.md), [#82 summary-entity-links](82-summary-entity-links.md), [#83 summary-entity-mentions-pipeline](83-summary-entity-mentions-pipeline.md), [#26 pre-deploy-security-checklist](26-pre-deploy-security-checklist.md)

---

## Executive Summary

Thestill's output is judged by readers long before we notice: a name
linked to the wrong Wikidata entity, a speaker mislabelled, a summary
claim the episode never made. Today that signal is lost, and the fixes
we do make are one-offs: a blacklist row here, an alias removed there,
with nothing that fails if the next model, prompt or linker change
brings the error back.

This spec turns quality into a loop with three durable objects:

1. **Report** — a reader (first the operator, later any user) marks a
   specific thing as wrong: a durable anchor, a snapshot of what they
   saw, a category from a fixed taxonomy, and where cheap, the expected
   value. One model for every artifact kind: transcript segment, summary
   block, entity link, citation, briefing item.
2. **Case** — an accepted report becomes a versioned test record: the
   input pointer and the exact expected outcome, with the report id as
   provenance. Cases are JSON in the repo, consumed by a Python runner
   and, for client-side rules, by vitest.
3. **Guard** — cases run as a `cases` rubric under the [#53](53-eval-runs-and-summary-rubric.md)
   eval runner: deterministic cases in CI on every PR against frozen
   inputs, all cases nightly against the live database, with a **no
   regression** rule: a case that passed yesterday and fails today blocks
   the change, and names the report that created it.

Fixes are recorded operations on existing primitives (entity overrides
and blacklists, alias edits, overlay diffs), never hand edits, so any
fix can be replayed after a rebuild. The three objects exist in
fragments already: [#59](59-transcript-summary-feedback.md) designed
the report for transcripts and summaries; the entity corrections
endpoint already applies a fix and returns a "paste-ready golden
snippet" nobody consumes ([api_entities.py:644](../thestill/web/routes/api_entities.py#L644));
[#53](53-eval-runs-and-summary-rubric.md) built append-only runs with
`compare`. This spec connects them and adds the discipline: **no
accepted report without a case, no case without a report.**

**Key principle:** a regression is a person's complaint resurfacing, not
an abstract score dip. Aggregate scores stay useful for prompt and model
comparisons; cases are what make the system safe to change.

---

## Problem

The evidence from one afternoon (2026-09-24, spec [#82](82-summary-entity-links.md)
browser check on the Karpathy "Deep Dive into LLMs" episode):

| What the reader sees | What the data says | How it got there |
|---|---|---|
| "LLM" links to *Master of Laws* | `company:master-of-laws`, alias `LLM`, 142 mentions on prod, nearly all AI usage | A 2022-era ReFinED decision; the stored alias then feeds the extractor's anchor scan at confidence 1.0, so the linker never gets another look |
| "Reinforcement" links to the psychology concept | Q1321905 carries the alias "reinforcement learning"; the real concept Q830687 is typed *company* and carries the alias "frontier labs" | Same mechanism; the summary's shared-term rule dropped the phrase, the single-token canonical slipped through |
| "PhD" links to a *person* | `person:doctor-of-philosophy` | P31 typing |

Each of these can be fixed today with existing primitives
(`resolution-blacklist`, `entity`, `resolve-entities`,
`repair-entity-types`). None of those fixes leaves a trace that would
fail if the next linker version, alias-hygiene pass or backfill
reintroduces the error. `clean-aliases` explicitly keeps any alias the
post-fix resolver has linked once, so it protects these mistakes rather
than removing them.

More generally:

- **Reports have nowhere to go.** The operator notices errors while
  reading and either fixes them on the spot from the CLI or forgets
  them. A future user has no affordance at all.
- **Fixes are not replayable.** A blacklist row or alias edit is applied
  to the live database. A rebuild (`rebuild-entities`), a backfill
  ([#81](81-live-wikidata-entity-linking.md) Phase 4) or a migration can
  undo it silently.
- **Evals measure averages, not promises.** The golden set and rubrics
  ([#53](53-eval-runs-and-summary-rubric.md)) tell us whether a prompt
  change is better on balance. They cannot tell us that the thing a
  reader complained about last month is still fixed.
- **The one regression guard that exists is hand-authored.** The entity
  resolution golden test ([test_entity_resolution_golden.py](../tests/unit/core/test_entity_resolution_golden.py))
  has six cases, each typed in by hand with a frozen model snapshot. That
  is the right shape and the wrong workflow: it cannot scale to hundreds
  of corrections.

## Goals

1. **Report from every surface** the reader already uses: transcript
   segment, summary block, entity peek, citation button, briefing item.
   One gesture for a flag; one more tap for a structured proposal.
2. **Every accepted report yields a case** automatically, with the
   report as provenance, and every case is runnable from the CLI and CI.
3. **Fixes are recorded operations.** Accepting a report applies a named
   primitive with its arguments; `thestill reports replay` reapplies
   them in order after any rebuild.
4. **No regression rule.** A change that makes a previously passing case
   fail cannot merge or deploy without an explicit, reasoned override
   that retires or amends the case.
5. **Aggregation by category, stage and podcast** so the report stream
   itself prioritises pipeline work.
6. **Operator first, users second.** Everything works in single-user
   mode from day one; multi-user reporting adds gating, corroboration and
   a triage queue without changing the model.

## Non-goals

- A transcript editor. [#59](59-transcript-summary-feedback.md) Tier 2
  stays optional and later; structured proposals capture most of the
  value.
- Automatic fixes from user reports. A user's report is a suggestion
  until the operator accepts it; only accepted reports touch data.
- Replacing the [#53](53-eval-runs-and-summary-rubric.md) rubrics. Score
  rubrics compare prompts and models on balance; cases guard specific
  promises. Both run under the same runner.
- Reputation, rewards or notifications for reporters (v1).
- Fixing the entity data quality problems above. They are the worked
  example; the fixes happen through Phase 1 of this spec, and the
  linker-side prevention belongs to [#81](81-live-wikidata-entity-linking.md)
  Phase 4.

## Design

### The loop

```text
 reader sees an error
        │
        ▼
   ┌─────────┐   accept + fix    ┌────────────┐   thestill cases add   ┌────────┐
   │ REPORT  │ ────────────────► │ FIX (op)   │ ─────────────────────► │ CASE   │
   │ anchor  │                   │ primitive  │                        │ input  │
   │ snapshot│   reject /        │ + args     │                        │ expect │
   │ category│   duplicate       │ replayable │                        │ origin │
   └─────────┘                   └────────────┘                        └────┬───┘
        ▲                                                                   │
        │  a failing case names its report                                  ▼
        │                                                     ┌──────────────────────┐
        └──────────────────────────────────────────────────── │ GUARD: cases rubric  │
                                                              │ CI (frozen inputs)   │
                                                              │ nightly (live DB)    │
                                                              │ no-regression rule   │
                                                              └──────────────────────┘
```

### Reports

One table, both backends (the [#59](59-transcript-summary-feedback.md)
`feedback` table, renamed and widened; the shapes below supersede
[#59](59-transcript-summary-feedback.md) §Data model).

```sql
CREATE TABLE IF NOT EXISTS reports (
    id            TEXT PRIMARY KEY,          -- uuid4
    user_id       TEXT,                      -- NULL in single-user mode
    episode_id    TEXT,                      -- NULL for corpus-level targets (an entity)
    target_kind   TEXT NOT NULL,             -- see taxonomy
    category      TEXT NOT NULL,             -- see taxonomy
    anchor_json   TEXT NOT NULL,             -- durable anchor, shape per target_kind
    context_json  TEXT NOT NULL,             -- server-built snapshot at report time
    proposed_json TEXT,                      -- structured proposal; NULL = pure flag
    comment       TEXT,                      -- free text, rendered as text only
    fingerprint   TEXT,                      -- sha256(anchor|category|proposal), NULL for flags
    status        TEXT NOT NULL DEFAULT 'open',
                  -- open | accepted | rejected | duplicate | fixed | guarded
    fix_json      TEXT,                      -- the recorded operation, once accepted
    case_id       TEXT,                      -- the case created from it, once guarded
    duplicate_of  TEXT,                      -- report id
    created_at    TEXT NOT NULL,             -- ISO-8601 UTC, +00:00
    resolved_at   TEXT,
    resolved_by   TEXT
);
```

**Target kinds and anchors** (validated Pydantic models):

| `target_kind` | Anchor | Snapshot (`context_json`) |
|---|---|---|
| `transcript_segment` | `{source_segment_ids, algorithm_version, segment_id_hint, start_s, end_s}` (from [#59](59-transcript-summary-feedback.md)) | segment text, speaker, kind |
| `summary_block` | `{block_index, block_sha256, summary_sha256, nearest_cite_id?}` — block = paragraph/list item, the same unit [#82](82-summary-entity-links.md) uses | block markdown (≤ 2 kB) |
| `entity_link` | `{mention_id?, surface_form, entity_id, wikidata_qid?, segment_id?, source: transcript\|summary\|speaker}` | surface, excerpt, entity name/type/QID, resolution method |
| `citation` | `{cite_id, raw_label, segment_id_hint, cited_playback_s}` | the summary sentence and the target segment text |
| `briefing_item` | `{briefing_id, item_index, item_sha256, episode_id}` | the item's text |

**Categories** ([#59](59-transcript-summary-feedback.md)'s table, extended):

| Category | Target | Proposal | Fix surface |
|---|---|---|---|
| `wrong_speaker` | segment | `{speaker}` | diarization, speaker-mapping prompt |
| `wrong_words` | segment | `{old_text, new_text}` | ASR hints, cleaning prompt |
| `bad_boundary` | segment | `{action: merge_prev\|merge_next\|split, index?}` | segmenter |
| `wrong_kind` | segment | `{kind}` | kind classifier |
| `summary_inaccurate` | block | free text, optional `{claim}` | summary prompt (faithfulness) |
| `summary_missing` | block or episode | free text | summary prompt (coverage) |
| `summary_malformed` | block | — | prompt + deterministic checks |
| `wrong_entity` | entity_link | `{target_qid \| target_entity_id \| unresolvable}` | linker, aliases, blacklist |
| `not_an_entity` | entity_link | — | extractor, blacklist |
| `missing_entity` | segment or block | `{surface_form, target_qid?}` | extractor, [#83](83-summary-entity-mentions-pipeline.md) |
| `wrong_entity_type` | entity_link | `{type}` | P31 typing |
| `wrong_timestamp` | citation | `{segment_id \| seconds}` | citation resolver |
| `wrong_fact` / `wrong_attribution` | briefing_item | free text, optional `{claim}` | briefing prompt |
| `other` | any | free text | — |

A pure flag (no proposal) is always allowed. Proposals are validated
against the live artifact at write time (`old_text` must match the
snapshot, QIDs must exist, speakers from the episode's vocabulary or
explicit free entry).

### Fixes are recorded operations

Accepting a report applies exactly one primitive and stores it as
`fix_json = {"op": ..., "args": {...}, "applied_at": ..., "result": {...}}`.
The primitives are the ones that exist:

| `op` | Today's implementation | Used for |
|---|---|---|
| `entity.correct` | `apply_correction` ([entity_review.py:246](../thestill/core/entity_review.py#L246)): `blacklist`, `force_entity`, `drop`, `force_unresolvable`, then re-resolve | `wrong_entity`, `not_an_entity` |
| `entity.alias_remove` / `entity.alias_add` | `thestill entity`, `entity-alias-add` | alias pollution |
| `entity.retype` | `repair-entity-types` for one entity | `wrong_entity_type` |
| `transcript.overlay` | [#59](59-transcript-summary-feedback.md) per-user overlay diff, promoted to the global "corrected" layer on accept | `wrong_speaker`, `wrong_words`, `wrong_kind` |
| `citation.override` | new: a row in the citations sidecar's override map | `wrong_timestamp` |
| `none` | the fix is a prompt/model change, tracked only by the case | summary and briefing categories, `bad_boundary` |

`thestill reports replay [--since] [--dry-run]` reapplies every
`fix_json` in `created_at` order and reports which ones no longer apply
(entity gone, segment ids changed). This is what makes a rebuild or a
backfill safe: run the rebuild, replay the fixes, run the cases.

Summary and briefing errors have no per-artifact fix on purpose.
Canonical artifacts stay write-once ([#59](59-transcript-summary-feedback.md)'s
argument holds); the fix is upstream, and the case is what proves it
landed.

### Cases

A case is a JSON record under `tests/fixtures/eval/cases/<kind>/*.json`,
versioned with the code because it *is* a test. Generated by
`thestill cases add --from-report <id>` (or by the accept action in the
admin UI), never typed by hand except to amend.

```json
{
  "id": "entity_link/llm-is-not-master-of-laws",
  "kind": "entity_link",
  "check": "deterministic",
  "origin": {"report_id": "…", "created_at": "2026-09-24T13:20:11+00:00", "created_by": "ssarunic"},
  "input": {
    "podcast_slug": "andrej-karpathy",
    "episode_slug": "deep-dive-into-llms-like-chatgpt",
    "surface_form": "LLM",
    "excerpt": "…the Large Language Model (LLM) AI technology that powers ChatGPT…",
    "frozen": {"candidates": [{"qid": "Q754848", "label": "Master of Laws"}, {"qid": "Q115305900", "label": "Large language model"}]}
  },
  "expect": {"status": "resolved", "qid": "Q115305900", "forbid_qids": ["Q754848"]},
  "status": "active",
  "notes": "ReFinED-era mislink; alias LLM removed from company:master-of-laws"
}
```

**Expectation shapes per kind:**

| Kind | `check` | `expect` |
|---|---|---|
| `entity_link` | deterministic | `{status, qid?, entity_id?, forbid_qids[]}` — evaluated against the live linker's decision (frozen candidates in CI) and against the stored mention (nightly) |
| `entity_type` | deterministic | `{entity_id, type}` |
| `transcript_segment` | deterministic | `{speaker?, text_contains[]?, text_not_contains[]?, kind?}` against the corrected layer |
| `citation` | deterministic | `{cite_id, segment_id}` or `{within_s}` |
| `summary_block` | judged | `{must_not_claim?, must_mention?}` — one judge question per case, pinned judge, the [#53](53-eval-runs-and-summary-rubric.md) way |
| `briefing_item` | judged | same shape |
| `summary_entity_link` | deterministic, **vitest** | `{text, entities_fixture, expect_links: [{text, entity_id}]}` — the [#82](82-summary-entity-links.md) client rule, so a wrong match reported in the summary guards the term index |

`frozen` is the FM-5 boundary from [#42](42-robustness-and-failure-mode-hardening.md):
CI never calls Wikidata or an LLM, so a deterministic case carries the
real candidate list captured at fix time (the same idea as the
`GoldenCase.refined` snapshot, produced by the tool instead of by hand).
Nightly runs ignore `frozen` and use the live services.

A case has a lifecycle: `active` → `retired` (with a reason and, usually,
a replacement case id). Retiring is a reviewed change to a JSON file in
a PR, never a runtime flag.

### Guards: the `cases` rubric

Cases run through the [#53](53-eval-runs-and-summary-rubric.md) runner as
a rubric named `cases`, so runs are append-only directories with a
manifest, `compare` works, and the judge is pinned:

```bash
thestill cases run --mode fixtures                 # CI: deterministic kinds, frozen inputs, no network
thestill cases run --mode live --kind entity_link  # nightly: against the database and live linker
thestill cases run --mode live --judged            # nightly: judged kinds, pinned judge
thestill cases show <run-id>                       # per-case pass/fail with the originating report
thestill cases compare <run-a> <run-b>             # newly failing / newly passing / unchanged
```

**No-regression rule.** `compare` against the last accepted run classifies
each case as `pass`, `fail`, `newly_failing`, `newly_passing`,
`retired`. Any `newly_failing` case fails the run. CI runs fixtures mode
on every PR (fast: no network, hundreds of cases in seconds). Nightly
runs live mode and posts the delta; the deploy gate
([#26](26-pre-deploy-security-checklist.md) style) refuses a tag while
the last nightly has `newly_failing` cases. Overriding requires retiring
or amending the case in the same PR, with a reason, which is how the
audit trail survives.

Per-case output names the report, the category and the reporter, so the
person who reads a red CI line sees "LLM linked to Master of Laws again,
reported 2026-09-24", not "entity_link case 37 failed".

The existing hand-written golden test migrates into `cases/entity_link/`
in Phase 1 and the Python test becomes a thin loader over the JSON.

### Aggregation

`thestill reports stats [--since] [--by category|podcast|stage|week]`
answers the questions that decide pipeline work: which category is
growing, which podcast produces the most `wrong_speaker`, how many
`wrong_entity` reports name the same surface form (a linker rule), what
share of reports have a case (`guarded / accepted`). The first dashboard
is a table in the admin page; no charts until the numbers are boring.

### Frontend

One `ReportButton` (desktop: a small flag in the hover affordance;
phone: an entry in the existing bottom sheets) and one `ReportSheet`
with the category chips for the target kind and the proposal input where
one exists. Five mount points:

- transcript segment (hover action, and the speaker label picker from
  [#59](59-transcript-summary-feedback.md));
- summary block (hover action on paragraphs and list items);
- entity peek ([#82](82-summary-entity-links.md) card and sheet):
  "Wrong entity?" opens a picker of Wikidata candidates for the surface
  form, plus "not an entity"; in single-user mode the accept happens in
  the same flow;
- citation button: "Wrong place?" with the segment the reader is looking
  at as the proposal;
- briefing item.

Admin page `/admin/reports`: list (corroborated and accepted first),
snapshot and proposal side by side, accept (choose the fix op when more
than one applies), reject, mark duplicate, and after a fix, "Guard"
shows the generated case JSON and writes it to the repo checkout when
running locally (or emits it for a PR when running on prod). Everything
is keyboard-driven; the operator will triage dozens at a time.

### Multi-user

Users see the same buttons once `MULTI_USER` is on. Their reports land as
`open`, never apply anything, are rate-limited per user and episode, and
corroborate by fingerprint exactly as [#59](59-transcript-summary-feedback.md)
specifies (two distinct users, same fingerprint → `corroborated`, sorted
first in triage). Per-user overlays for their own transcript proposals
come with [#59](59-transcript-summary-feedback.md) Phase 2, unchanged.

### Failure modes ([#42](42-robustness-and-failure-mode-hardening.md))

- **Errors as empty results (FM-1):** a case whose input cannot be loaded
  (episode gone, sidecar missing, linker down) is `error`, never `pass`
  and never silently skipped. Nightly treats `error` on a previously
  passing case as a regression.
- **Checkpoint before durability (FM-2):** order on accept is apply fix →
  verify the fix landed (re-read the mention/segment) → write `fix_json`
  and the case. A case is never written for a fix that did not apply.
- **Consistent-mock tests (FM-5):** frozen inputs are captured from the
  real services at fix time by the tool, never composed by hand; a case
  whose frozen candidates do not contain the expected QID is rejected at
  `cases add`.
- **Silent degradation (FM-4):** the deploy gate reads the nightly run's
  manifest; a missing nightly is a blocking state, not a pass.
- **Unsanitized input (FM-7):** comments and proposals are stored
  verbatim and rendered as text; proposals are vocabulary- and
  range-checked; judge output goes through the [#53](53-eval-runs-and-summary-rubric.md)
  sanitizer and report model.
- **Path drift (FM-6):** case files live under one directory that the
  runner discovers; `cases lint` in CI fails on an unreadable case, a
  duplicate id, or a case whose `origin.report_id` does not exist in the
  export shipped with the fixtures.

## Cost and capacity

- Tables: reports grow by human action; hundreds per month at most.
- CI: deterministic cases only, no network; hundreds of cases in a few
  seconds.
- Nightly: live linker calls bounded by the number of `entity_link`
  cases (cached decisions make repeats free); judged cases at one short
  prompt each on the pinned judge, target ≤ 200 cases → cents per night
  on Gemini Flash, a few dollars on Claude.

## Testing

- Repository contract tests for `reports` on both backends (the
  `briefing_delivery` shape).
- Anchor and proposal validators: every target kind, malformed input,
  stale artifact hash.
- `apply → verify → record` ordering: a fix that fails to verify writes
  nothing.
- `cases add`: generated JSON round-trips through the runner; frozen
  candidates without the expected QID are refused.
- Runner: `pass/fail/error/newly_failing` classification, retired cases
  excluded, judged cases go through the pinned judge with mocked LLM in
  unit tests.
- Frontend: `ReportSheet` per target kind; the entity peek's "Wrong
  entity?" flow; the vitest loader for `summary_entity_link` cases.
- End to end (Playwright, local server): report the "LLM" link from the
  peek, accept with `force_entity Q115305900`, see the case written,
  run `cases run --mode fixtures`, see it pass; revert the alias, run
  again, see it fail with the report named.

## Phases

| Phase | Scope | Gate |
|---|---|---|
| 0 — Capture (operator) | `reports` table and repos, `POST /api/reports`, admin list, `ReportButton` on all five surfaces with category and comment only | Today's three entity errors reported from the summary tab, visible in `/admin/reports` with snapshots |
| 1 — Entities end to end | `wrong_entity` / `not_an_entity` / `wrong_entity_type` proposals in the peek; accept → `entity.correct` → case written; `cases run --mode fixtures` in CI; golden test migrated to JSON cases | "LLM" and "Reinforcement" cases pass after the fix and fail on revert; CI red on the revert |
| 2 — Transcript and summary | [#59](59-transcript-summary-feedback.md) Tier 1 proposals (speaker, words, kind, boundary flags) and summary block reports; transcript cases (deterministic) and summary cases (judged) | A speaker relabel accepted from the UI is a passing case; a faithfulness report is a judged case that fails on the old summary |
| 3 — Guard for real | `cases run --mode live` nightly, `compare` with the no-regression rule, deploy gate, `reports replay`, `reports stats` | A deliberate regression on a branch is caught by nightly and blocks the tag; a rebuild followed by `replay` leaves all cases green |
| 4 — Users | Reporting for non-admin users, rate limits, fingerprint corroboration, triage queue ordering | A second account's matching report corroborates; nothing changes in data until the operator accepts |
| 5 — Pattern-driven (unscoped) | Whatever `stats` says: surface-form rules for the linker, hint-term lists for ASR, prompt suites from clustered summary cases; [#59](59-transcript-summary-feedback.md) Tier 2 workbench if `bad_boundary` reports justify it | Decided after ~100 accepted reports |

Phase 1 is deliberately first: the backend exists, the evidence is
fresh, and it exercises the whole loop (report, recorded fix, generated
case, CI guard) on one artifact kind before the more expensive transcript
UI.

## Relationship to existing specs

- [#59](59-transcript-summary-feedback.md) remains the design for
  transcript and summary *capture* (anchors, proposals, overlays,
  corroboration, Tier 2 workbench). Its `feedback` table becomes
  `reports` here, its Phase 3 "feed evals" becomes the case pipeline,
  and its Phases 1–2 are this spec's Phase 2.
- [#53](53-eval-runs-and-summary-rubric.md) gains the `cases` rubric and
  gets its Phase 4 CI gate from this spec's Phases 1 and 3. Score
  rubrics and the golden set are unchanged and still the tool for
  prompt/model comparisons.
- [#81](81-live-wikidata-entity-linking.md) Phase 4 (backlog sweep,
  alias hygiene, ReFinED removal) should run *after* Phase 1 here so
  every alias it removes and every mention it re-decides is guarded, and
  `reports replay` runs after the sweep.
- [#83](83-summary-entity-mentions-pipeline.md) gets its "measure #82's
  gaps first" data from `missing_entity` reports on summary blocks.
- [#26](26-pre-deploy-security-checklist.md)'s GO/NO-GO gains one line:
  the last nightly `cases` run has no `newly_failing` cases.

## Alternatives considered

- **GitHub issues as the report store.** No durable anchors, no snapshot,
  no fingerprinting, no path to a case. Fine for bugs in code, useless for
  ten thousand small data errors.
- **Per-bug pytest fixtures written by hand when something is fixed.**
  The current golden test. Right shape, does not scale, and nothing links
  a fixture to why it exists.
- **Judge everything with an LLM.** Facts that can be asserted exactly
  (a QID, a speaker, a segment id) must be asserted exactly; a judge
  adds noise and cost and hides regressions inside a mean.
- **Fix data directly and skip the report.** What we do today. Loses the
  provenance, the aggregate signal and the guard.
- **Build the editor first ([#59](59-transcript-summary-feedback.md) Tier 2).**
  The most expensive pixel for the least data; deferred until reports
  show boundary errors matter.

## Open questions

1. **Where do prod-generated cases go?** The admin accepts on prod; the
   case must reach the repo. Proposed: prod writes cases to a `cases`
   table and `thestill cases pull` syncs them into the fixtures directory
   for a PR, with `cases lint` refusing a fixture set that lags the
   table. Alternative: the admin UI copies JSON and the operator pastes.
2. **Judged case wording.** One judge question per case is simplest;
   should summary cases instead attach to the existing `summary` rubric
   as extra deterministic checks where a substring suffices?
3. **Corrected layer for transcripts.** Accepting a `wrong_speaker`
   report promotes the per-user overlay to a global layer
   ([#59](59-transcript-summary-feedback.md) Phase 4). Does the corrected
   layer feed downstream stages (summary, entities) on the next rebuild,
   or stay serve-time only? Leaning: serve-time in v1, revisit when
   `replay` exists.
4. **Case identity across re-cleans.** Transcript anchors use
   `source_segment_ids`; a raw re-transcription changes them. Proposed:
   such cases go `error` (not `fail`) and are listed for re-anchoring.
5. **Reporter feedback.** "Your report was accepted" is cheap once
   multi-user is real; out of scope until Phase 4.

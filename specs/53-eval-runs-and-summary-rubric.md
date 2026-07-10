# Eval Runs and Summary Rubric

> **Status:** 🚧 Implemented — Phases 1–3 (2026-07-10); Phase 4 (provenance capture, briefing rubric, CI gate) future
> **Created:** 2026-07-07
> **Author:** Product & Engineering
> **Related:** [#42 robustness-and-failure-mode-hardening](42-robustness-and-failure-mode-hardening.md), [#28 corpus-search-and-entities](28-corpus-search-and-entities.md) (§1.12 harness-eval), [#18 segment-preserving-transcript-cleaning](18-segment-preserving-transcript-cleaning.md), [#41 llm-prohibited-content-fallback](41-llm-prohibited-content-fallback.md)

---

## Executive Summary

Thestill already has two LLM-as-judge rubrics — raw-transcript quality
(`TranscriptEvaluator`) and cleanup quality (`PostProcessorEvaluator`) in
[core/evaluator.py](../thestill/core/evaluator.py) — but their output is
**evidence without provenance**: a bare JSON report with no record of which
judge model produced it, which prompt version it used, when it ran, or what
exact bytes it judged. `--force` overwrites the previous report, so the one
question evals exist to answer — *"did this change make quality better or
worse?"* — is unanswerable today.

This spec makes **eval runs first-class and append-only**. A run is one
invocation of one rubric over a set of artifacts with a pinned judge
configuration; it produces an immutable run directory containing a manifest
(judge model, rubric version + prompt hash, artifact content hashes,
per-item status) plus per-item reports and aggregate stats. A new
`thestill eval` command group provides `run`, `list`, `show`, and `compare`.

Only after that infrastructure exists does the third rubric land: a
**summary evaluator** (coverage, faithfulness, attribution — plus cheap
deterministic checks like section presence and timestamp validity, computed
in Python rather than delegated to the judge). Building the runner first
means the summary rubric is born comparable, instead of inheriting the
overwrite-and-forget behaviour of the current commands.

Explicitly out of scope: storing eval results in the database, a web UI,
CI gating, judge ensembles, and briefing/narration rubrics (future work).

---

## Motivation

1. **Results are not reproducible.** The saved report
   (e.g. [data/evaluations/raw/prof-g-markets/…_evaluation.json](../data/evaluations/raw/prof-g-markets/did-the-u-s-just-hand-its-ai-edge-to-china_952ffef7_transcript_evaluation.json))
   contains only the judge's JSON. The judge model is echoed to the console
   and lost. Two reports produced by different judges are silently
   incomparable.
2. **Prompts are unversioned.** The rubric prompts live as class constants
   ([evaluator.py:32](../thestill/core/evaluator.py#L32),
   [evaluator.py:131](../thestill/core/evaluator.py#L131)). Editing a prompt
   invalidates every existing report with no trace that it happened.
3. **`--force` destroys history.** Re-evaluating overwrites the prior
   report in place, so before/after comparison across a pipeline change is
   impossible by construction.
4. **No comparison or aggregation tooling.** Even with two intact reports
   there is no command to diff them, and no per-podcast or per-run score
   aggregation.
5. **The main deliverable has no rubric at all.** Summaries — arguably the
   product's core output — are never judged. Neither are briefings.
6. **Prompt iteration is about to accelerate.** #18 (segment-preserving
   cleaning) and #41 (provider fallback) both change what the cleaning
   stage emits; without a regression harness every prompt/provider change
   is judged by vibes.

### Why runner-first, rubric-second

A summary rubric added to the current machinery would produce one more
unversioned, overwritable JSON file. The comparability problem is upstream
of every rubric, so it gets fixed once, in one place, and each rubric
(existing and future) plugs into it.

---

## Current State (reference)

| Piece | Where | Behaviour |
|---|---|---|
| Raw-transcript rubric | `TranscriptEvaluator` ([evaluator.py:29](../thestill/core/evaluator.py#L29)) | LLM judge → JSON: name/entity/word error counts + examples, structure flags, 0–10 scores (accuracy, completeness, entity_handling, structural_clarity) |
| Cleanup rubric | `PostProcessorEvaluator` ([evaluator.py:128](../thestill/core/evaluator.py#L128)) | LLM judge, optionally with original transcript for comparison → fidelity (invented-content count), formatting flags, 0–10 scores (fidelity, formatting_clarity, readability, enhancements_value) |
| CLI | `evaluate-raw-transcript` ([cli.py:2335](../thestill/cli.py#L2335)), `evaluate-clean-transcript` ([cli.py:2486](../thestill/cli.py#L2486)) | Standalone (single file) or batch (discover un-evaluated episodes); skip-if-exists unless `--force` (overwrite) |
| Output | `data/evaluations/{raw,clean}/<podcast-slug>/<stem>_evaluation.json` via `PathManager.raw_transcript_evaluation_file` / `clean_transcript_evaluation_file` | Judge JSON only; no metadata |
| Judge config | `create_llm_provider_from_config` — whatever the pipeline LLM is | temperature 0.2, `response_format={"type": "json_object"}` |
| Entity harness | `thestill harness-eval` ([cli.py:4320](../thestill/cli.py#L4320)) | Deterministic pass/fail gate for spec-#28 §1.12 reference questions — different tool, untouched by this spec |

Provenance gap worth naming: the `Episode` model records artifact *paths*
(`raw_transcript_path`, `clean_transcript_path`, `summary_path`) but not
which transcriber/LLM/prompt produced each artifact, and the artifacts
themselves carry no frontmatter. The run manifest therefore records the
**content hash** of each judged artifact (its honest identity) and an
operator-supplied `--note`; capturing producer provenance in the pipeline
itself is future work (Open Question 1).

---

## Design

### Goals

- Every eval result is traceable to: judge (provider, model, temperature),
  rubric (name, version, prompt hash), input (path + sha256), and time.
- Runs are append-only. Nothing ever overwrites a previous result.
- `eval compare` answers "better or worse, on which dimensions, by how
  much, and is the comparison even valid?" in one command.
- Rubrics are pluggable: adding the summary rubric (Phase 3) or a future
  briefing rubric touches the registry, not the runner.

### Non-goals

- **No DB storage.** Eval runs are developer tooling: file-based runs are
  `jq`-able, diffable, and committable as golden baselines, and avoid an
  Alembic migration across the freshly dual SQLite/Postgres backends (#44).
  Revisit if a web UI ever wants eval history.
- **No CI gate** (yet). The golden-set fixture (Phase 3) is the on-ramp.
- **No judge ensembles / formal significance testing.** `--samples` gives
  variance visibility; statistics stay descriptive in v1.
- **No briefing/narration rubric.** Follow-up spec once #34 audio lands.

### Run model

One **run** = one rubric × one judge config × one set of items, executed at
one point in time.

```text
data/evaluations/runs/
└── 20260707-153012-clean-transcript-promptv2/   ← run_id
    ├── manifest.json      # what ran, on what, with what — see schema
    ├── summary.json       # aggregate stats (written last, after all items)
    └── items/
        ├── lenny-s-podcast_the-most-successful-ai-company.json
        └── acquired_costco.json
```

- `run_id` = `<UTC yyyymmdd-HHMMSS>-<rubric>-<label-slug>` (label slug
  omitted when `--label` not given). Creation fails if the directory
  exists — never merge into an existing run.
- The legacy `data/evaluations/{raw,clean}/` trees stay readable but are
  frozen; new writes go only under `runs/`.
- New `PathManager` methods: `evaluation_runs_dir()`,
  `evaluation_run_dir(run_id)` (guarded by `_assert_inside_root`, matching
  the existing evaluation-path helpers).

### Manifest schema (Pydantic, `thestill/evals/models.py`)

```jsonc
{
  "schema_version": 1,
  "run_id": "20260707-153012-clean-transcript-promptv2",
  "label": "promptv2",
  "note": "cleaning prompt v2 candidate, gemini-2.5-pro produced the artifacts",
  "created_at": "2026-07-07T15:30:12+00:00",   // UTC ISO-8601, FM-3
  "git_commit": "64a3827",                      // best effort; null outside a repo
  "rubric": {
    "name": "clean-transcript",
    "version": "1",
    "prompt_sha256": "ab34…"                    // hash of the exact system prompt sent
  },
  "judge": {
    "provider": "anthropic",
    "model": "claude-sonnet-5-20260203",       // dated snapshot, not a floating alias
    "temperature": 0.0,
    "pinned": true,                             // false = fell back to the pipeline provider
    "samples": 1
  },
  "items": [
    {
      "podcast_slug": "lenny-s-podcast-product-career-growth",
      "episode_slug": "the-most-successful-ai-company-youve-never-heard-of-qasar-younis",
      "artifacts": {
        "clean_transcript": {"path": "clean_transcripts/…_cleaned.md", "sha256": "9f1c…"},
        "raw_transcript":   {"path": "raw_transcripts/…_transcript.json", "sha256": "77aa…"}
      },
      "status": "ok",                 // ok | failed
      "error": null,                  // classified message when failed
      "report_file": "items/lenny-s-podcast_the-most-successful….json",
      "scores": {"fidelity": 8, "formatting_clarity": 9, "readability": 8, "enhancements_value": 6},
      "duration_s": 41.2
    }
  ],
  "counts": {"ok": 12, "failed": 1}
}
```

Design notes:

- **Scores are denormalized into the manifest** so `list`/`compare` never
  open N item files. The item file keeps the full judge report (examples,
  verdict prose) as evidence.
- **`prompt_sha256` is the ground truth** for "same rubric?"; the human
  `version` string is a courtesy. Any prompt edit must bump the version,
  and a unit test asserts the registry's recorded hash matches the prompt
  text (edit without bump = red test).
- **Artifact hashes make comparisons honest.** Two runs over "the same
  episode" may be judging different bytes (the clean transcript was
  regenerated in between); the hash is how `compare` detects that.
- With `--samples N > 1`, `scores` holds the per-dimension mean, a
  sibling `scores_std` map holds the per-dimension sample standard
  deviation (N itself lives once in `judge.samples`), and the item file
  keeps all N reports. Keeping `scores` flat means `list`/`show`/
  `compare` read one shape regardless of sampling.

### Rubric registry (`thestill/evals/rubrics.py`)

Each rubric is a declarative entry the runner consumes:

```python
@dataclass(frozen=True)
class Rubric:
    name: str                      # "raw-transcript" | "clean-transcript" | "summary"
    version: str
    system_prompt: str
    dimensions: tuple[str, ...]    # keys expected under report["scores"]
    inputs: tuple[str, ...]        # artifact kinds to load, e.g. ("clean_transcript", "raw_transcript")
    report_model: type[BaseModel]  # validates the judge's JSON before it is saved
    deterministic_checks: Callable[..., dict] | None = None   # Phase 3
```

The two existing prompts move verbatim out of `core/evaluator.py` into the
registry as version `"1"` — reports produced by the new runner for an
unchanged prompt remain comparable with historical intent, and
`core/evaluator.py` shrinks to a thin execution wrapper (or is absorbed
into the runner; implementer's choice, but only one copy of each prompt
may exist).

`report_model` is the FM-7 boundary: judge output goes through
`sanitize_text` ([utils/text_sanitizer.py](../thestill/utils/text_sanitizer.py))
→ `json.loads` → Pydantic validation. On failure, retry once; on second
failure the item is recorded as `failed` with the classified error. An
invalid report is never written as a success.

### Judge pinning

The judge is configured **independently of the pipeline LLM** — the status
quo (judge = whatever `create_llm_provider_from_config` returns) is the
anti-pattern every mature eval framework avoids, because a judge that
changes whenever the pipeline model changes destroys longitudinal
comparability, and a judge grading its own family's output is biased
(see below).

- New config: `EVAL_JUDGE_PROVIDER` + `EVAL_JUDGE_MODEL` (and
  `EVAL_JUDGE_TEMPERATURE`, default `0.0`). Precedence, promptfoo-style:
  `eval run --judge-model/--judge-provider` flag → `EVAL_JUDGE_*` env →
  pipeline provider fallback. Whichever layer wins is recorded in the
  manifest; when the fallback is used, the manifest carries
  `"judge": {"pinned": false, …}` and the CLI prints a warning — an
  unpinned run is allowed for ad-hoc use but visibly second-class.
- **Pin dated snapshot IDs, not aliases** (e.g. a `-YYYYMMDD` model id,
  not a floating family alias). Aliases drift server-side; the day the
  alias re-points, every subsequent score silently means something
  different and no manifest field can prove when it happened.
- **Prefer a judge from a different model family than the producer.**
  Clean transcripts and summaries are LLM-emitted; judges systematically
  favor their own outputs and their own family's (self-preference bias,
  ~10–25% on same-family comparisons in published measurements). If the
  pipeline cleans with Gemini, judge with Claude or GPT — cross-family by
  configuration, since the runner cannot enforce it while producer
  provenance is unrecorded (Open Question 1).
- **Changing the judge = re-baselining.** Deltas are only meaningful
  within a judge. `eval compare` already enforces this at read time: two
  runs with different judges over identical artifacts classify as a
  *judge comparison*, never as a pipeline delta. The operational rule:
  after upgrading `EVAL_JUDGE_MODEL`, re-run the golden set once to
  establish the new baseline before trusting any comparison.

### CLI (`thestill eval` group)

```bash
# Run a rubric (batch discovery, same filters as today)
thestill eval run --rubric clean-transcript \
    [--podcast-id X] [--episode-id Y] [--max-episodes N] \
    [--episodes-file tests/fixtures/eval/golden_episodes.json] \
    [--label promptv2] [--note "…"] [--samples 3] [--dry-run]

thestill eval list                      # run_id, rubric, judge model, n ok/failed, label
thestill eval show <run-id>             # manifest + per-item score table
thestill eval compare <run-a> <run-b> [--json]
```

- `eval run` selects episodes that *have* the rubric's required artifacts
  (it no longer skips "already evaluated" — runs are cheap to enumerate
  and never collide). `--episodes-file` pins an explicit episode list for
  apples-to-apples runs over time.
- Progress and failures go through structlog with `run_id` and
  `episode_id` context, per the logging conventions.
- A run with failures still writes its manifest (`counts.failed > 0`) and
  exits non-zero — partial results are visible, never silent (FM-1/FM-4:
  a judge outage must not read as "these episodes are fine" or as an
  empty run).
- Interruption safety: item reports are written as each item completes;
  the manifest is written atomically (temp file + rename) at the end. A
  killed run leaves item files but no manifest — `eval list` ignores it,
  and a `--resume <run-id>` is deliberately *not* offered (re-run instead;
  runs are append-only and disposable).
- The legacy `evaluate-raw-transcript` / `evaluate-clean-transcript`
  commands keep working for one release as deprecated wrappers that print
  the equivalent `eval run` invocation; batch mode delegates to the
  runner. Removal is a follow-up chore.

### Compare semantics

`eval compare A B` first requires `rubric.name` to match (comparing
accuracy to fidelity is a category error), then classifies what actually
differs — this classification is the headline of the output, because it
states what the delta *measures*:

| artifacts (per-episode sha256) | judge + prompt | classification |
|---|---|---|
| identical | differ | **Judge comparison** — same work, different grader |
| differ | identical | **Pipeline comparison** — same grader, different work (the interesting one) |
| differ | differ | **Confounded** — loud warning; deltas attribute to nothing |
| identical | identical | Reproducibility / variance check |

Items are joined on `(podcast_slug, episode_slug)`. Unmatched items are
listed, excluded from deltas, and counted (no silent truncation). Output:

- Per-dimension aggregate: mean A, mean B, delta, median delta.
- Per-episode delta table (the evidence behind the means).
- Improved / regressed / unchanged counts per dimension.
- When either run has `samples > 1`: judge σ per dimension, and deltas
  smaller than ~2σ flagged "within judge noise" (descriptive, not a
  hypothesis test).
- `--json` emits the full structure for scripting.

### Phase 3 — the summary rubric

`Rubric(name="summary", version="1")`, inputs
`("summary", "clean_transcript")`. The clean transcript is the ground
truth the summary is judged against.

**LLM-judged dimensions** (0–10 each, with counts + examples):

- `coverage` — are the transcript's major topics represented?
- `faithfulness` — invented or distorted claims (count + examples),
  mirroring the cleanup rubric's `invented_content` check.
- `attribution` — claims/quotes credited to the right speaker.
- `insight_value` — does it surface the non-obvious (takeaways, tension)
  rather than paraphrase chronology?

**Deterministic checks** — computed in Python via the rubric's
`deterministic_checks` hook, *not* delegated to the judge, because they
are cheap, exact, and a judge would grade them noisily:

- Required sections present (Gist, Timeline, Key Takeaways, … per
  `TranscriptSummarizer`'s output contract in
  [core/post_processor.py](../thestill/core/post_processor.py)).
- Timeline timestamps parse, are monotonically increasing, and fall
  within episode duration (when duration is known).
- Inline `[mm:ss]` references fall within episode bounds.

They land in the item report under `"checks"`, separate from `"scores"`.

**Context limits:** summary + full clean transcript fit comfortably in
current judge context windows for typical episodes. If the combined input
exceeds the judge's window, the transcript is truncated to fit and the
item is marked `"transcript_truncated": true` in the manifest — degraded
evidence is labelled, never silent (FM-4). Chunked judging is future work.

### Failure-mode checklist (spec #42)

| FM | Where it bites here | Mitigation |
|---|---|---|
| FM-1 errors-as-empty-results | Judge/provider failure during batch | Per-item `status: failed` + classified error in manifest; non-zero exit; never skipped silently |
| FM-3 mixed-tz | `created_at`, run_id timestamps | UTC ISO-8601 `+00:00` everywhere |
| FM-4 silent degradation | Truncated transcript, unmatched compare items, partial runs | Explicit flags/counts in manifest and compare output |
| FM-5 consistent-mock tests | Runner tests | Mock provider fixtures include malformed JSON, control chars, schema-violating and score-out-of-range outputs — not just happy-path |
| FM-7 unsanitized LLM output | Judge JSON | `sanitize_text` → parse → Pydantic `report_model` before any write |

---

## Phases

### Phase 1 — Run infrastructure

`thestill/evals/` package (`models.py`, `rubrics.py`, `runner.py`),
registry entries for the two existing rubrics (prompts moved, version
`"1"`, hash test), `PathManager` run-dir methods, `EVAL_JUDGE_*` config +
`--judge-*` flags with the pinned/fallback distinction, `eval run` /
`eval list` / `eval show`, legacy commands deprecated to wrappers.

**Gate:** a batch run over ≥3 episodes produces a valid manifest with
denormalized scores; a forced judge failure on one item yields
`counts.failed == 1`, non-zero exit, and two intact item reports.

### Phase 2 — Compare and variance

`compare.py` + `eval compare` with classification banner, per-dimension
and per-episode deltas, unmatched-item accounting, `--json`; `--samples N`
with mean/std storage and noise flagging.

**Gate:** two runs over the same episodes with different labels compare
correctly in all four classification cases (artifact-identical vs
-different × judge-identical vs -different), verified by unit tests.

### Phase 3 — Summary rubric + golden set

`summary` rubric (LLM dimensions + deterministic checks),
`tests/fixtures/eval/golden_episodes.json` (a pinned, committed episode
list spanning ≥3 podcasts), docs page (`docs/evals.md`) covering the
run→compare workflow.

**Gate:** `eval run --rubric summary --episodes-file …/golden_episodes.json`
completes; deterministic checks catch a fixture summary with an
out-of-bounds timestamp and a missing section.

### Phase 4 (future, separate specs/chores)

Producer provenance captured at pipeline write time; briefing/narration
rubric post-#34; optional CI regression gate over the golden set; legacy
command removal.

---

## Testing

Per [#04 testing](04-testing.md): unit tests with a mocked `LLMProvider`
(varied fixtures per FM-5 — valid, malformed JSON, control-char-laden,
schema-violating, out-of-range scores); runner tests for partial failure,
interrupt (no manifest → invisible to `list`), and duplicate run_id;
registry test asserting prompt-hash/version consistency; compare tests for
the four classifications and unmatched items; CLI smoke tests via Click's
runner. No live LLM calls anywhere in CI.

---

## Open Questions

1. **Producer provenance.** Should pipeline stages start writing sidecar
   provenance (model, prompt version) next to each artifact, or embed
   frontmatter in the MD outputs? Sidecars avoid perturbing artifact
   hashes and downstream parsers; deferred to Phase 4 either way — the
   `--note` field is the interim.
2. **Retention.** Run dirs are small (KBs of JSON), so no pruning is
   planned; is `eval list --prune-failed` worth it later?
3. **Golden baselines in git.** Commit a blessed baseline *run* (not just
   the episode list) so `compare HEAD-run baseline` works after a fresh
   clone? Requires the golden episodes' artifacts to be stable/committed
   too — punted until Phase 3 experience says whether it's needed.

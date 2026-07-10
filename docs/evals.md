# Quality Evals

Thestill judges pipeline output quality with LLM-as-judge **eval runs**
(spec #53). A run is one rubric × one judge configuration × one set of
episodes, executed at one point in time. Runs are append-only: nothing is
ever overwritten, so "did this change make quality better or worse?" is
always answerable.

## Rubrics

| Rubric | Judges | Ground truth | Score dimensions |
|---|---|---|---|
| `raw-transcript` | ASR output quality | — | accuracy, completeness, entity_handling, structural_clarity |
| `clean-transcript` | LLM cleanup quality | raw transcript (when present) | fidelity, formatting_clarity, readability, enhancements_value |
| `summary` | episode summary quality | clean transcript | coverage, faithfulness, attribution, insight_value |

All scores are 0–10, judged by an LLM. The `summary` rubric additionally
runs **deterministic checks** in Python (required sections present,
timestamps parse and fall within episode duration, timeline segments
ascend) — exact validations a judge would grade noisily.

## Pinning the judge

Configure the judge independently of the pipeline LLM in `.env`:

```bash
EVAL_JUDGE_PROVIDER=anthropic
EVAL_JUDGE_MODEL=claude-sonnet-4-5-20250929   # dated snapshot, not an alias
EVAL_JUDGE_TEMPERATURE=0.0
```

Rules of thumb:

- **Pin a dated snapshot.** Floating aliases re-point server-side; the day
  that happens, every subsequent score silently means something different.
- **Cross the model family.** Judges favour their own family's output
  (self-preference bias). If the pipeline summarizes with OpenAI and
  cleans with Gemini, judge with Claude.
- **Changing the judge = re-baselining.** Deltas are only meaningful
  within a judge. After changing `EVAL_JUDGE_MODEL`, re-run the golden set
  once before trusting any comparison.

Without a pin, `eval run` falls back to the pipeline LLM, warns, and marks
the manifest `pinned: false`. Per-run overrides: `--judge-provider`,
`--judge-model`, `--judge-temperature`.

## Running evals

```bash
# Judge the golden set (pinned episodes spanning 5 podcasts)
thestill eval run --rubric summary \
    --episodes-file tests/fixtures/eval/golden_episodes.json \
    --label baseline --note "summarizer prompt v1, pipeline gemini-3-flash"

# Judge recent episodes of one podcast
thestill eval run --rubric clean-transcript --podcast-id 3 --max-episodes 5

# Variance visibility: judge each episode 3 times (mean ± std recorded)
thestill eval run --rubric summary --samples 3 ...

# Inspect
thestill eval list
thestill eval show <run-id>
```

Each run writes `data/evaluations/runs/<run-id>/` containing:

- `manifest.json` — judge (provider/model/temperature/pinned), rubric
  name/version/prompt sha256, git commit, per-episode artifact content
  hashes, per-item status and scores. This is the provenance that makes
  runs comparable.
- `items/<podcast>_<episode>.json` — the full judge report(s) per episode
  (examples, verdict prose) plus deterministic check details.
- `summary.json` — per-dimension mean/median/min/max.

A run with failed items still writes its manifest (failures are recorded
per-item, never silently skipped) and exits non-zero.

## Comparing runs

```bash
thestill eval compare <run-a> <run-b> [--json]
```

The first line of every comparison is its **classification** — what
actually differs — because that states what the deltas measure:

| Artifacts | Judge/prompt | Meaning |
|---|---|---|
| identical | differ | Judge comparison — grades the judges, not your pipeline |
| differ | identical | **Pipeline comparison** — the one you usually want |
| differ | differ | Confounded — deltas attribute to nothing; hold one variable fixed |
| identical | identical | Reproducibility check — shows judge variance only |

Below the banner: per-dimension mean deltas with improved/regressed/
unchanged counts, per-episode deltas, and explicit accounting of episodes
excluded from the join. When either run used `--samples`, deltas smaller
than ~2σ of judge noise are flagged.

## Typical workflow: scoring a prompt change

```bash
# 1. Baseline with the current prompt
thestill eval run --rubric clean-transcript \
    --episodes-file tests/fixtures/eval/golden_episodes.json --label before

# 2. Change the cleaning prompt/model, re-clean the golden episodes

# 3. Score the new artifacts with the SAME judge
thestill eval run --rubric clean-transcript \
    --episodes-file tests/fixtures/eval/golden_episodes.json --label after

# 4. Compare — should classify as PIPELINE COMPARISON
thestill eval compare <before-run-id> <after-run-id>
```

## Golden episode set

`tests/fixtures/eval/golden_episodes.json` pins 5 episodes across 5
podcasts (tech monologue, product, news, opinion essay, conversational
history), all short enough that summaries stay single-chunk. Don't rotate
members casually — longitudinal comparison assumes stable inputs.

## Deprecated commands

`thestill evaluate-raw-transcript` and `evaluate-clean-transcript` remain
as wrappers for one release: single-file standalone mode still works,
batch mode delegates to `eval run`. The old overwrite-in-place reports
under `data/evaluations/{raw,clean}/` are frozen legacy artifacts.

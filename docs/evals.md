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
| `entity-linking` | the live Wikidata linker, against the links already stored | stored `entity_mentions` (what ReFinED wrote) | agreement, no_regression, new_link_precision, recall_gain (derived — see below) |

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
    --label baseline --note "summarizer prompt v1, pipeline gemini-3-flash-preview"

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

## Eval backlog: host/role attribution

Conclusions from the 2026-07-28 host-misattribution incident (Elad Gil
shown as host of 13 Cheeky Pint episodes), written down for when a
role-attribution rubric gets built.

**Failure mode.** Podcast-level facts (`## Hosts`) are extracted once,
from whichever episode is cleaned first, and never revisited. An LLM
seeing a single transcript cannot distinguish a one-off guest co-host
from a permanent host — the prompt's "appears in EVERY or MOST episodes"
criterion is unanswerable from one sample. Three live instances found in
one audit: Elad Gil on Cheeky Pint (guest co-host on the first-cleaned
episode), John Collison on Dwarkesh Podcast (crossover episode), and
three non-hosts on Unsupervised Learning. The error then multiplies:
the UI reports podcast-level hosts against *every* episode of the
podcast, so one bad bullet reads as "host of 13 episodes".

**Signal quality (measured against live data, 2026-07-28).**

- `entity_mentions` speaking coverage is *unusable* as ground truth:
  legitimate hosts routinely score 0 (Neil deGrasse Tyson 0/31 on
  StarTalk, Bill Gurley 0/3 on BG2) because mention extraction only tags
  `speaking` when the transcript names the person.
- Episode facts `Name (Host)` speaker-mapping annotations are reliable:
  they corroborated every legitimate host checked (41/41 StarTalk, 8/8
  Tool Use, 7/51 for emeritus-style Reid Hoffman) and cleanly separated
  all three bad entries (1/8, 0/26, 0–1/12). Name variants ("Neil de
  Grasse Tyson") cost some recall but not enough to matter.

**Guardrail shipped** (`role_linker.collect_host_evidence`): at link
time, demote a podcast-level host candidate when ≥5 episode facts files
carry host annotations and the candidate appears in <2 — never emptying
the list. Re-runs after every episode clean, so it self-heals as
evidence accumulates.

**Eval ideas.**

- LLM-judge rubric: given a podcast's episode facts speaker mappings,
  score the podcast facts `## Hosts` list for precision/recall.
  Ground truth is cheap: the aggregated `(Host)` annotation counts.
- Regression metric worth tracking: hosts listed at podcast level with
  zero corroborating episode facts appearances (should stay at 0).
- The initial-extraction prompt should be evaluated on
  first-episode-is-atypical inputs (crossover episodes, guest
  co-hosts, panel episodes) — that's exactly where it fails.

## Deprecated commands

`thestill evaluate-raw-transcript` and `evaluate-clean-transcript` remain
as wrappers for one release: single-file standalone mode still works,
batch mode delegates to `eval run`. The old overwrite-in-place reports
under `data/evaluations/{raw,clean}/` are frozen legacy artifacts.

## The `entity-linking` rubric (spec #81)

This rubric is the gate for switching `ENTITY_LINKER` from `refined` to
`live`. It is pairwise, not a score sheet:

1. for each episode, the live linker decides the same names the pipeline's
   linker already decided (`direct`, `llm_linked` and `unresolvable`
   mentions; anchors, coreference and overrides are left out);
2. names where both answers match cost nothing;
3. every disagreement goes to the judge, which sees two answers labelled A
   and B and is never told which linker gave which. The side is fixed per
   name, so a rerun asks the same question.

The baseline is what is **stored**, not a fresh ReFinED run, so the eval host
needs no `entities` extra. The live linker runs with no memory: it reads no
cached decision and writes nothing, to the cache or to `entity_mentions`.

```bash
thestill eval run --rubric entity-linking \
    --episodes-file tests/fixtures/eval/entity_linking_episodes.json --label p1
```

The pinned set holds production slugs, so point the command at the
production database. A run makes real Wikidata requests (paced by
`WIKIDATA_MAX_RPS`) and real LLM calls: roughly 2–3 linker calls and one
judge call per episode.

The four dimensions are 0–10 views of ratios, for `eval list/show/compare`.
A dimension with nothing to measure in an episode is left out, not scored.
**The decision is made on `totals.json`**, which sums the counts across
episodes — a mean of per-episode ratios would let a three-name episode
outvote a ninety-name one:

| Criterion | Meaning | Pass |
|---|---|---|
| `regression_rate` | of the names the baseline linked, those where the judge found the baseline right and the live linker wrong | under 2% |
| `recall_gain` | of the names the baseline left unlinked, those the live linker linked correctly | at least 25% |
| `new_link_precision` | of the live linker's links on names the baseline left unlinked, those judged right ("unclear" left out) | at least 90% |
| no blacklisted links | no link a reviewer has ruled out was accepted | zero |
| unanswered | names the linker could not answer, over the whole run (a transient timeout is the pipeline's retry to absorb, not a quality finding) | at most 5% |

Each item report lists every name with both answers, the outcome, the
verdict and the judge's reason, plus `baseline_split`: how often ReFinED gave
one name two answers within an episode, which is the evidence for or against
the live linker's one-answer-per-name simplification.

Prefer a judge from a different model family than `ENTITY_LINKING_MODEL`: a
model grading its own choices is the self-preference bias the judge pin
exists to avoid.

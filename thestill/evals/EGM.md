# thestill.evals

## Overview

LLM-as-judge quality evaluation (spec #53), which backs `thestill eval`. A
**run** is one rubric with one judge configuration over one set of
episodes. Each run is written once, under
`data/evaluations/runs/<run_id>/`, and never overwritten.

The package also holds three specialised evaluations:

- The entity-linking cutover gate (spec #81).
- The Resource List probe (spec #92).
- The corpus search golden-set evaluation (spec #89).

`data/search_golden.json` is the golden query set. It is a data file with no
contract of its own.

## Interface

- `rubrics.py:RUBRICS` and `rubrics.py:get_rubric(name)` hold the rubric
  registry:

  | Rubric | Score dimensions | Inputs |
  | --- | --- | --- |
  | `raw-transcript` | `accuracy`, `completeness`, `entity_handling`, `structural_clarity` | `raw_transcript` |
  | `clean-transcript` | `fidelity`, `formatting_clarity`, `readability`, `enhancements_value` | `clean_transcript`, with `raw_transcript` optional |
  | `summary` | `coverage`, `faithfulness`, `attribution`, `insight_value` | `summary`, `clean_transcript` |
  | `entity-linking` | `agreement`, `no_regression`, `new_link_precision`, `recall_gain` | Database evidence, no files |

- `rubrics.py:Rubric`, a frozen dataclass whose `prompt_sha256` property
  hashes the system prompt. The artifact kinds are
  `rubrics.py:RAW_TRANSCRIPT`, `CLEAN_TRANSCRIPT`, and `SUMMARY`. The
  entity-linking judge reports are `rubrics.py:Verdict` and
  `rubrics.py:LinkingJudgeReport`.
- `runner.py:resolve_judge(config, cli_provider, cli_model, cli_temperature)
  -> JudgeResolution` and `runner.py:JudgeResolution`, which holds the
  `provider` and `info`.
- `runner.py:EvalRunner(config, path_manager, feed_manager,
  supplementary=None)`. Its methods are `discover(...)`,
  `evaluate_texts(...)`, `run(...) -> RunManifest`, and the static method
  `build_run_id`.
- `runner.py:summarize_run`, `load_manifest`, `list_manifests`, and
  `EvalError`.
- `runner.py:MAX_INPUT_CHARS` (600,000), `MANIFEST_FILENAME`,
  `SUMMARY_FILENAME`, and `ITEMS_DIRNAME`.
- `models.py:RunManifest`, `ManifestItem`, `ArtifactRef`, `JudgeInfo`,
  `RubricInfo`, `RunSummary`, and `DimensionStats`. The schema versions are
  `MANIFEST_SCHEMA_VERSION` and `ITEM_REPORT_SCHEMA_VERSION`, both 1.
- `models.py:RawTranscriptReport`, `CleanTranscriptReport`, and
  `SummaryReport`: the judge-report validation schemas. Each score is in the
  range 0–10.
- `compare.py:compare_runs(manifest_a, manifest_b) -> RunComparison`.
  `Classification` is `judge`, `pipeline`, `confounded`, or
  `reproducibility`. Related symbols are `DimensionDelta`, `EpisodeDelta`,
  and `NOISE_SIGMA_FACTOR` (2.0).
- `summary_checks.py:run_summary_checks(artifacts, duration_seconds)`.
  `REQUIRED_SECTIONS` lists the 9 numbered sections, The Gist through BS.
  `DURATION_TOLERANCE_SECONDS` is 30.0.
- `resource_list_checks.py:ResourceListProbe(source, entity_repository=None)`,
  which is callable as `(episode, artifact_texts) -> dict`, and
  `resource_list_checks.py:resource_list_metrics`.
- `entity_linking.py:LinkingEvalRunner`, an `EvalRunner` subclass.
  `entity_linking.py:gate(totals, no_blacklisted_links)` applies the cutover
  criteria, and `entity_linking.py:no_memory()` returns an empty decision
  repository.
- `entity_linking.py` also provides `compare_names`, `baseline_answer`,
  `metrics`, `derived_scores`, and `live_is_side_a`.
- `entity_linking.py` constants: `TOTALS_FILENAME` (`totals.json`),
  `JUDGE_BATCH_SIZE` (30), `MAX_REGRESSION_RATE` (0.02), `MIN_RECALL_GAIN`
  (0.25), `MIN_NEW_LINK_PRECISION` (0.90), and `MAX_UNANSWERED_RATE` (0.05).
- `search_quality.py:run_golden(search, queries, label=..., k=10, judge=None,
  cache=None) -> Run`, `score_runs`, `latency_summary`, `per_query_table`,
  `load_golden`, and `judge_hits`.
- `search_quality.py:JudgmentCache`, `GoldenQuery`, `Hit`, `QueryResult`,
  `Run`, and `KindScore`. `GOLDEN_PATH` is `data/search_golden.json`, and
  `PASSAGE_CHARS` is 600.

## Invariants

- Runs are append-only:
  - `EvalRunner.run` raises `EvalError` if the run directory already exists.
  - The run id is `<UTC %Y%m%d-%H%M%S>-<rubric>[-<label slug>]`.
  - The manifest is written last, atomically, by writing a temp file and
    renaming it.
  - A killed run leaves item files but no manifest, and `list_manifests`
    ignores it.

  [`runner.py:EvalRunner.run`, `runner.py:_atomic_write_json`,
  `runner.py:list_manifests`]
- One item's failure is recorded as `status: "failed"` with its error, and
  the run continues [`runner.py:EvalRunner._run_item`].
- Judge output is sanitized, parsed with `json.loads`, and validated with
  `report_model.model_validate`, with one retry. Two invalid reports raise
  `EvalError`, and an invalid report is never stored as a success
  [`runner.py:EvalRunner._judge_once`].
- Combined input is capped at `MAX_INPUT_CHARS`, split evenly across
  artifacts. Any truncation sets `transcript_truncated`
  [`runner.py:EvalRunner.evaluate_texts`].
- A judge is pinned only when its provider or model came from a CLI flag or
  from `EVAL_JUDGE_*`. Otherwise it falls back to the pipeline LLM with
  `pinned=False`. An `EVAL_JUDGE_MODEL` applies only to its own provider
  family [`runner.py:resolve_judge`].
- `samples` must be at least 1, and the entity-linking rubric requires
  exactly 1 [`runner.py:EvalRunner.run`,
  `entity_linking.py:LinkingEvalRunner.run`].
- Supplementary checks such as `ResourceListProbe` never affect `checks_ok`,
  and a failure in one is recorded rather than failing the item
  [`runner.py:_run_supplementary`].
- `compare_runs` refuses runs of different rubrics. It joins only `ok` items
  by `(podcast_slug, episode_slug)` and classifies the comparison by whether
  the judge, the prompt hash, or the artifact SHA-256 hashes differ
  [`compare.py:compare_runs`, `compare.py:_classify`].
- Editing a rubric prompt changes `prompt_sha256`, and a unit test pins the
  hash per name and version [`rubrics.py` module docstring,
  `rubrics.py:Rubric.prompt_sha256`].
- Discovery in a pinned episode set never truncates silently: a missing
  episode logs `eval_pinned_episode_unavailable`
  [`runner.py:EvalRunner.discover`].
- The entity-linking evaluation reads and stores no linker memory
  (`_NoMemory`). The live answer's A/B side is fixed per name by a hash, so
  the judge is blind to which side is new. The gate applies to corpus-level
  totals [`entity_linking.py:_NoMemory`, `live_is_side_a`, `gate`].
- In search evaluation, grades are 0, 1, or 2, and the judge must grade
  every passage. nDCG is computed against the pooled judgments of all
  compared runs. The warm-up query does not count toward latency
  [`search_quality.py:_parse_grades`, `score_runs`, `run_golden`].

## Side Effects

- Calls the judge LLM provider with
  `response_format={"type": "json_object"}` [`runner.py:EvalRunner._chat`,
  `search_quality.py:judge_hits`].
- Writes files under `PathManager.evaluation_run_dir(run_id)`:
  `items/<podcast>_<episode>.json`, `summary.json`, `manifest.json`, and,
  for entity linking, `totals.json` [`runner.py:EvalRunner.run`,
  `entity_linking.py:LinkingEvalRunner.run`].
- Reads transcript and summary artifacts from disk and hashes them with
  SHA-256 [`runner.py:EvalRunner._run_item`].
- Runs `git rev-parse --short HEAD` as a subprocess to record the commit
  [`runner.py:_git_commit`].
- Entity linking reads the stored `entity_mentions` through the entity
  repository and calls the live Wikidata linker (`LiveWikidataLinker.link`)
  [`entity_linking.py:LinkingEvalRunner._run_item`].
- `search_quality.py:JudgmentCache.put_many` appends to a JSONL grade cache,
  and `load_golden` reads `data/search_golden.json`.
- Binds the structlog contextvars `run_id` and `episode_id`. Log events
  include `eval_run_completed`, `eval_item_failed`,
  `eval_judge_report_invalid`, `eval_judge_control_chars_stripped`,
  `eval_supplementary_check_failed`, `search_eval_judged`, and
  `search_eval_judge_invalid`.

## Verification

- Run: `./venv/bin/pytest tests/unit/evals`

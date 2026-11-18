# scripts: one-off, diagnostic and operational scripts

## Overview

Standalone Python scripts that sit outside the `thestill` CLI, the web server and the task
queue. The pipeline does not import any of them. They fall into six groups: data backfills,
read-only audits and diagnostics, prototypes and A/B harnesses, market-data builders, an SEO
check, and a check of a live MCP connector. Most of them reuse `thestill` repositories,
processors and `PathManager`. Several connect straight to SQLite (`data/podcasts.db`). Prod
now runs on Postgres; of these scripts, only `scripts/backfill_description_fields.py` and
`scripts/narration_ab.py` (through `make_repositories(config)`, `narration_ab.py:174`) can
talk to Postgres, both when `DATABASE_URL` is set.

Run them from the repo root with `./venv/bin/python scripts/<name>.py`. Many default paths
are relative to the current directory, for example `data/podcasts.db` and `data/clean_transcripts`.

Note: CLAUDE.md says to fix entity and transcript data through the audited CLI and admin
services, never with raw SQL. The backfill scripts here are the earlier one-off way of doing
that. Read a script's dry-run output before you pass `--apply`.

## Interface

### Backfills (each defaults to a dry run; writes only with `--apply`)

| Script | Flags | Entry points |
| --- | --- | --- |
| `scripts/backfill_control_chars.py` | `--apply`, `--db` (default `data/podcasts.db`) | `backfill_db`, `backfill_files`, `DB_TARGETS`, `FILE_ROOTS` |
| `scripts/backfill_description_fields.py` | `--apply`, `--db` (used when `DATABASE_URL` is unset) | `needs_repair`, `compute_fixes`, `write_backup`, `run` |
| `scripts/backfill_feed_holes.py` | `--apply`, `--podcast-id`, `--db` | `main`, `_find_holes` |
| `scripts/backfill_segmented_transcripts.py` | `--apply`, `--episode-id` (repeatable; default `DEFAULT_EPISODE_IDS`), `--force`, `--no-enqueue` | `main`, `_find_targets`, `_derive_paths` |

### Read-only audits and diagnostics

| Script | Flags | Entry points and exit codes |
| --- | --- | --- |
| `scripts/audit_segment_coverage.py` | `--storage-path` (default `./data`), `--database-path`, `--json` | `audit()` returns a `CoverageReport`. Exits 2 when `CoverageReport.gate_passes` fails, i.e. speaker gap above `SPEAKER_COVERAGE_FAIL_THRESHOLD` (0.30). |
| `scripts/check_feed_discrepancies.py` | `--limit` (default 10), `--workers` (default 8) | `main`, `_check_one` |
| `scripts/verify_speakers.py` | `--last` (default 20), `--storage-path` | `analyse_episode`, `render_report`. Exits 1 if a `SPEAKER_NN` label survives or blended speakers are missing from the segmented JSON. |
| `scripts/prototype_entity_review.py` | none (`DB='data/podcasts.db'`, `TOP_N=30`) | `main` |
| `scripts/diag_gemini_clean_218.py` | none (one Croatian episode hardcoded) | `install_diag` wraps `GeminiProvider.generate_structured`. |

### Prototypes and A/B harnesses

| Script | Flags | Entry points |
| --- | --- | --- |
| `scripts/cleaning_prototype.py` | none (`TEST_EPISODES` lists 13 episodes) | `approach_b_corrections`, `approach_c_hybrid`, `approach_d_current_pipeline`, `apply_corrections_fuzzy`, `CleaningResult` |
| `scripts/compare_cleanup.py` | `--last` (10), `--episode-id`, `--gap-candidates`, `--max-scan` (200), `--skip-segmented-done`, `--dry-run`, `--analyse-only`, `--storage-path`, `--save-json`, `--concurrency` (1) | `discover_recent_episodes`, `discover_episodes_by_id`, `discover_gap_candidates`, `snapshot_baseline`, `run_dual_cleanup`, `compute_metrics`, `render_report`, `EpisodeTarget` |
| `scripts/narration_ab.py` | `--briefing` (required), `--voices` (default `conversational_anchor conversational_v2`), `--runs` (3), `--target` (300 s), `--out` (default `data/narration_ab`), `--no-judge` | `main`, `GATE`, `ROWS` |

### Market data and SEO

| Script | Flags | Entry points |
| --- | --- | --- |
| `scripts/build_top_podcasts.py` | `--region` (default `us`), `--regions` (comma-separated batch) | `run_batch`, `enrich_and_order`, `write_region_files`, `TARGET_COUNT` (500), `CSV_FIELDS` |
| `scripts/survey_alternate_enclosures.py` | `--baseline` (default `data/alternate_enclosure_survey.json`), `--out` | `survey_feed`, `run_survey`, `diff`, `is_video_mime` |
| `scripts/body_word_count.py` | `urls...`, `--ua` (`bingbot`, `chrome` or `googlebot`), `--min-words`, `--text` | `body_text`. Exits 1 if a page has fewer than `--min-words` words. |

### Connector check

| Script | Flags | Entry points and exit codes |
| --- | --- | --- |
| `scripts/check_connector.py` | env `THESTILL_MCP_URL` (required), `--query` (default `AI agents`), `--entity` (default `Nvidia`), `--write`, `--follow-url`, `--import-url` | `main`, `run_checks`, `check_annotations`, `redactor`, `Report`, `Options`, `OPERATOR_TOOLS`, `WRITE_TOOLS`. Exits 2 when `THESTILL_MCP_URL` is unset, 1 on any failed tool or when the connector cannot be reached, 0 otherwise. |

### Other code that imports these scripts

- `scripts/verify_speakers.py` imports `from scripts.compare_cleanup import EpisodeTarget,
  discover_recent_episodes`.
- `evaluation/run.py:step_clean_b` imports `cleaning_prototype.approach_b_corrections` (see
  [../evaluation/EGM.md](../evaluation/EGM.md)).
- `tests/unit/mcp/test_check_connector.py` loads `scripts/check_connector.py` with
  `importlib` and runs `run_checks` against an in-memory MCP server.

## Invariants

- Backfill guards:
  - `scripts/backfill_feed_holes.py` creates the repository only when `--apply` is given.
  - `scripts/backfill_segmented_transcripts.py` returns early in a dry run, before it creates
    the LLM provider or the queue.
- Safety and idempotency guards in the backfill scripts:
  - `scripts/backfill_control_chars.py` and `scripts/backfill_description_fields.py`:
    the docstrings say both are idempotent; they change only rows and files that still need
    fixing.
  - `scripts/backfill_description_fields.py:write_backup` writes the original values to a
    JSON backup before any UPDATE runs.
  - `scripts/backfill_control_chars.py:backfill_db` calls the best-effort
    `maybe_load_vec_extension` (`backfill_control_chars.py:71`), because the `chunks_au`
    trigger writes into the vec0 tables. Without sqlite-vec it returns `False` silently
    (`thestill/utils/sqlite_ext.py:65-85`), and the UPDATE that fires the trigger then fails.
  - `scripts/backfill_feed_holes.py`: `_load_podcasts` selects only non-synthetic podcasts
    (`backfill_feed_holes.py:59`); `main` skips YouTube feeds (`:115`) and podcasts with no
    tracked episodes; `_find_holes` fetches the full feed and keeps only entries dated on or
    after the oldest tracked `pub_date`; `main` inserts them with `repo.save_episodes` (only
    with `--apply`). It never changes `podcasts.last_processed`.
  - `scripts/backfill_segmented_transcripts.py:main`:
    - without `--force`, it skips episodes that already have `clean_transcript_json_path`;
    - it skips the enqueue when `has_pending_task(..., TaskStage.EXTRACT_ENTITIES)` is true;
    - an unknown `--episode-id` ends the run with an error (raised in `_find_targets`).
- Read-only scripts: these never write to the database:
  - `scripts/audit_segment_coverage.py`
  - `scripts/check_feed_discrepancies.py` (its docstring says so)
  - `scripts/verify_speakers.py`
  - `scripts/prototype_entity_review.py` (SELECT only)
- `scripts/diag_gemini_clean_218.py:main` passes `save_prompts=False` and no `output_path`, so
  it writes no debug artifacts. It can still write facts files: with a `path_manager`,
  `TranscriptCleaningProcessor.clean_transcript` saves missing episode and podcast facts
  (`save_episode_facts`, `transcript_cleaning_processor.py:269`; `save_podcast_facts`,
  `:285`). It returns 1 if the configured provider is not Gemini.
- `scripts/check_connector.py:main`:
  - It reads the connector URL only from `THESTILL_MCP_URL`, never from the command line,
    and redacts the URL and its token from every reported detail and log record
    (`redactor`, `_RedactingFilter`); a connection error prints only the exception type.
  - `check_annotations` fails `tools/list` unless every listed tool has a title and both
    `read_only_hint` and `destructive_hint`.
  - It never calls `OPERATOR_TOOLS`. Without `--write` it skips `WRITE_TOOLS`;
    `--follow-url` and `--import-url` are refused without `--write`.
  - A listed tool the script neither calls nor skips is reported as `FAIL`.
- `scripts/compare_cleanup.py:main`:
  - It picks targets by the first of `--episode-id`, `--gap-candidates` or `--last` that is
    given.
  - `--dry-run` lists the targets and exits before any write or LLM call.
  - Before any cleanup run, it takes a one-time baseline copy (`snapshot_baseline`). The copy
    is never overwritten.
  - DB writes are serialised behind `_DB_WRITE_LOCK`.
- `scripts/narration_ab.py:main`:
  - It runs the judge only when `content.mode == 'narrated'`.
  - If `resolve_judge` fails, it falls back to the pipeline provider.
  - It calls `NarrationGenerator.generate` only, so it never writes to the narrations folder.
- `scripts/build_top_podcasts.py:write_region_files` writes rows in final rank order, because
  the seeder reads rank from row position. `scripts/build_top_podcasts.py:main` returns 1 if
  `thestill/utils/podcast_categories.json` is missing.
- `scripts/survey_alternate_enclosures.py:survey_feed` records fetch errors and never raises.
  `run_survey` sorts its output by `source_rank`.

## Side Effects

- Database writes. Each of these needs `--apply`, except `compare_cleanup.py`, which writes
  unless `--dry-run` or `--analyse-only` is given:
  - `scripts/backfill_control_chars.py:_backfill_db_rows` UPDATEs `chunks.text`,
    `entity_mentions.quote_excerpt`, `entity_mentions.surface_text` and
    `entities.canonical_name` in SQLite.
  - `scripts/backfill_description_fields.py:run` UPDATEs `episodes.description` and
    `episodes.description_html`. It targets Postgres when `DATABASE_URL` is set and SQLite
    otherwise.
  - `scripts/backfill_feed_holes.py:main` inserts into `episodes` through
    `SqlitePodcastRepository.save_episodes`.
  - `scripts/backfill_segmented_transcripts.py:main`:
    - updates the episode's transcript paths through `PodcastFeedManager.mark_episode_processed`;
    - inserts an `EXTRACT_ENTITIES` row into `tasks` through `QueueManager.add_task`.
  - `scripts/compare_cleanup.py:run_dual_cleanup` runs
    `UPDATE episodes SET clean_transcript_json_path`.
- File writes:
  - `scripts/backfill_control_chars.py:backfill_files`, with `--apply`, rewrites `.md` and
    `.json` files in place under `data/clean_transcripts`, `summaries`, `episode_facts`,
    `podcast_facts` and `digests`.
  - `scripts/backfill_description_fields.py:write_backup` writes
    `data/backfill_description_fields_backup_<stamp>.json`.
  - `scripts/backfill_segmented_transcripts.py:main` writes `<clean_transcripts>/<podcast>/*_cleaned.md`
    and the `.json` sidecar.
  - `scripts/compare_cleanup.py`:
    - `run_dual_cleanup` overwrites the production `*_cleaned.md` and writes its `.json`
      sidecar;
    - `snapshot_baseline` writes `*_cleaned.baseline.md`;
    - `--save-json` writes the given path.
  - `scripts/cleaning_prototype.py:main` writes `data/cleaning_prototype_output/<label>__<approach>.md`.
  - `scripts/narration_ab.py:main` writes `<out>/<briefing8>-<voice>-<run>.{md,json}`.
  - `scripts/build_top_podcasts.py:write_region_files` writes
    `data/top_podcasts_<region>.{json,csv}`.
  - `scripts/survey_alternate_enclosures.py:main` writes the `--out` JSON.
  - `scripts/diag_gemini_clean_218.py:main` can write missing episode and podcast facts
    files through `TranscriptCleaningProcessor.clean_transcript`.
- LLM calls, each through `create_llm_provider_from_config`:
  - `scripts/backfill_segmented_transcripts.py` (with `--apply`) and
    `scripts/compare_cleanup.py:run_dual_cleanup`, through `TranscriptCleaningProcessor.clean_transcript`;
  - `scripts/cleaning_prototype.py`;
  - `scripts/narration_ab.py`: `NarrationGenerator`, plus the judge in `_judge`;
  - `scripts/diag_gemini_clean_218.py` (Gemini only).
- Network calls:
  - RSS fetches:
    - `scripts/backfill_feed_holes.py:_find_holes` uses `RSSMediaSource.fetch_episodes`, even
      in a dry run.
    - `scripts/check_feed_discrepancies.py:_check_one` uses `guarded_get` (SSRF-guarded).
    - `scripts/survey_alternate_enclosures.py:survey_feed` uses `requests` with 24 threads.
  - `scripts/build_top_podcasts.py` calls Apple top-chart RSS and iTunes Lookup. It also runs
    yt-dlp `ytsearch5` (metadata only) and fetches feeds with feedparser.
  - `scripts/body_word_count.py:main` sends HTTP GET requests to the given URLs.
  - `scripts/check_connector.py:run_checks` connects to the remote MCP connector as an MCP
    client (`mcp.Client`) and calls its read tools. With `--write` it also changes the
    URL owner's account: `send_to_inbox` adds an episode to the inbox (it stays there),
    `--follow-url` follows a show and unfollows it again (`add_podcast`, `remove_podcast`),
    and `--import-url` imports an episode, which runs the pipeline.
- Database reads only:
  - `scripts/audit_segment_coverage.py:audit` uses `SqlitePodcastRepository.get_all_episodes`.
  - `scripts/check_feed_discrepancies.py:main` reads `podcasts` and runs `MIN(pub_date)` on
    `episodes`.
  - `scripts/verify_speakers.py`, through `discover_recent_episodes`.
  - `scripts/prototype_entity_review.py:main` reads `entities` and `entity_mentions`.
  - `scripts/narration_ab.py:main` reads `user_briefings`, `user_episode_inbox` and
    `episodes` through `make_repositories`.

## Known Drift

- `scripts/cleaning_prototype.py` imports `thestill.core.transcript_cleaner`, which was deleted
  in commit `8f398689`. The script fails on import, and so does `evaluation/run.py:step_clean_b`,
  which imports it.
- `scripts/compare_cleanup.py:shadow_legacy_path` calls `PathManager.clean_transcript_shadow_file`,
  which does not exist in `thestill/utils/path_manager.py`. Nothing in `thestill/` reads the
  `THESTILL_CLEANUP_PIPELINE` or `THESTILL_LEGACY_CLEANUP_SHADOW` env vars this script sets.
  The legacy-shadow comparison therefore no longer works. A run without `--dry-run` or
  `--analyse-only` still overwrites production cleaned transcripts.
- The docstring of `scripts/backfill_control_chars.py` lists two DB columns, but `DB_TARGETS`
  covers four.
- The docstring of `scripts/build_top_podcasts.py` names `data/top_podcasts.{json,csv}`, but
  the files it writes are per region.
- `scripts/diag_gemini_clean_218.py:EPISODE_ID` is defined but never used.

## Verification

- `make test` (`./venv/bin/pytest`) runs only one script: `tests/unit/mcp/test_check_connector.py`
  covers `scripts/check_connector.py` against an in-memory server. It runs nothing else in
  `scripts/`; it tests the `thestill` modules the other scripts depend on.
- To check a script safely, run its `--help` or a default dry run against a copy of the
  database. Never run `--apply` or the LLM, network or feed scripts as a test.

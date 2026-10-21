# Codebase Cleanup: Dead Code, Inert Flags, One-Off Tools and Duplication

> **Status:** 🚧 Phases 0, 1, 3a and 3c done (2026-10-21, released in v1.29.1); Phases 2, 3b, 4–7 open
> **Created:** 2026-10-21
> **Updated:** 2026-10-21
> **Priority:** Medium. One live prod bug (Phase 1) comes out of this audit. The rest is cost of carrying: about 3,500 lines of unused production code, flags that do nothing, and SQLite/Postgres twins that have already shipped three parity bugs.
> **Author:** Product & Engineering
> **Related:** [#42 robustness-and-failure-mode-hardening](42-robustness-and-failure-mode-hardening.md) (failure-mode catalogue: silent degradation, path drift), [#44 postgres](44-postgres-migration.md) (SQLite → Postgres cutover tools), [#53 eval-runs-and-summary-rubric](53-eval-runs-and-summary-rubric.md) (deprecated eval commands), [#64 legacy-account-claim](64-legacy-account-claim.md), [#81 live-wikidata-entity-linking](81-live-wikidata-entity-linking.md) (ReFinED removal), [#86 quality-feedback-loop](86-quality-feedback-loop.md) (ordering of #81 Phase 4), [#89 search-quality](89-search-quality.md) (parked reranker), [#92 resource-list-entities](92-resource-list-entities.md), [#99 top-chart-movement-and-topics](99-top-chart-movement-and-topics.md) (category seeder), [#104 abuse-prevention-and-user-admin](104-abuse-prevention-and-user-admin.md) (runtime settings).

---

## Executive summary

Months of feature work left behind temporary flags, one-time repair and
migration commands, methods nobody calls, and logic copied between the
SQLite and Postgres backends. An audit on 2026-10-20 found:

- **One live bug.** DLQ "Retry all" returns a 500 on Postgres after the
  requeue has already succeeded (Phase 1).
- **Dead code.** About 3,500 lines of production code with no callers, or
  with callers only in their own tests.
- **Inert configuration.** Flags that are read but change nothing, and
  env vars read in two places with different fallbacks.
- **One-off tools.** Repair, backfill and cutover commands whose job is
  done on prod, but whose retirement depends on an upgrade policy for
  self-hosted installs.
- **Duplication with drift.** Output paths built in three places that
  disagree on file names; the ElevenLabs webhook re-implementing the
  transcriber and leaking pending-operation rows; SQLite and Postgres
  repositories with about 4,900 matched lines and 34 public methods not
  declared on the shared interfaces.

This spec turns that audit into a sequence of small, independent PRs. The
order is: fix the bugs, delete what is provably dead, remove inert
configuration, then consolidate. Retiring operational tools comes last
and only behind explicit conditions.

## Rules for every phase

1. **Work from `origin/main`.** The first audit ran partly on a checkout
   25 commits behind and produced wrong findings (for example, frontend
   error parsing already has a shared `apiErrorMessage` helper). Each PR
   regenerates its evidence on a fresh branch.
2. **Evidence per deletion.** Every removed symbol is listed in the PR
   description with the grep that shows it unused across `thestill/`,
   `tests/`, `scripts/`, `docs/`, `deploy/` and the frontend. No "and
   others" buckets.
3. **Test-only is not live.** A symbol whose only callers are its own
   tests is deleted together with those tests. A test fake used by other
   tests stays.
4. **"No frontend caller" is not "unused".** Endpoints are documented
   interfaces. They are removed only with prod usage evidence (Phase 0,
   [A.8](#a8-endpoints-no-durable-access-history)) and a docs update.
5. **Completed on prod is not completed everywhere.** One-time repairs
   and migrations stay until the upgrade policy (Decision 1) says old
   databases need not upgrade through them.
6. **One concern per PR.** Bug fixes never ride along with deletions.
7. **Shared-tree hygiene.** Each PR lives on its own branch and worktree.
   Format only edited files (the repo is not black-clean). Never
   `npm install` on macOS (lockfile).

## Decisions needed

These block only Phase 7. Phases 0–6 can proceed without them.

| # | Question | Options | Blocks |
|---|---|---|---|
| D1 | **Upgrade policy.** Must an old self-hosted SQLite database, or a restored pre-cutover backup, still upgrade cleanly? | (a) Yes: repair commands and the SQLite migration ladder stay. (b) Only from a named baseline release; older databases are refused with a clear message. | Phase 7a, SQLite migration squash |
| D2 | **ReFinED ordering.** [#86](86-quality-feedback-loop.md) places #81 Phase 4 (sweep, alias hygiene, ReFinED removal) after #86 Phase 1, which is unimplemented. | (a) Keep the ordering; ReFinED and its 8.6 GB cache stay until #86 Phase 1 ships. (b) Remove ReFinED once the relink sweep is verified; keep #86's ordering for alias hygiene only. | Phase 7c |
| D3 | **Parked flags.** | Search reranker and entity leg (#89): run the latency check and enable, or delete. `ENTITY_RESOURCE_SEEDS_ENABLED`: default to on (prod is on) and later remove. `NARRATION_ENABLED`: flip the default, or document the env var as the seed of the #104 runtime setting. `ENABLE_DIARIZATION`: default to true when `TRANSCRIPTION_PROVIDER=dalston`. | Phase 7d |
| D4 | **SQLite support level.** | (a) Full parity (status quo). (b) New features may be Postgres-only behind `Unavailable*` stubs, as analytics already is. | Nothing in this spec; recorded for later |

## Phase 0: Verified inventory

No production code changes, except one comment.

- Reword the comment at `core/task_handlers.py` (around line 371) that
  begins `# type: text/html`. Vulture parses with `type_comments=True`,
  reads it as a PEP 484 type comment, and skips the whole file. That
  produced about 20 false "unused" results.
- Re-run on `origin/main`:
  - `uvx vulture thestill --min-confidence 60`
  - `npx --yes knip` and `npx --yes knip --production` in the frontend
  - `uvx pylint --disable=all --enable=duplicate-code --min-similarity-lines=12 thestill`
- Produce the deletion list for Phases 3 and 4 as a checklist (file,
  symbol, evidence, test files touched). Commit it as an appendix to this
  spec.
- Gather prod usage evidence for these endpoints:
  - `GET /api/podcasts/{slug}/followers/count`
  - `GET /api/inbox/unread-count`
  - `GET /api/commands/status`
  - `GET /api/commands/task/{id}` and `GET /api/commands/progress/current`
  - `GET /api/episodes/failed` and `GET /api/episodes/{id}/failure`

  The plan was 30-day hit counts from CloudWatch. Prod does not ship
  logs there, so [A.8](#a8-endpoints-no-durable-access-history) replaces
  it with a capture of the retained container logs.

**Done when:** the appendix exists and every Phase 3–4 item in it carries
evidence. Done 2026-10-21: [Appendix A](#appendix-a-phase-0-inventory).

## Phase 1: DLQ "Retry all" on Postgres

**Bug.** `POST /api/commands/dlq/retry-all` (in
`web/routes/api_commands.py`) requeues dead tasks, then calls
`repository.clear_episode_failures(episode_ids)`. Only
`SqlitePodcastRepository` implements that method, and it is not on the
`EpisodeRepository` interface. On Postgres the call raises
`AttributeError`. The surrounding `except` only catches
`sqlite3.OperationalError`, so the admin gets a 500 for a retry that
worked, and the failure banners stay.

**Change.**

- Declare `clear_episode_failures(episode_ids) -> int` on the episode
  repository interface.
- Implement it in the Postgres episodes mixin (one `UPDATE … WHERE id =
  ANY(%s)`).
- Make the cosmetic-clear `except` backend-neutral. It should log and
  continue for lock or transient errors on either backend, not only
  `sqlite3.OperationalError`.

**Tests.** A dual-backend test (runs on Postgres in CI through
`TEST_DATABASE_URL`) asserting that retry-all on a dead episode task:

- returns 200,
- requeues the task,
- clears the episode's failure fields.

**Recovery in prod until shipped.** Retries do land. Failure banners
self-clear on the next successful stage via
`clear_episode_failure_for_stages`.

## Phase 2: ElevenLabs webhook pending-operation cleanup

**Bug.** `WebhookTranscriptProcessor._cleanup_pending_operation` deletes
`pending_operations/elevenlabs_<id>.json`. Pending operations now live in
the database (`pending_ops_repository`), so the row is never removed.

**Trap.** `ElevenLabsTranscriber` documents that the webhook's
`request_id` differs from the submission's `transcription_id`; the
episode id is the only value present in both. Deleting by callback id
alone would still leak rows.

**Change.**

- Look up the stored operation by episode (or by whatever key the
  repository already records), then delete it.
- Replace the processor's local `_slugify` with `utils.slug.generate_slug`.
- Type the processor against the repository interface, not
  `SqlitePodcastRepository`.

**Tests.**

- A callback removes the pending row.
- A repeated callback for the same episode is a no-op.
- A callback whose `request_id` matches nothing logs and leaves other
  rows alone.

**Priority.** Lower than Phase 1. Prod transcribes with Dalston, so this
path is probably idle there, but self-hosted ElevenLabs installs hit it.

## Phase 3: Delete internal dead code

Scope is exactly the Phase 0 appendix. Expected contents, to be
re-confirmed there:

**Python, no references:**

- `models/podcast.py`: `TranscriptMetadata`, `ProcessedContent`,
  `CleanedTranscript`, `TranscriptCleaningMetrics`.
- Transcribers:
  - Google: `resume_pending_operations`, `wait_for_pending_operations`,
    `_format_timestamp_mmss`.
  - ElevenLabs: `resume_pending_operations`, `_log_error`.
  - Whisper and Parakeet: `estimate_processing_time`,
    `generate_prompt_from_podcast_info`; Whisper also `_preprocess_audio`.
- `utils/path_manager.py`: `clean_transcript_json_file`,
  `raw_transcript_evaluation_file`, `clean_transcript_evaluation_file`,
  `pending_operation_file`, `chunks_dir`.
- `utils/logger.py`: `ProcessingLogger`, `get_default_log_path`.
- `utils/cli_formatter.py`: the six unused static `format_*` methods (the
  class stays).
- `mcp/utils.py`: the four `build_*_uri` functions, plus their unused
  import in `mcp/resources.py`.
- `feed_manager.py`: `get_episodes_with_raw_transcripts`,
  `get_episodes_with_clean_transcripts`.
- Small leftovers:
  - `error_classifier.wrap_as_transient` and `wrap_as_fatal`
  - `audio_preprocessor.clip_audio`
  - `slug.generate_unique_slug`
  - `duration.format_duration_verbose`
  - `jwt._unsafe_peek_token_expiry`
  - `url_generator.episode_transcript` and `episode_summary`
  - `PodcastService.get_segmented_transcript`
  - `role_linker._split_name_and_bio`

**Python, test-only (delete with their tests):**

- `core/transcript_parser.py` (whole module).
- `llm_provider.py`: `chat_completion_streaming` on five providers and
  `chat_completion_with_continuation` on four. Update the log strings
  that mention them.
- `wikidata_client.lookup_entity`.
- `external_transcript_downloader.download_all_pending`.
- `audio_downloader.cleanup_old_files`.
- `transcript_formatter.format_to_file`.
- `jwt.is_token_expiring_soon`.
- `follower_service.get_followed_podcasts`, `is_following_by_slug`.
- `path_manager`: `clean_transcript_file_with_podcast`, `feeds_file`,
  `file_exists`, `get_file_path`.
- `NullWikidataClient` and `NullWikipediaClient` move to `tests/`
  instead of being deleted, if tests still use them.

**Interface methods with no production caller.** Remove them from the
interface and every implementation in one change:

- Podcast and episode repositories: `save_podcast`,
  `get_unprocessed_episodes`, `get_episodes_by_podcast`,
  `get_episode_by_external_id`, `is_top_podcast_in_region`,
  `get_podcast_platform_url_sources`, `transaction`.
- Queue managers: `cancel_retry`, `cleanup_old_tasks`.
- User repositories: `update_last_login`.
- Follower repositories: `get_by_user`, `get_by_podcast`.
- Pending-ops repositories: `update_payload`.
- Entity repositories: `count_mentions_for_episode`, `get_enrichment`,
  `list_overrides`.
- Briefing-delivery repositories: `get_for_briefing`.
- File storage: `delete_batch`, `get_public_url`.

Each one is checked again in Phase 0: `save_episodes` looked dead, but a
script calls it.

**Frontend:**

- Dead client functions: `getPipelineTaskStatus`,
  `getEpisodeTasksExtended`, `getInboxUnreadCount`, `getFailedEpisodes`,
  `getEpisodeFailure`.
- Dead hooks: `useRecentActivity`, `usePodcastEpisodes`, `useInbox`,
  `useAddPodcast`, `useFailedEpisodes`, `useEpisodeFailure`.
- The two invalidations of the unused `['inbox', 'unread-count']` query
  key (`ImportEpisodeModal.tsx`, `useApi.ts`).
- `ShareButton.tsx` and its test. Spec #90 replaced it with
  `EpisodeMoreMenu`. Update the `useShareAction` doc comment.
- The response types left unused by the above.
- Drop `export` from about 50 types used only inside `api/types.ts`.
- The `@types/dompurify` dev dependency. Edit `package.json` and the
  lockfile without a macOS `npm install`.
- Declare `hast` and `unified`, which are imported but not listed.

**Not in this phase:** backend endpoints, compatibility branches, repair
tools.

**Done when:** `make check` and the frontend tests pass, and a re-run of
vulture and knip shows only the documented false positives.

## Phase 4: Inert configuration and stale documentation

**Remove (they change no processing):**

| Item | Why |
|---|---|
| `REFRESH_VIA_QUEUE` | Read only into a log field. The CLI help says it implies `--queue`; the code says it does not. |
| `ENABLE_TRANSCRIPT_CLEANING`, `CLEANING_EXTRACT_ENTITIES` | They build a `cleaning_config` dict that is never passed on; they only change a `status` line. |
| `DEBUG_CLIP_DURATION` | Config field never read. Remove it from the `thestill-aws` "must never reach production" list too. |
| `SPOTIFY_CLIENT_ID`, `SPOTIFY_CLIENT_SECRET` Config fields | Duplicates; `spotify_resolver` reads the environment directly. |
| `evaluate-raw-transcript`, `evaluate-clean-transcript`, `_deprecated_eval_wrapper` | Deprecated since spec #53 (2026-07-10); their message promises removal. Update `tests/unit/cli/test_eval_cli.py`. |
| `_extract_executive_summary` | One-line compatibility shim with one caller; inline `extract_gist`. |
| `MODEL_CONFIGS` and `ModelLimits` re-export in `core/post_processor.py` | Nothing imports them from there. |
| `BATCH_RECOGNIZE_TIMEOUT_SECONDS` | Labelled legacy, unused. |
| Stale "deprecated `clean_transcript` param" comments in `whisper_transcriber.py` | The parameter no longer exists. |

**Single source for each env var.** Read these through `Config` only,
with one default:

- `ELEVENLABS_WEBHOOK_SECRET` (also re-read in `web/routes/webhooks.py`).
- `DALSTON_BASE_URL`, `DALSTON_API_KEY`, `DALSTON_MODEL` (re-read in
  `dalston_transcriber.py` with a different fallback).
- `ELEVENLABS_API_KEY`, `ELEVENLABS_BASE_URL` (re-read in
  `elevenlabs_transcriber.py`).
- `QUEUE_ABANDONED_THREAD_BUDGET` (read in `core/task_worker.py`).
- `EMBEDDING_MODEL`: import `search.base.DEFAULT_EMBEDDING_MODEL` instead
  of repeating the literal.

**Small fixes:**

- Set `SERVICE_VERSION` from the image tag in the Dockerfile or compose
  file. Every prod log line says `1.0.0` today.
- Document `DEV_ALLOW_UNSIGNED_WEBHOOKS`, the only env var read but not
  documented.

**Docs.** Update `docs/configuration.md`, the CLI help, and
`specs/README.md`, where #78, #92 and #94 still show as in progress or
"default off".

## Phase 5: One owner for output paths

**Drift.** Three places build cleaned-transcript and summary paths
themselves:

- `cli.py` (clean and summarize commands)
- `mcp/tools.py` (clean and summarize tools)
- `core/task_handlers.py` (clean and summarize handlers)

They disagree:

- The MCP clean tool has two naming branches (`{episode_slug_hash}` and
  `{base_name}`).
- Summaries come out as `{slug_hash}_summary.md` from the CLI but as
  `{clean_stem}_summary.md`, that is `…_cleaned_summary.md`, from the
  worker.

`mcp/resources.py` hard-codes `storage_path / "raw_transcripts"` with a
`PathManager` in scope, and `core/facts_manager.py` repeats PathManager's
facts directory names.

**Change.**

- Add `PathManager.clean_transcript_target(...)` and
  `PathManager.summary_target(...)`. Each returns the absolute path and
  the database-relative path.
- Use them from all three callers and from `mcp/resources.py`.
- Choose one summary filename for new files. Existing rows keep their
  stored paths, so no data migration is needed.

**Tests.** One test per target asserting that the CLI, MCP and worker
paths produce the same name for the same episode.

## Phase 6: Repository interfaces and shared logic

**Interfaces.** Declare on the podcast and episode repository interfaces
the public methods the application keeps and calls (34 after Phase 3
removals; re-count then). A backend missing a method then fails at
instantiation, not in a prod request.

**Contract tests.** Add dual-backend tests for every newly declared
method. Postgres contract tests already run in CI through
`TEST_DATABASE_URL`; this extends their coverage.

**Shared pure logic.** Move code that is identical between backends into
backend-neutral modules:

- `enqueue_full_pipeline` and `enqueue_discovered_episodes` (identical in
  `queue_manager.py` and `postgres_queue_manager.py`).
- One Postgres `_episode_from_row`. There are three copies today, in
  `postgres_inbox_repository.py`, `postgres_podcast_repository_episodes.py`
  and `postgres_podcast_repository_podcasts.py`.
- The related-episodes orchestration in `related_builder.py` and
  `pg_related_builder.py`, behind a `CorpusStore` protocol extending the
  existing `IncrementalBackend`.

**Smaller duplicates** (one PR or folded into the above):

- `_format_time` (two copies in `google_transcriber.py`, one in
  `whisper_transcriber.py`) → `utils.duration.format_elapsed`.
- The nested `format_duration` in `api_commands.py` →
  `utils.duration.format_duration`.
- `_as_utc` (Apple linker, YouTube linker, `api_commands.py`) and five
  SQLite ISO parsers → `datetime_utils.as_utc` and `parse_iso_utc`.
- `normalize_title` and its regexes (Apple and YouTube linkers) →
  `core/platform_match.py`.
- The identical `_save_transcript` overrides in the Dalston and
  ElevenLabs transcribers → removed; use the base class.
- `_build_segments` (ElevenLabs, webhook processor, Dalston) → one helper
  in `core/transcriber.py`.
- Hand-built episode URLs in `web/public_shell.py` →
  `utils.url_generator`.

**Frontend:**

- Clock formatting: `MiniPlayer.formatTime`, `EntityRail`, `public.ts`
  and `SegmentedTranscriptViewer` → `utils/formatClock` (add `fromMs`).
- `formatTimeRemaining` (byte-identical in `PipelineStepper` and
  `PipelineActionButton`) and `formatRuntime` (`NarrationView`,
  `NarrationStatsTile`) → shared utilities.
- About 12 local date wrappers → `utils/dateFormat.ts`
  (`formatShortDate`, `formatDateTime`, `formatRelative`), with one
  locale.
- `pages/Podcasts.tsx` and `pages/TopPodcasts.tsx` → use the existing
  `useDebouncedSearchParam`.
- `listening.ts` and `public.ts` reuse the base fetch wrapper and the
  existing `apiErrorMessage`.

**Out of scope here.** A shared continuation or retry abstraction in
`llm_provider.py` is reconsidered only after Phase 3 deletes the unused
continuation entry points. What remains may be too small to justify one.

## Phase 7: Retire operational tools behind conditions

Each item ships as its own small PR once its condition holds.

### 7a. Repairs done on prod (needs D1)

| Tool | Code removed with it | Condition |
|---|---|---|
| `repair-mojibake` | `core/mojibake_repair.py`, `text_sanitizer.repair_mojibake` and its helpers, the `ftfy` import and dependency. `sanitize_text` stays. | D1 |
| `repair-anchor-mentions` | `core/anchor_scan_repair.py`, `list_anchor_scan_overlaps` (3 repositories) | D1 |
| `backfill-inbox` | `inbox_service` backfill, `backfill_existing_followers` (interface + 2 repositories) | D1 |
| `repair-images` | `feed_manager` image repair, `ImageRepairOutcome` | D1. Refresh already re-syncs images. |
| JSON pending-ops import at SQLite boot | The import function and its test | D1 |
| `repair-entity-types` | `find_mistyped_entities` (interface + 2 repositories) | Spec #86 retargeted to `retype_in_place`. The id-changing re-type is harmful (re-typed id 404 incident). |

### 7b. After a pending prod run

| Tool | Condition | Note |
|---|---|---|
| `backfill-summary-previews` | Prod run done | Keep the lazy fill in `resolve_summary_preview` unless D1 drops old databases. |
| `clean-aliases` | Prod alias cleanup done | Keep `is_related_alias`; `entity_coref` and `entity_anchor` use it. |
| `relink-direct` and `core/entity_relink.py` | ReFinED relink sweep verified complete | Independent of ReFinED: it re-decides old links with the live linker. |
| `claim-local-user` | `--discard` confirmed on prod | Keep the auto-claim on first login while single → multi-user stays supported. |

### 7c. ReFinED (needs D2)

- Remove `ENTITY_LINKER` and the `refined` branch in
  `entity_linking/factory.py`.
- Remove `REFINED_DATA_DIR` handling, the dependency, its patches and
  smoke test.
- Delete the 8.6 GB cache on the box.

### 7d. Parked flags (needs D3)

Apply whichever option D3 picks for the reranker and entity leg,
`ENTITY_RESOURCE_SEEDS_ENABLED`, `NARRATION_ENABLED` and
`ENABLE_DIARIZATION`.

### 7e. Cutover tools

- Remove `utils/db_promotion.py`, `utils/db_migration.py` and
  `test_sqlite_to_postgres_migration`.
- **Condition:** spec #99's category seeder has landed. `db_promotion` is
  the only thing that fills Postgres `categories` today.
- Drop the line in #104 that plans to teach `db_migration` new tables.

### 7f. Scripts

- Remove the one-off scripts:
  - `backfill_control_chars.py`
  - `backfill_description_fields.py`
  - `backfill_feed_holes.py`
  - `backfill_segmented_transcripts.py`
  - `diag_gemini_clean_218.py`
  - `audit_segment_coverage.py`
  - `prototype_entity_review.py`
  - `cleaning_prototype.py`
  - `compare_cleanup.py`
  - `verify_speakers.py`
  - `check_feed_discrepancies.py`
  - `survey_alternate_enclosures.py`
- **Condition:** links in specs #39, #41, #42 and #100 are repointed to a
  commit SHA.
- `prototype_entity_review.py` is the only caller of
  `/api/entities/review-queue` and `/api/entities/corrections`; decide
  those endpoints with it.

### 7g. Endpoints

- Remove any Phase 0 endpoint that has no hits in the
  [A.8](#a8-endpoints-no-durable-access-history) captures and that the
  operator confirms nothing external calls. Remove its
  `docs/web-server.md` row and tests with it.
- Keep the non-SSE task status endpoints if they are the documented
  fallback for SSE.

### 7h. SQLite migration squash (needs D1 = b)

- Replace the 1,674-line `_run_migrations` ladder in
  `sqlite_podcast_repository.py` with a baseline schema taken from a
  fresh database.
- Add a guard that refuses databases older than the baseline release,
  with a clear message.
- Fold the `_migrate_add_column` calls in `queue_manager._ensure_table`
  into its `CREATE TABLE`.

## Kept on purpose

- **Opt-out switches:** `PUBLIC_PAGES_ENABLED` and `MCP_HTTP_ENABLED`.
  Self-hosters may want them.
- **Kill switches and operational flags:**
  - `QUOTA_ENFORCE`
  - `QUEUE_AUTO_HEAL`, `QUEUE_EXIT_ON_DEGRADED`, `QUEUE_CIRCUIT_BREAKER`,
    `QUEUE_STAGE_WATCHDOG_SECONDS`
  - `PLATFORM_LINKS_ENABLED`, `ANALYTICS_ENABLED`
  - `REFRESH_SCHEDULER_ENABLED`, `REFRESH_ON_OPEN_ENABLED`,
    `MIGRATE_ON_STARTUP`
- **Jti-less token handling.** `JWT_EXPIRE_DAYS` is configurable, so old
  tokens may still be valid on some installs. Revisit with D1.
- **Facts extraction JSON-mode fallback.** It is "silent degradation"
  in #42's catalogue, so make it log loudly rather than delete it. Track
  it as a #42 follow-up.
- **Legacy flat-layout path fallbacks and null-slug branches.** Delete
  only after a query shows no such rows or files remain.
- **Maintenance commands:**
  - `backfill-roles`, `resolve-summary-citations`, `rebuild-cooccurrences`
  - `merge-aliases`, `enrich-entities`, `rebuild-entities`,
    `resolve-entities`
  - `chunks backfill` and `chunks verify`, `related build`,
    `rebuild-entity-pages`
- **SQLite feature parity**, until D4.

## Non-goals

- Removing the SQLite backend. That is about 15k lines, 118 test files
  and the zero-setup install. It is a separate decision after D4 and
  after tests run on Postgres by default.
- Regrouping the 17 top-level entity CLI commands under `thestill entity`
  and `thestill repair`. It is worth doing once Phase 7 has removed the
  retired ones, as its own spec.
- New features of any kind.

## Tracking

- [x] Phase 0: inventory appendix, endpoint evidence route, vulture comment fix
- [x] Phase 1: DLQ retry-all on Postgres
- [ ] Phase 2: ElevenLabs webhook pending-op cleanup
- [ ] Phase 3: dead code (Python, frontend)
  - [x] 3a: Python A.1 and A.2
  - [ ] 3b: Python A.3 and A.4, with their tests
  - [x] 3c: frontend A.6
- [ ] Phase 4: inert configuration and docs
- [ ] Phase 5: PathManager output targets
- [ ] Phase 6: interfaces, contract tests, shared logic, small duplicates
- [ ] D1–D4 recorded in this spec
- [ ] Phase 7a–7h, each behind its condition

## Appendix A: Phase 0 inventory

Generated 2026-10-21 on `origin/main` at `022a1f85`. Line numbers are at
that commit; each later PR re-checks them. "Tests" lists the files under
`tests/` that a deletion touches.

### A.0 Method

```bash
uvx vulture thestill --min-confidence 60 --exclude 'thestill/web/frontend'
npx --yes knip --reporter compact            # in thestill/web/frontend
npx --yes knip --production --reporter compact
uvx pylint --disable=all --enable=duplicate-code --min-similarity-lines=12 \
  --ignore-imports=yes --ignore-docstrings=yes --ignore=frontend,migrations thestill
```

Each vulture candidate was then checked with a word-boundary grep over
`thestill/`, `tests/`, `scripts/`, `evaluation/`, `deploy/`, `docs/` and
`specs/`. Definition lines, comments, docstrings, doctests and log
strings do not count as uses.

Vulture reported 388 unused functions, methods and classes. 223 are
registered by a framework and are not dead:

- Click commands
- FastAPI routes and exception handlers
- Pydantic validators
- Starlette `dispatch` methods
- Alembic `upgrade` and `downgrade`

Of the rest, the groups below are real findings. Name collisions and
docstring-only mentions were resolved by hand. `SupportsStaleReset` stays:
it is a protocol the tests use to check that both queue managers conform.

### A.1 Python: no references (Phase 3)

About 970 lines. Removed in Phase 3a, together with what the removals
stranded: `Quote`, `GoogleTranscriptMetadata`, `WhisperTranscriber._split_on_silence`
and five imports.

| Location | Symbol | Note |
|---|---|---|
| `models/podcast.py:530,549,558,631` | `TranscriptMetadata`, `ProcessedContent`, `CleanedTranscript`, `TranscriptCleaningMetrics` | 147 lines; specs still mention `CleanedTranscript` |
| `core/google_transcriber.py:2067,2209` | `wait_for_pending_operations`, `_format_timestamp_mmss` | 105 lines; one docstring mention |
| `core/elevenlabs_transcriber.py:385` | `_log_error` | |
| `core/whisper_transcriber.py:612` | `_preprocess_audio` | |
| `core/audio_preprocessor.py:91` | `clip_audio` | |
| `core/error_classifier.py:416,435` | `wrap_as_transient`, `wrap_as_fatal` | |
| `core/feed_manager.py:1432,1467` | `get_episodes_with_raw_transcripts`, `get_episodes_with_clean_transcripts` | |
| `core/progress_store.py:231` | `get_all_active` | |
| `core/summary_citations.py:151` | `summary_citations_path` | |
| `core/task_handlers.py:818` | `handle_entity_branch_placeholder` | Spec #28 Phase 0 no-op; no stage registers it any more |
| `models/transcript.py:85` | `get_text_with_timestamps` | |
| `repositories/postgres_podcast_repository_podcasts.py:1011` | `_save_episode_row` | |
| `services/narration/models.py:64` | `with_id` | |
| `services/podcast_service.py:764` | `get_segmented_transcript` | |
| `services/role_linker.py:224` | `_split_name_and_bio` | |
| `utils/cli_formatter.py:61-142` | `format_episode_list`, `format_success`, `format_error`, `format_info`, `format_progress`, `format_completion` | The class stays |
| `utils/logger.py:51-94` | `ProcessingLogger` (9 methods), `get_default_log_path` | `setup_logger` stays |
| `utils/path_manager.py:84,377,453,486,548,563` | `_validate_episode_id`, `clean_transcript_json_file`, `raw_transcript_evaluation_file`, `clean_transcript_evaluation_file`, `pending_operation_file`, `chunks_dir` | |
| `utils/config.py:1106` | `get_default_config_path` | |
| `utils/console.py:86` | `progress_complete` | |
| `utils/duration.py:251` | `format_duration_verbose` | |
| `utils/jwt.py:97` | `_unsafe_peek_token_expiry` | |
| `utils/slug.py:53` | `generate_unique_slug` | Doctest only; no doctest runner |
| `utils/url_generator.py:70` | `episode_transcript` | |
| `mcp/utils.py:167-220` | `build_podcast_uri`, `build_episode_uri`, `build_transcript_uri`, `build_audio_uri` | Remove the unused import in `mcp/resources.py:36` too |
| `web/routes/api_dashboard.py:44,67` | `DashboardStats`, `ActivityResponse` | Pydantic models no route uses |
| `web/routes/api_search.py:654` | `_resolve_episode_slugs` | |
| `web/routes/webhooks.py:86,103` | `ElevenLabsWebhookPayload`, `WebhookResultSummary` | Pydantic models no route uses |

### A.2 Python: defined on two classes, called by neither (Phase 3)

About 130 lines. None of these is on a base class. Removed in Phase 3a.

| Location | Symbol |
|---|---|
| `core/google_transcriber.py:2020`, `core/elevenlabs_transcriber.py:967` | `resume_pending_operations` |
| `core/whisper_transcriber.py:474,486`, `core/parakeet_transcriber.py:207,214` | `estimate_processing_time`, `generate_prompt_from_podcast_info` |

### A.3 Python: test-only (Phase 3, delete with the tests)

About 1,590 lines, including the whole `transcript_parser.py` module.

| Location | Symbol | Tests |
|---|---|---|
| `core/transcript_parser.py` (whole module, 565 lines) | `load_best_external_transcript` and the rest | `unit/core/test_transcript_parser.py` |
| `core/llm_provider.py:1293,1519,2090,2741,3038` | `chat_completion_streaming` (5 providers, ~500 lines) | `unit/providers/test_gemini_provider.py`, `unit/providers/test_mistral_provider.py` |
| `core/llm_provider.py:1052,1624,2432,2897` | `chat_completion_with_continuation` (4 providers) | `unit/providers/test_gemini_provider.py`; update the log string at `:1164` that recommends it |
| `core/audio_downloader.py:265` | `cleanup_old_files` | `unit/core/test_audio_downloader.py` |
| `core/external_transcript_downloader.py:188` | `download_all_pending` | `unit/core/test_external_transcript_downloader.py` |
| `core/feed_manager.py:130` | `transaction` (and its `_in_transaction` state) | `unit/services/test_feed_manager.py` |
| `core/google_transcriber.py:1656` | `_load_operation` | `unit/core/test_transcriber_pending_ops_persistence.py` |
| `core/media_source.py:1710,1719` | `get_youtube_source`, `get_rss_source` | `unit/models/test_media_source.py` |
| `core/queue_manager.py:195` | `is_idempotent_stage` | `unit/core/test_restart_recovery.py` |
| `core/task_worker.py:908` | `_maybe_enqueue_next_stage` (superseded by the atomic hand-off in `complete_task`) | `unit/core/test_entity_branch_fanout.py`, `unit/core/test_chain_stops_when_unwanted.py`: point them at `_chain_successors` |
| `core/transcript_formatter.py:146` | `format_to_file` | `unit/utils/test_formatter.py` |
| `core/wikidata_client.py:210` | `lookup_entity` | `unit/core/entity_linking/test_candidates.py` |
| `core/wikidata_client.py:493`, `core/wikipedia_client.py:182` | `NullWikidataClient`, `NullWikipediaClient` | Move to `tests/` (used by `unit/core/test_wikidata_client.py`, `unit/core/test_wikipedia_client.py`) |
| `mcp/scopes.py:62` | `registered_tools` | `unit/mcp/test_scopes.py` |
| `repositories/factory.py:264` | `make_user_repository` | `integration/test_user_repository_contract.py` |
| `services/follower_service.py:232,311` | `get_followed_podcasts`, `is_following_by_slug` | `integration/follower/test_follower_flow.py` |
| `services/platform_link_service.py:213` | `link_all` | `unit/services/test_platform_link_service.py` |
| `utils/jwt.py:135` | `is_token_expiring_soon` | `integration/auth/test_auth.py` |
| `utils/path_manager.py:361,583,620,654` | `clean_transcript_file_with_podcast`, `feeds_file`, `file_exists`, `get_file_path` | `unit/services/test_path_manager.py` |
| `utils/podcast_categories.py:107-119` | `is_valid_category`, `is_valid_subcategory`, `get_subcategories` | `unit/utils/test_podcast_categories.py` |
| `utils/url_generator.py:83` | `episode_summary` | `unit/services/test_path_manager.py` (the other matches are a different `episode_summary`) |
| `core/entity_linking/rate_limiter.py:91` | `reset_shared_rate_limiter` | **Keep:** a test-reset hook (`unit/core/entity_linking/test_rate_limiter.py`, `test_factory_and_config.py`) |

### A.4 Python: interface methods with no production caller (Phase 3)

About 720 lines across the base classes and both backends. Remove each one
from the interface and every implementation in one change.

| Method | Defined in | Tests |
|---|---|---|
| `cleanup_old_tasks`, `cancel_retry` | `core/queue_manager.py:1780,2475`, `core/postgres_queue_manager.py:1001,1668` | `integration/test_postgres_queue_manager.py`, `unit/core/test_queue_auto_heal.py` |
| `get_episodes_by_podcast` | `podcast_repository.py:816` + both backends | `integration/test_podcast_repository_episodes_contract.py`, `unit/repositories/test_sqlite_podcast_repository.py` |
| `get_unprocessed_episodes` | `podcast_repository.py:885` + both backends | same two files |
| `is_top_podcast_in_region` | SQLite `:5420`, Postgres podcasts `:716` (not on the interface) | `integration/test_podcast_repository_podcasts_contract.py`, `unit/repositories/test_sqlite_top_podcasts.py` |
| `transaction` (repository) | SQLite `:2771`, Postgres podcasts `:245` | docstring examples only |
| `update_last_login` | `user_repository.py:121` + both backends | `integration/test_user_repository_contract.py` |
| `get_by_podcast` | follower repositories (3) | `integration/follower/test_follower_flow.py` |
| `update_payload` | pending-ops repositories (3) | `integration/test_pending_operations_repository_contract.py`, `unit/repositories/test_sqlite_pending_operations_repository.py` |
| `count_mentions_for_episode`, `get_enrichment`, `list_overrides` | entity repositories (3 each) | `integration/test_entity_repository_contract.py`, `unit/repositories/test_sqlite_entity_repository.py`, `unit/repositories/test_sqlite_entity_enrichment.py`, `unit/cli/test_enrich_entities_cli.py` |
| `count_imports_for_user_since` | inbox repositories (3); spec #31 import cap, superseded by the #104 usage ledger | `integration/test_inbox_repository_contract.py`, `unit/repositories/test_sqlite_inbox_repository.py`, `unit/services/test_import_service_apple.py` |
| `delete_batch`, `get_public_url`, `get_size`, `ensure_directory` | `utils/file_storage/base.py`, `local.py`, `s3.py` | `unit/utils/file_storage/test_contract.py`, `test_local.py`, `test_s3.py` |

**Exceptions to decide in Phase 3.** These have no production caller, but
tests use them widely to seed data or to read it back. Deleting them
means rewriting fixtures, so they may stay as test support:

- `save_podcast`, `save_episodes`: about 390 lines across the interface and both backends.
- `get_episode_by_external_id`: 37 test lines.
- `get_for_briefing`: 47 test lines.
- Follower `get_by_user`: used by the abuse-admin tests.

`scripts/backfill_feed_holes.py` also calls `save_episodes`; that script
retires in Phase 7f.

**`count_admins`: delete in Phase 3b.** It is on the user-directory
repositories, added in spec #104 on 2026-10-18. The last-admin guard it
was meant for already exists: `revoke_admin` and the disable path call
`set_admin(..., keep_an_active_admin=True)` (and the status update with the
same flag), which counts the other active admins in the same transaction as
the update, under an advisory lock. So the standalone count is unused, and a
count-then-update built on it would race.

### A.5 Whole modules

| Module | Lines | Importers | Phase |
|---|---:|---|---|
| `core/transcript_parser.py` | 565 | `tests/unit/core/test_transcript_parser.py` only | 3 (A.3) |
| `utils/db_migration.py` | 302 | `tests/integration/test_sqlite_to_postgres_migration.py` only | 7e |
| `utils/db_promotion.py` | 518 | none | 7e |

### A.6 Frontend (Phase 3)

| Kind | Items |
|---|---|
| Dead files | `src/components/ShareButton.tsx` and `ShareButton.test.tsx`; update the `useShareAction.ts` doc comment |
| Dead client functions (`src/api/client.ts`) | `getPipelineTaskStatus`, `getEpisodeTasksExtended`, `getInboxUnreadCount`, plus `getFailedEpisodes` and `getEpisodeFailure` once their hooks go |
| Dead hooks (`src/hooks/useApi.ts`) | `useRecentActivity`, `usePodcastEpisodes`, `useAddPodcast`, `useFailedEpisodes`, `useEpisodeFailure`, `useInbox` (only a comment in `queryClient.ts` mentions it) |
| Dead query invalidations | `['inbox', 'unread-count']` in `ImportEpisodeModal.tsx:50` and `useApi.ts:1208`; no query reads that key |
| Duplicate | `browserTimezone` is exported from `client.ts` and redefined in `BriefingScheduleSettings.tsx:25`. Import the export |
| Drop `export`, keep the symbol | `useMarkInboxRead` (used inside `useApi.ts`), `EVENT_PROPS`, `variantStyles`, `sizeStyles`, `GuestLine`, `TICK_INTERVAL_MS`, `SEEK_DEBOUNCE_MS`, `randomId`, `MIN_RESUME_SECONDS`, `setFollowPlayback`, `SWIPE_CLOSE_PX`, `FOCUSABLE_SELECTOR`, `transistorOrigin`, `supportsMediaSession`, `TOP_N`, `RATE_OPTIONS`, `DEFAULT_RATE`, `isValidRate`; the named or default double export of `KaraokeWord` |
| Unused exported types | About 50 in `src/api/types.ts` and 14 elsewhere (knip list in A.0): drop `export` where the type is used inside its file, delete it where it is not (`ExtendedPipelineTask`, `EpisodeWithFailure`, the inbox unread-count response) |
| Dependencies | Remove `@types/dompurify` (dompurify 3 ships its own types). Declare `hast` and `unified`, which are imported but not listed. Edit `package.json` and the lockfile without a macOS `npm install` |
| Keep | `contexts/playback-engine/youtube-player-fake.ts`, `tests/episode-hero-fixture.ts`, `src/test/setup.ts` (test infrastructure) and the exports that exist for tests (`APP_ROUTE_PATTERNS`, `gpcEnabled`, `apiErrorCode`, `__reset*ForTests`) |

### A.7 Phase 4 items re-checked on this commit

All still hold:

- `REFRESH_VIA_QUEUE` is read only into a log field (`web/app.py:607`).
  The CLI help at `cli.py:647` still says it implies `--queue`, and the
  comment at `:660` says it does not.
- `ENABLE_TRANSCRIPT_CLEANING` and `CLEANING_EXTRACT_ENTITIES` build
  `cleaning_config` at `cli.py:2027` and `:2248`. It is never passed on:
  `transcribe_audio` gets only `TranscribeOptions`.
- `DEBUG_CLIP_DURATION` (`config.py:479`) is never read. Its only test
  checks that `thestill-aws` never migrates it.
- The `SPOTIFY_CLIENT_*` Config fields (`config.py:338`) are unread;
  `spotify_resolver.py:436` reads the environment directly.
- `_deprecated_eval_wrapper` (`cli.py:3326`) with `evaluate-raw-transcript`
  and `evaluate-clean-transcript`. Update `tests/unit/cli/test_eval_cli.py`
  and `docs/evals.md:196`.
- In `core/post_processor.py:36`, `MODEL_CONFIGS` and `ModelLimits` sit in
  `__all__`, but nothing imports them from there. The module itself uses
  both, so only the `__all__` entries go.
- `BATCH_RECOGNIZE_TIMEOUT_SECONDS` (`google_transcriber.py:81`) is unused.
- `_extract_executive_summary` (`briefing_script_generator.py:389`) has
  one caller.
- These env vars are read twice:
  - `ELEVENLABS_WEBHOOK_SECRET`: `webhooks.py:78`
  - `ELEVENLABS_API_KEY`, `ELEVENLABS_BASE_URL`: `elevenlabs_transcriber.py:133,141`
  - `DALSTON_BASE_URL`, `DALSTON_API_KEY`, `DALSTON_MODEL`:
    `dalston_transcriber.py:100-102`, with a `DEFAULT_BASE_URL` fallback
    that Config does not have.

### A.8 Endpoints: no durable access history

Prod keeps no access logs that could produce 30-day counts:

- Nothing ships logs to CloudWatch. Every compose service uses Docker's
  `local` driver with 5 × 20 MB rotation
  (`deploy/aws-ec2/docker-compose.prod.yml:18`).
- Caddy has no `log` directive.
- Every deploy recreates the app container, so its logs start again.

`docs/logging-cloudwatch-queries.md` assumes a CloudWatch pipeline that
this deployment does not have. That includes the "Tokens and Cost by
Stage" query the LLM usage cost check (PR #351) relies on. This is a separate
follow-up.

**Evidence route instead:**

1. **The operator confirms the admin-only endpoints.** These are
   `/api/commands/status`, `/api/commands/task/{id}`,
   `/api/commands/progress/current`, `/api/episodes/failed` and
   `/api/episodes/{id}/failure`. Only admins can call them, and prod has
   one.
2. **Capture the user endpoints from the retained app log before each
   deploy for two weeks.** These are `/followers/count` and
   `/inbox/unread-count`. On the box (`thestill-aws ssh`, then
   `cd /srv/thestill`):

```bash
docker compose -f docker-compose.prod.yml logs --no-log-prefix --timestamps thestill 2>/dev/null \
  | head -1 | cut -c1-20                       # oldest retained line = coverage start
docker compose -f docker-compose.prod.yml logs --no-log-prefix thestill 2>/dev/null \
  | grep '"http_request_completed"' \
  | grep -oE '"endpoint": ?"[^"]*(/followers/count|/inbox/unread-count|/api/commands/status|/api/commands/task/[^"]*|/api/commands/progress/current|/api/episodes/failed|/api/episodes/[^"/]*/failure)"' \
  | sed -E 's#/api/podcasts/[^/]+/#/api/podcasts/{slug}/#; s#/commands/task/[^"]+#/commands/task/{id}#; s#/episodes/[^/"]+/failure#/episodes/{id}/failure#' \
  | sort | uniq -c
```

Record each capture (date, coverage start, counts) in this section.
Phase 7g removes an endpoint only when every capture shows zero hits and
the operator confirms nothing external calls it.

| Capture date | Coverage from | Hits |
|---|---|---|
| _pending_ | | |

### A.9 Duplicate-code clusters (Phases 5–6)

pylint finds 24 clusters of 12 or more similar lines. They match the
audit:

- **ElevenLabs transcriber vs webhook processor:**
  `elevenlabs_transcriber.py:780-911` ↔ `webhook_transcript_processor.py:217-349`
  (132 lines). `_build_segments` is shared with
  `dalston_transcriber.py:548-611`.
- **Queue managers:** `enqueue_*` at `queue_manager.py:1069-1189` ↔
  `postgres_queue_manager.py:353-479`, and `:2604-2639` ↔ `:1796-1831`.
- **Postgres `_episode_from_row` copies:**
  `postgres_inbox_repository.py:118-137` ↔
  `postgres_podcast_repository_episodes.py:122-141` ↔
  `postgres_podcast_repository_podcasts.py:1659-1674`.
- **Output paths:** `cli.py:1159-1189` ↔ `mcp/tools.py:1153-1180` ↔
  `task_handlers.py:596-614`.
- **Search and related builders:** `sqlite_vec_client.py:158-222` ↔
  `pgvector_client.py:182-242`, `related_builder.py:158-180` ↔
  `pg_related_builder.py:114-136`, and `chunk_writer.py:106-132` ↔
  `postgres_chunk_writer.py:78-105`.
- **Repository SQL twins:** 13 pairs across the entity, inbox and podcast
  repositories. These are Phase 6 candidates only where the logic, not
  just the SQL, is the same.

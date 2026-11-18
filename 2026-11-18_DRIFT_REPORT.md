# EGM Drift Report — 2026-11-18

Drift between the 47 `EGM.md` contracts written by `egm-bootstrap` on
2026-11-18 and the code at commit `c4337539`. Eight fresh-context reviewers
each re-read their contracts against the current source. Every item below
was checked against the code before it was reported. Contracts that match are
listed as "No drift detected."

Items are of three kinds:

- **Contract errors**: the EGM.md says something the code does not do. Fix
  the contract.
- **Coverage gaps**: public code the contract does not mention.
- **Code findings**: the contract describes the intended behaviour and the
  code falls short. These need a human decision, and the most important are
  listed first.

## Triage: code findings worth a decision

- Search: a query of only exclusions (`-nvidia`) runs as a search *for*
  `nvidia` (`thestill/search/query_translator.py:194-198`).
- Web: `/api/admin`, `/api/status` and `/api/dashboard` are mounted with
  `require_admin` only, so they skip `enforce_read_only` and the per-user
  rate limit (`thestill/web/app.py:872-894`).
- Web: non-admin reads of any task's status and progress
  (`/api/commands/task/{id}`, `/progress`, `/episode/{id}/tasks`) have no
  ownership check, and `GET /api/commands/add/status` without a `task_id`
  returns the last add from any user (`thestill/web/routes/api_commands.py`).
- Logging: `LOG_FORMAT=gcp` bypasses `log_safety_processor`, so secrets are
  not redacted in that format (`thestill/logging.py:173-183`).
- SSRF: `core/media_source.py:_resolve_apple_podcast_redirect` follows
  redirects through `urllib` without re-validating each hop
  (`media_source.py:1067-1084`).
- CLI: `mention drop`, `mention repoint`, `entity-alias-add`,
  `set-entity-visibility`, `episode set-guests`, `podcast set-hosts` and
  `podcast set-recurring` write the repository directly, with no audit row.
- CLI: `transcribe --dry-run --cancel-pending` still cancels Google
  operations (`thestill/cli.py:2010-2027` runs before the dry-run return).
- Alembic: `thestill/migrations/env.py` loads `.env` with bare
  `load_dotenv`, ignoring `THESTILL_ENV_FILE`.
- Migration `0010` backfill overwrites existing podcast chart URLs
  (`COALESCE(t.apple_url, p.apple_url)` lets the chart win).
- `evaluation/run.py` and `scripts/cleaning_prototype.py` import the deleted
  `thestill.core.transcript_cleaner`; `scripts/compare_cleanup.py` calls the
  missing `PathManager.clean_transcript_shadow_file` yet still overwrites
  production cleaned transcripts unless `--dry-run` / `--analyse-only`.
- `thestill/cli.py:entity_get --json` does not cap `recent_mentions` at 5
  (`cli.py:5265`); the `podcast detect-hosts` hint is not an f-string and
  names a nonexistent `extract-entities` command (`cli.py:5582`).
- Bootstrap regression (fixed in this run): an `EGM.md` inside
  `thestill/services/narration_prompts/` was listed as an anchor voice by
  `available_anchor_prompts()`. That contract was moved into
  `thestill/services/narration/EGM.md`; see the section below.

## Resolution

All contract errors and coverage gaps below were corrected on branch
`docs/egm-contracts` (worktree `../thestill-egm`, based on `e369ba1b`). Each
was re-checked against the code at `e369ba1b` before it was applied. The same
pass also updated the contracts for the code that changed since `c4337539`
(216 files) and added two new contracts, `thestill/web/frontend/src/drift`
and `thestill/web/frontend/src/workers`. The route table now lists 142
routes.

Lists that already have a CI-checked home were then removed from the
contracts, which now link to them instead:

- the route table goes to `docs/web-server.md`
  (`tests/docs/test_api_route_drift.py`);
- the MCP tool table goes to `docs/mcp-usage.md`
  (`tests/docs/test_mcp_tool_drift.py`);
- the CLI command table goes to `CLAUDE.md` and `docs/`
  (`tests/docs/test_cli_drift.py`);
- the `Config` field list goes to `docs/configuration.md`
  (`tests/docs/test_env_var_drift.py`);
- the architecture overview goes to `specs/01-architecture.md`.

The route and MCP contracts keep only what those docs lack: the guard each
router is mounted with, and the scope each tool requires.
`specs/02-api-reference.md` now says it is not the complete route list.

One finding was not applied: `thestill.followPrompt.dismissed` no longer
exists, because `FollowPrompt` was deleted on main.

Code findings stay open. Each is recorded in its contract as a "Known issue"
bullet. The fix pass found four more:

- `thestill refresh --dry-run` still saves newly discovered episodes:
  `RefreshService.refresh` always calls `feed_manager.refresh_feeds`, which
  writes through `save_refresh_batch` (`thestill/core/feed_manager.py:800`).
  `dry_run` only skips platform links and enqueueing.
- `thestill transcribe` with ElevenLabs webhooks waits forever when it reuses
  an already-running `thestill server`: the callback is marked done in the
  server's tracker, not the CLI's (`thestill/cli.py:1948-1965, 2372-2378`).
- `thestill/web/frontend/src/workers/driftAlign.worker.ts:18` has a `.then`
  with no `.catch`: a rejection posts no `done`, and the attempt stays
  `running`.
- `/api/admin`, `/api/status` and `/api/dashboard` calls are never counted
  against `USER_API_LIMIT`, as noted in the triage list above
  (`thestill/web/app.py:879-901`).

## EGM.md

- "Logging passes through `log_safety_processor`, which redacts secrets" does not hold for `LOG_FORMAT=gcp`: `thestill/logging.py:173-183` uses `structlog_gcp.build_processors(...)` as the whole chain, and `log_safety_processor` is added only in the non-gcp branch (logging.py:187-205), so gcp output is never redacted.
- "Configuration is loaded only through `load_config` / `load_env_file`" is wrong: modules read the environment directly, e.g. `logging.py:117, 171` (`LOG_FORMAT`, `LOG_FILE`), `utils/url_guard.py:103` (`URL_GUARD_ALLOWLIST`), `core/spotify_resolver.py:449-450` (`SPOTIFY_CLIENT_ID/SECRET`), `core/entity_resolver.py:240` (`REFINED_DATA_DIR`), `mcp/tools.py:113` (`MCP_SESSION_KEY`), `web/middleware/rate_limit.py:61`, `web/app.py:960` (`THESTILL_STATIC_DIR`), and `DALSTON_*` / `ELEVENLABS_*` fallbacks in `core/dalston_transcriber.py:81-82, 156-158` and `core/elevenlabs_transcriber.py:133, 141`.
- "Every redirect hop re-validated" is not universal: `core/media_source.py:1067-1084` (`_resolve_apple_podcast_redirect`) validates the URL once, then calls `urllib.request.urlopen`, which follows redirects without re-validation; the URL reaching it is filtered only by a substring test (`"podcasts.apple.com" in url`, media_source.py:1005).
- (Compiler note, same defect as thestill/web/EGM.md) "every `/api` router except auth and public pages requires a session, honours read-only mode and is rate-limited" is wrong for `/api/admin`, `/api/status` and `/api/dashboard`, which are mounted with `require_admin` only (thestill/web/app.py:872, 874, 890-894).

## thestill/EGM.md

- The Overview says admin and curation commands go through the audited services as `Actor.cli()`, listing only `podcast set-hosts / set-recurring` as exceptions. These also write the repository directly, with no `Actor` and no audit: `mention drop` (cli.py:6206-6238) and `mention repoint` (cli.py:6240-6278), `entity-alias-add` (cli.py:6316-6345), `set-entity-visibility` (cli.py:554-573), `episode set-guests` (cli.py:5634-5653).
- The processor-order invariant ("includes `log_safety_processor`") is false for `LOG_FORMAT=gcp` (logging.py:173-183).
- "`--dry-run` returns before any write, download or LLM call" does not fully hold for `transcribe`: the Google `--cancel-pending` branch (cli.py:2010-2027, `reset_pending_operations()`) runs before the dry-run return (cli.py:2191), and on ElevenLabs the webhook server starts first (cli.py:1937-1965).

## deploy/EGM.md

No drift detected.

## deploy/aws-ec2/EGM.md

- `ensure_iam` does not grant "these permissions only": it also attaches the AWS-managed `AmazonSSMManagedInstanceCore` policy (thestill-aws:595-599), which grants `ssm:GetParameter(s)` on `*`, `ssmmessages:*`, `ec2messages:*` and more.
- `docker-compose.prod.yml` also hard-sets `ENVIRONMENT: production` and `COOKIE_SECURE: "true"` (docker-compose.prod.yml:44-45), not just the six listed values.
- The bootstrap does not remove "old app images only": it also runs `docker image prune -f` (thestill-aws:978), removing every dangling image.
- `_secrets_diff` exits 1 for a missing local variable except those in `_DIFF_EXPECTED_LOCAL_ONLY` (`STORAGE_PATH`, `DATABASE_URL`, `LOG_*`, `WHISPER_*`, …), which are subtracted first (thestill-aws:1335).
- `audit` does not record every mutating action: `_ensure_recovery_alarm` (thestill-aws:1580), instance-profile creation and role attachment and `put_role_policy` in `ensure_iam` (:603-617), SG rule authorizations on an existing SG in `ensure_app_sg`, and the Dalston rule revoke in `cmd_teardown` write no audit entry.

## evaluation/EGM.md

- Not every step error is caught: in `step_dalston_transcribe` and `step_elevenlabs_transcribe` the transcript-reuse path and `find_audio_file` run before the `try` (run.py:95-103, 137-145), so an I/O or JSON error aborts the run.
- `--skip-dalston` "still works" only while `dalston_transcribe` is `pending`; on `--resume` with it `done`, `--skip-dalston` changes nothing (run.py:539-541), `clean_d` runs and raises `ModuleNotFoundError` (run.py:569-573).

## scripts/EGM.md

- Not only `backfill_description_fields.py` can talk to Postgres: `scripts/narration_ab.py:173-174` uses `make_repositories(config)`, which picks Postgres when `DATABASE_URL` is set.
- `diag_gemini_clean_218.py:main` can write files: `TranscriptCleaningProcessor.clean_transcript` saves missing facts files (`save_episode_facts`, transcript_cleaning_processor.py:269; `save_podcast_facts`, :285).
- In `backfill_feed_holes.py` the synthetic filter is in `_load_podcasts` (:59), the YouTube skip in `main` (:115) and the insert (`repo.save_episodes`) in `main`; `_find_holes` only fetches and filters by date.
- `backfill_control_chars.py:backfill_db` calls the best-effort `maybe_load_vec_extension` (:71), which returns `False` silently without sqlite-vec (thestill/utils/sqlite_ext.py:65-85); the trigger UPDATE then fails. It does not "always load" the extension.

## thestill/core/EGM.md

- Coalescing is said to return the existing row for `(podcast, stage)` too. `QueueManager.add_feed_task` returns `None` when it coalesces (thestill/core/queue_manager.py:1290-1292, docstring :1229-1236), and so does `PostgresQueueManager.add_feed_task` (thestill/core/postgres_queue_manager.py:500-502). Only the episode path `_insert_or_coalesce` returns the existing `Task` (queue_manager.py:1072-1081).
- The Postgres lock claim is wrong for feed tasks: `add_feed_task` does not call `_lock_target`; it takes its own advisory lock keyed `add_feed_task:{podcast_id}:{stage}` (postgres_queue_manager.py:462-466). `_lock_target` covers episode tasks only (postgres_queue_manager.py:211, 235).
- The `QueueManager` / `PostgresQueueManager` method list omits public methods: `get_dead_tasks` (queue_manager.py:2240, the DLQ listing API), `fail_task` (:1628), `claim_pending_for_coalescing` (:1757), `episode_unwanted` (:1606), `has_pending_task` / `has_active_task_for_episode` / `has_pending_feed_task` (:1702, :1725, :1308), `get_task` / `get_tasks_for_episode` / `get_pending_count` (:1661, :1680, :1745).
- `refresh_failure.py` public types `RefreshFailure`, `RefreshAttemptResult`, `RefreshDecision` (refresh_failure.py:90, 101, 134) and `resolve_streak_state` (:165) are not mentioned.
- `RefreshScheduler.tick` also calls `repository.seed_unscheduled_feeds(...)` on every tick before reading due feeds (thestill/core/refresh_scheduler.py:103), a DB write the doc omits.

## thestill/core/entity_linking/EGM.md

- `entity_resolver` does not re-export `infer_entity_type_from_label`; thestill/core/entity_resolver.py:46-55 re-exports `SURFACE_LABEL_TO_ENTITY_TYPE`, `EntityLinkerBrokenError`, `ResolutionResult`, `linked_result`, `unresolvable_result` and private helpers only.
- "Any disagreement blocks promotion" is too broad: `_maybe_promote` considers only rows with the current `linker_version`, a non-null QID and non-low confidence (thestill/core/entity_linking/cache.py:136-141). A "none", a low-confidence answer, or a row from another linker version does not block promotion.
- Logging: besides `live_linker.py`, `candidates.py` emits `entity_linking_candidate_search_failed` (candidates.py:115) and `wikidata_control_chars_stripped`, and `chooser.py` emits `entity_linking_chooser_call_failed` (chooser.py:189-194) and `llm_control_chars_stripped`.

## thestill/repositories/EGM.md

- `upsert_entity`: QID and description are not "only filled when NULL". Both backends use `COALESCE(excluded.wikidata_qid, entities.wikidata_qid)` and `COALESCE(excluded.description, entities.description)` (sqlite_entity_repository.py:175, 185; postgres_entity_repository.py:176, 185), so a non-NULL incoming value overwrites the stored one. The ABC docstring has the same wrong wording (entity_repository.py:228-230).
- `LastActiveAdminError` is raised only when the caller passes `keep_an_active_admin=True` (default `False`) to `apply_status` / `set_admin` (sqlite_user_repository.py:231-233, 252-254; postgres_user_repository.py:221-222, 241-242; user_repository.py:165, 175-178). The doc cites `SqliteUserRepository.save`, which has no such guard.
- SQLite schema ownership is overstated: the SQLite `tasks` table is created by `core/queue_manager.py:678` (`QueueManager`), not by `SqlitePodcastRepository`.
- Without sqlite-vec the whole chunks step is skipped, including the plain `chunks` table and `chunks_fts`, not only the vec0 tables (sqlite_podcast_repository.py:1167-1178).
- sqlite-vec is not loaded "soft" on every SQLite connection: `utils/sqlite_ext.py:connect` defaults to `load_vec="none"`; only `SqliteEntityRepository` (sqlite_entity_repository.py:138) and `SqlitePodcastRepository._get_connection` (sqlite_podcast_repository.py:2906) pass `"soft"`.
- `sqlite_usage_event_repository.py` has `count_usage` (:32) and `insert_usage` (:42) but no `lock_usage`; SQLite serialises with `BEGIN IMMEDIATE` in `reserve` (:73).
- The `save_podcast` "Postgres-only divergence" is not one: SQLite also returns its input unchanged (sqlite_podcast_repository.py:3534), and both contradict the ABC docstring "with updated timestamps if changed" (podcast_repository.py:212-213).
- The lock inventory is incomplete: SQLite `BEGIN IMMEDIATE` also in OAuth `approve` / `redeem_code` / `rotate_refresh` (sqlite_oauth_repository.py:233, 313, 356), `move_follows_aside` / `restore_follows` (sqlite_suspended_follow_repository.py:32, 50) and `apply_status` / `set_admin` (sqlite_user_repository.py:232, 253); Postgres sync-track `promote` also takes `SELECT ... FOR UPDATE` (postgres_sync_track_repository.py:90-95), and its advisory lock uses `hashtext` while the others use `hashtextextended(..., 0)` (:88).

## thestill/services/EGM.md

- `publish` is said to discard a pending track whose generation or audio duration does not match. It discards only on a duration mismatch (`_repo.retire(..., STATE_PENDING)` + `_delete_files`, thestill/services/sync_track_service.py:200-215). On a generation mismatch it logs `sync_track_pending_superseded`, sets `pending = None` and leaves the pending row in place (sync_track_service.py:189-195).
- ID resolution is incomplete: `PodcastService.get_podcast` also resolves internal UUIDs (podcast_service.py:480-484) and slugs (:487-491); `get_episode` also matches the internal UUID (:560-565).
- Missing side effects: `RefreshService.refresh` fetches RSS and persists new episodes via `feed_manager.refresh_feeds` (refresh_service.py:133) and calls `PlatformLinkService.link_podcast` best-effort per refreshed podcast (refresh_service.py:150-158). The podcast-row writes of `PodcastService.add_podcast` / `remove_podcast` are not listed.

## thestill/services/abuse/EGM.md

- The `AdminActionError` code list is not exhaustive: `services/entity_curation.py` also raises `same_entity`, `type_mismatch`, `invalid_qid`, `qid_taken`, `exists`, `unchanged`, `no_mentions`, `no_surface` (entity_curation.py:127-295).
- `SettingsService.set` / `modify` take `audit=False`, which skips the audit row (`AccountAdminService._edit_overrides` passes it, account_admin_service.py:505); `reset` records only when a stored row was deleted (settings_service.py:214).
- `QuotaExceededError` sets `Retry-After` only when `retry_after_seconds` is set (errors.py:121-122); it is `None` for `follows_total`.
- `AlertService.send(message)` (alerts.py:163) and `channel_names` (alerts.py:147) are public but unlisted.

## thestill/services/narration/EGM.md

- With no LLM provider, `NarrationGenerator.generate` returns the skeleton narration with `mode="narrated"` (narration_generator.py:221-227, 442-460), not the link-index fallback the Overview claims. The "no clusterer or writer → fallback" invariant is unreachable: `_llm_available()` (narration_generator.py:498-499) and provider construction always yield both or neither, so the `llm_provider_missing` fallback (:357, :370) never fires; only empty or invalid script blocks trigger it.
- `NarrationRunner.run` and `artifact_exists` are keyword-only (narration_runner.py:108, 118-120); the doc shows positional signatures.
- `read_narration_header` returns `None` without logging for valid JSON that is not a dict (artefacts.py:47-48); only OS and decode errors log `narration.read_failed`.

## thestill/services/narration_prompts/EGM.md

- `available_anchor_prompts()` globs every `*.md` in `PROMPTS_DIR` (`__init__.py:63`), so this contract file itself is now listed as a voice: it returns `['EGM', 'conversational_anchor', 'conversational_v2', 'newsroom_anchor']`, and "EGM" appears in `load_anchor_prompt`'s "available: ..." error text. `load_anchor_prompt("EGM")` is still refused by the lowercase name regex. The Overview's "each anchor voice is a Markdown file in this directory" no longer implies "each Markdown file is a voice".

## thestill/web/EGM.md

- The default-deny bullet says every `/api` router except `auth` and `api_public` gets `[require_auth, enforce_read_only, user_rate_limit]`. `api_status`, `api_dashboard` and `api_admin` are mounted with `require_operator = [Depends(require_admin)]` only (thestill/web/app.py:872, 874, 890-894): they get authentication through `require_admin` but neither `enforce_read_only` nor `user_rate_limit`. The next bullet lists their guard correctly, so the two bullets contradict each other.
- `/robots.txt`, `/sitemap.xml`, `/sitemap-{page}.xml`, `/{indexnow_key}.txt`, `/assets` and `serve_spa` are all registered inside `if static_dir.exists():` (app.py:961). Without a `static/` build they do not exist and fall through to the default 404; the doc states them unconditionally.
- `BackgroundWebhookServer.start` does not make HTTP probes: it does a socket bind check (`_is_port_available`, background_server.py:99-107), starts uvicorn and sleeps (:131-154). The `requests.get` probe is in `is_thestill_server_running` (:38-62).

## thestill/web/routes/EGM.md

- Route table: all 140 rows match the code (method, full path, handler, guard) and none is missing.
- The same default-deny contradiction: `api_admin`, `api_dashboard` and `api_status` are mounted with `[Depends(require_admin)]` only (app.py:872, 874, 890-894), without `enforce_read_only` or `user_rate_limit`. The table is right; the invariant is wrong.
- "Every write handler in `api_admin.py` has a per-route `require_admin`" is not true: `send_test_alert` (api_admin.py:266-269) relies on the router-level guard and takes no `Actor`.
- `safe_next_path` accepts 512-character paths (rejects only `len > 512`, auth.py:230, 235) and rejects only `/login` and `/login/...` (auth.py:242), not `/login*` (`/login-help` passes).
- `safe_next_path` is listed under "helpers other modules import" but is used only inside `auth.py` (auth.py:284, 386).

## thestill/web/middleware/EGM.md

No drift detected.

## thestill/web/services/EGM.md

- "An empty podcast slug becomes `unknown`" cannot happen: a missing slug is replaced by `_slugify(podcast.title)` (webhook_transcript_processor.py:120), which returns `"untitled"` rather than an empty string (:380), so the `podcast_slug or "unknown"` fallbacks (:137, :141) are dead code.

## thestill/web/frontend/EGM.md

- The storage list ("`thestill:`-prefixed keys, `reading-position-*` and `thestill.pendingFollow`") omits `thestill.followPrompt.dismissed`, written to `localStorage` via `usePersistedBoolean` (src/components/FollowPrompt.tsx:7, 26).

## thestill/web/frontend/src/EGM.md

- The storage list has the same gap (`thestill.followPrompt.dismissed`, src/components/FollowPrompt.tsx:7).
- "`PublicApp`: signed-in paths redirect to `/login?next=…`" holds only for `/inbox`, `/briefings/*`, `/search`, `/settings`, `/episodes`, `/status`, `/queue`, `/failed`, `/analytics`, `/podcasts`, `/oauth/consent` (src/PublicApp.tsx:47). Other signed-in routes (`/admin/*`, the editor route `/podcasts/:p/episodes/:e/edit`) fall through to `NotFound` (src/PublicApp.tsx:50-57).
- Server-written pages are not "hydrated": main.tsx uses `createRoot(...).render` (src/main.tsx:46), replacing the server HTML; only the query cache is seeded.
- The provider tree omits `<StrictMode>` and `<ListeningQuerySync />` inside `QueryClientProvider` (src/main.tsx:47-53).

## thestill/web/frontend/src/analytics/EGM.md

No drift detected.

## thestill/web/frontend/src/api/EGM.md

- "Every request sends cookies (`credentials: 'include'`)" is false for ten POST helpers in client.ts that omit `credentials`: `startRefresh` (:246), `queuePipelineTask` (:300), `bulkProcessEpisodes` (:346), `retryDLQTask` (:375), `skipDLQTask` (:389), `retryAllDLQTasks` (:403), `retryFailedEpisode` (:463), `reprocessEpisode` (:480), `runPipeline` (:502), `cancelPipeline` (:520); also `AuthContext.logout` (src/contexts/AuthContext.tsx:173). Same-origin requests still send cookies by default.
- "An `account_disabled` refusal anywhere redirects to `/login?error=account_disabled`" holds only for `fetchApi` (src/api/client.ts:89), `addPodcast` (:279), `getOAuthPending` (:943) and `fetchJson` (src/api/http.ts:13). Other mutating helpers, `sendApi` (:921-934), `editorFetch` (src/api/transcriptEdits.ts:47), the listening transport and the public client throw without redirecting.

## thestill/web/frontend/src/components/EGM.md

- `ActionRow` has one primary plus up to three icon actions, four slots in total (src/components/ActionRow.tsx:6, 13-14), not "one primary and four icon slots".
- The `localStorage` list omits `thestill.followPrompt.dismissed` (src/components/FollowPrompt.tsx:7, 26).

## thestill/web/frontend/src/components/admin/EGM.md

No drift detected.

## thestill/web/frontend/src/components/analytics/EGM.md

No drift detected.

## thestill/web/frontend/src/components/episode-entities/EGM.md

- `synthesizeSummaryMention` is cited under `indexMention.ts`, but lives in `src/components/episode-entities/summaryMention.ts:9`, which the contract never mentions.

## thestill/web/frontend/src/components/episode-header/EGM.md

- There is no separate Share action in the action row: it is play, an optional Watch video icon, `EpisodeMoreMenu` (which holds Share) and `inboxAction` (src/components/episode-header/EpisodeHeader.tsx:146-163; src/components/EpisodeMoreMenu.tsx:39-42, 54).

## thestill/web/frontend/src/components/public/EGM.md

No drift detected.

## thestill/web/frontend/src/components/transcript-editor/EGM.md

No drift detected.

## thestill/web/frontend/src/constants/EGM.md

No drift detected.

## thestill/web/frontend/src/contexts/EGM.md

No drift detected.

## thestill/web/frontend/src/contexts/listening/EGM.md

No drift detected.

## thestill/web/frontend/src/contexts/playback-engine/EGM.md

No drift detected.

## thestill/web/frontend/src/hooks/EGM.md

- "Every in-app timestamp link goes through `episodeTimestampPath`" is broken in src/pages/SearchResults.tsx:373 and src/components/NowPlayingSheet.tsx:318, which build `?t=` by hand.
- "Reader position is restored only on Back/Forward (POP)" (src/hooks/useReadingPosition.ts:153-163): in page mode router POP is ignored and restore happens only on a document `back_forward` load or `location.state.restoreScroll`; in overlay mode router POP to a seen entry restores, as does `restoreScroll`.
- The polling list omits `usePodcast` (5 s while `refresh_pending`, src/hooks/useApi.ts:158), `useLatestBriefing` (15 s while items pending, :1307-1314) and `useQueueTasks` (5 s / 15 s, :952-959).

## thestill/web/frontend/src/pages/EGM.md

- `TranscriptEditorPage` also redirects back below the `sm` breakpoint (`if (!isSmUp) return <Navigate to={backTo} replace />`, src/pages/TranscriptEditorPage.tsx:337); only the `can('transcript.edit')` gate is documented.

## thestill/web/frontend/src/pages/admin/EGM.md

- `AdminSettings` also has a `signup_allowlist` editor (src/pages/admin/AdminSettings.tsx:99) and an alert-threshold overrides JSON editor that PUTs the `alerts` setting (AdminSettings.tsx ~165-198); neither is listed.

## thestill/web/frontend/src/pages/public/EGM.md

- The person topic filter is written to the fragment, not the query: ``FilterChip … to={{ hash: `about=${topic.slug}` }}`` (src/pages/public/EntityPage.tsx:218) and person links `/entities/person/${slug}#about=…` (:237). The page reads either form (`deepLinkParam`, :97); the canonical form is `#about=` (src/components/public/helpers.ts:11).

## thestill/web/frontend/src/utils/EGM.md

No drift detected.

## thestill/utils/EGM.md

- Invariants say every path built from an external string is resolved and must stay under `storage_path` (`PathManager._assert_inside_root`). That holds only for the slug-validated getters. The flat getters (`original_audio_file`, `downsampled_audio_file`, `raw_transcript_file`, `clean_transcript_file`, `summary_file`, `evaluation_file`) join a caller-supplied filename with no slug or root check (thestill/utils/path_manager.py:288-386, 419-429). The Interface "File getters" list omits them, plus `external_transcript_dir_for_podcast` and `evaluation_runs_dir`.
- Side Effects say `ensure_directories_exist` "creates the whole `data/` tree". Its list (path_manager.py:505-529) skips `briefings_dir()` (path_manager.py:216) and `external_transcripts_dir()` (path_manager.py:212).
- "Hosts listed in `URL_GUARD_ALLOWLIST` skip the public-IP check" holds only for hostnames. A bare-IP URL is checked with `_ip_is_public` and rejected (url_guard.py:160-168) before the allowlist is consulted (url_guard.py:170).

## thestill/utils/file_storage/EGM.md

No drift detected.

## thestill/migrations/EGM.md

- The claim that `env.py` loads the same `.env` as the app is only partly true. `env.py` calls bare `load_dotenv(_find_dotenv_from_package())` (thestill/migrations/env.py:44-48). The app's `config.py:load_env_file` checks the explicit path, then `THESTILL_ENV_FILE` (config.py:763), then the package walk, then the CWD walk. With `THESTILL_ENV_FILE` set, or with only a CWD `.env`, Alembic loads a different `.env` (or none). This also breaks the utils rule that `.env` loading must go through `load_env_file`.

## thestill/migrations/versions/EGM.md

- Row `0001` says it "creates the base tables listed in `_TABLES`" and names `digests`, `digest_episodes`, `briefings`, `briefing_episodes`. `_TABLES` (0001_initial_schema.py:46-76) is only the downgrade drop list. `upgrade()` runs the current `postgres_schema.SCHEMA_SQL`, which no longer creates those four tables (postgres_schema.py:47-865) and already creates every table later revisions add (`mcp_tokens`, `oauth_*`, `app_settings`, `admin_audit_log`, `episode_platform_links`, `transcript_edits`, `usage_events`, `episode_sync_tracks`, `user_briefing_schedules`, `briefing_deliveries`, `entity_link_decisions`, and more). On a fresh database 0001 builds the whole current schema and 0002-0028 are no-ops.
- "Backfills only fill gaps and never overwrite data", with 0010 as the example, is wrong for 0010: `SET apple_url = COALESCE(t.apple_url, p.apple_url), ...` (0010_podcast_chart_urls.py:50-55) lets the chart value win and overwrites an existing podcast link whenever the chart has one.

## thestill/webhook/EGM.md

- The Overview says the CLI is the only consumer and registers each episode. Registration is done by `thestill/core/elevenlabs_transcriber.py:351-355` (`get_tracker().add_pending`), completion by the web route `thestill/web/routes/webhooks.py:46,357-359` (`mark_completed`); `thestill/cli.py:2361-2375` only reads `pending_count` / `is_all_done` and waits.

## thestill/mcp/EGM.md

- The doc says exception text becomes `Internal error (ref <8-hex>)` "unless `config.environment == "development"`", citing `errors.py:public_error_message`. That function never reads config; it relays detail only when the caller passes `expose_detail` (thestill/mcp/errors.py:58-90). `tools.py` and `resources.py` pass it (thestill/mcp/tools.py:107,518; thestill/mcp/resources.py:134), but the backstop in `registration.py:register_tools` does not (thestill/mcp/registration.py:112), so an exception that escapes the dispatcher stays opaque even in development.

## thestill/mcp/middleware/EGM.md

- `log_mcp_stdio` is described as a stdio-only decorator that binds `transport="stdio"` once per stdio call. Its one use is on `list_tools` (thestill/mcp/tools.py:217), and `setup_tools` is also registered on the remote HTTP connector (thestill/web/mcp_http.py:125). `register_tools` calls `list_tools` on every remote `tools/list` and during argument validation on every `tools/call` (thestill/mcp/registration.py:89,98). On remote requests the decorator therefore binds `transport="stdio"`, `mcp_method="list_tools"` and a fresh `request_id` (stdio_adapter.py:60-64), then `clear_contextvars()` in `finally` (stdio_adapter.py:104) drops any request-scoped contextvars bound earlier in that handler context before the tool runs.

## thestill/models/EGM.md

No drift detected.

## thestill/search/EGM.md

- "Exclusions become a `NOT (...)` tail" does not hold for exclusion-only input: `_assemble` sets `positive_expr = negatives[0]` when there are no positives (thestill/search/query_translator.py:194-198), so `-nvidia` becomes the FTS expression `nvidia` and returns the chunks the user tried to exclude. A single exclusion also renders as `NOT term`, without parentheses (query_translator.py:206-207).
- The supported syntax omits bare uppercase `NOT`, which is passed through as an FTS5 operator (`_FTS5_OPERATORS = {"AND", "OR", "NOT"}`, query_translator.py:71).
- `VerifyReport.healthy` also requires `sampled > 0` (thestill/search/index_verify.py:70), so an empty sample reports unhealthy; the doc's health rule omits this.

## thestill/evals/EGM.md

No drift detected.

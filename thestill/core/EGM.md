# thestill.core

## Overview

`thestill/core` holds the atomic processors behind the podcast pipeline, the task queue that runs them (`QueueManager` / `PostgresQueueManager`, `TaskWorker`), and the background loops that feed it (`RefreshScheduler`, `BriefingScheduler`, `PeriodicRunner`). The pipeline and queue design is described in [specs/01-architecture.md](../../specs/01-architecture.md#task-queue-system); this contract records the code-level surface, invariants and side effects.

Most modules are processors that receive their collaborators: an LLM provider, a transcriber, repositories, `PathManager` or `FileStorage`. Persistence normally goes through `thestill/repositories`. A few modules talk to the database directly: the queue managers, the chunk writers and `mojibake_repair`.

## Interface

### Subdirectories

- [entity_linking](./entity_linking/EGM.md): turns spoken names into Wikidata QIDs (spec #81). Its entry points are `factory.build_linker`, `LiveWikidataLinker.resolve`, the `EntityLinker` protocol, `LinkDecisionCache` and `invalidate_link_decisions`.

### Queue and worker

- `queue_manager.py:TaskStage`: the pipeline stages.
  - User chain: `download`, `downsample`, `transcribe`, `clean`, `summarize`.
  - Entity branch: `extract-entities`, `resolve-entities`, `reindex`, `rebuild-cooccurrences`, `compute-related`, `enrich-entities`.
  - Feed-scoped: `refresh-feed`.
- `queue_manager.py:TaskStatus`: `pending`, `processing`, `completed`, `retry_scheduled`, `failed`, `dead`, `superseded`.
- `queue_manager.py:ErrorType`: `transient`, `fatal`.
- `queue_manager.py:Task`: dataclass for one task row. `Task.to_dict()` serializes it.
- `queue_manager.py:SupportsStaleReset`: protocol for `reset_stale_tasks`. Both backends implement it.
- Stage-graph helpers in `queue_manager.py`:
  - `STATE_TO_NEXT_STAGE`, `starting_stage_for`: the stage an episode should (re)start at, given its state.
  - `STAGE_SUCCESSORS`, `get_next_stages`: the stages to enqueue after a given stage.
  - `stages_at_or_before`: the stages in the same branch up to and including a given stage.
  - `is_feed_scoped_stage`, `is_idempotent_stage`, `is_entity_branch_stage`: stage classification.
  - `ENTITY_BRANCH_STAGES`, `USER_CHAIN_STAGE_VALUES`, `ACTIVE_TASK_STATUSES`, `coalescing_statuses`: stage and status groupings.
- `queue_manager.py:calculate_backoff`: retry delay of `min(5 * 6**retry_count, 600)` seconds, with ±20% jitter.
- `queue_manager.py:QueueManager`: SQLite task queue. Its public methods:
  - Enqueue: `add_task`, `add_task_if_episode_idle`, `enqueue_full_pipeline`, `enqueue_discovered_episodes`, `add_feed_task`, `promote_pending_feed_task`, `claim_pending_for_coalescing`.
  - Claim and complete: `get_next_task`, `complete_task`, `fail_task`, `supersede_stale_tasks`.
  - Retry and dead-letter queue (DLQ): `schedule_retry`, `reschedule_without_budget`, `mark_dead`, `get_dead_tasks` (the DLQ listing), `retry_dead_task(s)`, `find_healable_tasks`, `heal_task`.
  - Recovery: `reset_stale_tasks`, `recover_interrupted_tasks`.
  - Queries: `get_task`, `get_tasks_for_episode`, `get_pending_count`, `has_pending_task`, `has_active_task_for_episode`, `has_pending_feed_task`, `episode_unwanted`.
  - Admin: `cancel_*`, `bump_task`, `get_active_tasks`, `get_queue_stats`, `cleanup_old_tasks`, `sum_duration_by_stage`, `queued_audio_durations`.
- `postgres_queue_manager.py:PostgresQueueManager`: Postgres port of `QueueManager` with the same public surface. It imports the stage, status and backoff definitions from `queue_manager` rather than duplicating them.
- `task_worker.py:TaskWorker`: runs a background thread with an asyncio loop and one poller per `TaskStage`, each limited by its own semaphore. Methods: `start`, `stop`, `is_running`, `get_status`, `is_degraded`, `executor_max_workers`.
- `task_worker.py:request_process_exit`: sends `SIGTERM` to the worker's own process so the supervisor restarts it.
- `task_handlers.py:create_task_handlers`: builds the `TaskStage -> handler(task, progress_cb)` map from `AppState`. The handlers are:
  - User chain: `handle_download`, `handle_downsample`, `handle_transcribe`, `handle_clean`, `handle_summarize`.
  - Entity branch: `handle_extract_entities`, `handle_resolve_entities`, `handle_reindex`, `handle_rebuild_cooccurrences`, `handle_compute_related`, `handle_enrich_entities`.
  - Feed: `handle_refresh_feed`.
- `task_handlers.py:resolve_pending_mentions`, `ResolutionRun`, `build_link_context`, `convert_language_for_transcriber`: helpers shared with the CLI and services.
- `circuit_breaker.py:StageCircuitBreaker`: per-stage breaker. Methods: `allow_dispatch`, `cancel_dispatch`, `record_failure`, `record_success`, `is_tripped`, `snapshot`.
- `circuit_breaker.py:CircuitState`: `closed`, `open`, `half_open`.
- `error_classifier.py`:
  - `classify_error_class` returns `fatal`, `infra` or `item`.
  - `classify_and_raise` re-raises as `TransientError` or `FatalError`.
  - Predicates: `is_transient_error`, `is_fatal_error`, `is_infrastructure_error`, `is_programming_error`.
  - Constants: `TRANSIENT_HTTP_CODES`, `FATAL_HTTP_CODES`.
- `progress.py:TranscriptionStage`, `ProgressUpdate`, `STAGE_LABELS`: progress types for transcription.
- `progress_store.py:ProgressStore`, `TaskProgress`: thread-safe in-memory progress store with async pub/sub for server-sent events (SSE).
- `reprocess_metadata.py:reprocess_options`, `reprocess_requested_at`, `REPROCESS_INITIATED_BY`: read the spec #106 `reprocess` block in task metadata.

### Schedulers

- `refresh_scheduler.py:RefreshScheduler`: `tick()` seeds never-scheduled feeds, then enqueues a `REFRESH_FEED` task for every due feed and quarantine probe. Returns the number enqueued. Also `start`, `stop`, `is_running`.
- `briefing_scheduler.py:BriefingScheduler`: `tick()` generates, narrates and delivers briefings for due user schedules. Also `start`, `stop`, `is_running`.
- `periodic_runner.py:PeriodicRunner`, `PeriodicJob`: one daemon loop for small periodic jobs (spec #104). `run_due` runs every job whose interval has passed.

### Feeds, media and platform links

- `feed_manager.py:PodcastFeedManager`:
  - Podcasts: `add_podcast`, `remove_podcast`, `list_podcasts`.
  - Refresh: `refresh_feeds` (returns `RefreshOutcome`; `dry_run=True` fetches but skips the batch save and transcript links), `get_new_episodes`, `repair_episode_images` (returns `ImageRepairOutcome`).
  - Episode state: `mark_episode_downloaded`, `mark_episode_downsampled`, `mark_episode_processed`, `clear_episode_*_path`.
  - Work lists: `get_episodes_to_download`, `get_episodes_to_downsample`, `get_downloaded_episodes`.
  - Batching: `transaction`.
- `media_source.py:MediaSource` (abstract base class): `is_valid_url`, `extract_metadata`, `fetch_episodes`, `download_episode`.
- `media_source.py:RSSMediaSource`: implements `MediaSource` for RSS feeds. Adds:
  - `fetch_rss_content` (conditional GET), `parse_rss`, `fetch_and_parse`.
  - `extract_episode_images`, `extract_episode_audio_urls`, `extract_transcript_links`, `extract_alternate_enclosures`.
- `media_source.py:YouTubeMediaSource`: implements `MediaSource` for YouTube.
- `media_source.py:MediaSourceFactory`: picks the source for a URL. Methods: `detect_source`, `get_rss_source`, `get_youtube_source`.
- `media_source.py:FetchRSSResult`, `FetchAndParseResult`: results of an RSS fetch and of a fetch plus parse.
- `refresh_failure.py`:
  - `RefreshFailureKind`: `connectivity`, `remote_transient`, `remote_gone`, `authentication`, `invalid_content`, `security_policy`, `internal`.
  - `RefreshAction`: `backoff`, `quarantine`, `ignore`.
  - Policy and classification: `decide_refresh_action`, `classify_http_status`, `classify_fetch_exception`, `compute_backoff_interval`, `parse_retry_after`, `error_class_for_failure`, `RefreshPolicySettings`.
  - Result types: `RefreshFailure` (kind, real HTTP status, parsed Retry-After), `RefreshAttemptResult` (one feed refresh; `failure is None` means success), `RefreshDecision` (action plus `disabled_reason`).
  - `resolve_streak_state`: failure-streak bookkeeping shared by both repositories' `record_refresh_failure`. A change of failure kind restarts the streak.
- `audio_downloader.py:AudioDownloader`: `download_episode`, `delete_audio_file`, `cleanup_old_files`. Raises `DownloadError`.
- `audio_preprocessor.py:AudioPreprocessor`: `downsample_audio`, `preprocess_audio`, `needs_preprocessing`, `delete_downsampled_audio`. Targets 16 kHz, 16-bit, mono.
- `youtube_downloader.py:YouTubeDownloader`: `extract_playlist_info`, `get_episodes_from_playlist`, `download_episode`.
- `external_transcript_downloader.py:ExternalTranscriptDownloader`: downloads Podcasting 2.0 `<podcast:transcript>` files. Methods: `download_all_for_episode`, `download_all_pending`.
- Episode-link matchers (spec #87). These modules do no network I/O:
  - `apple_episode_linker.py:match_candidates`, `parse_lookup_window`.
  - `youtube_episode_linker.py:match_candidates`, `match_rule`, `claim_corroborated`.
  - `publisher_links.py:publisher_links_for_candidate`, `show_links_from_sources`.
- Episode-link helpers that call yt-dlp: `youtube_episode_linker.py:list_channel_videos`, `fetch_video`.
- `youtube_channel_discovery.py:search_channels`, `pick_channel`: find a show's YouTube channel and verify it by matching its uploads.
- `spotify_resolver.py:SpotifyLinkResolver`: methods `identify`, `resolve_episode`, `resolve_show`. Raises `SpotifyResolutionError`.
- `spotify_resolver.py:SpotifyWebApi`: Spotify Web API client.
- `spotify_resolver.py:find_apple_show`, `score_episode_candidate`, `pick_episode`: helpers for Spotify link resolution.
- `spotify_show_probe.py:fetch_latest_episode`, `parse_embed_page`, `show_id_from_url`: read the newest episode from a Spotify show's embed page.

### Transcription

- `transcriber.py:Transcriber` (abstract base class): `transcribe_audio`, `load_model`.
- `transcriber_factory.py:create_transcriber`, `validate_transcription_provider`: map provider names to transcriber classes.
  - `google` → `GoogleCloudTranscriber`.
  - `elevenlabs` → `ElevenLabsTranscriber`.
  - `dalston` → `DalstonTranscriber`.
  - `parakeet` → `ParakeetTranscriber`.
  - `whisper`, `whisperx` or empty → the Whisper transcribers.
- `whisper_transcriber.py:WhisperTranscriber`, `WhisperXTranscriber` (adds diarization), `DiarizationProgressMonitor`.
- `parakeet_transcriber.py:ParakeetTranscriber`: runs NeMo `nvidia/parakeet-tdt-0.6b-v3`.
- `google_transcriber.py:GoogleCloudTranscriber`: Speech-to-Text V2 (Chirp 3).
  - Long audio is split into chunks of up to `MAX_CHUNK_DURATION_MS` (12 min) with a `OVERLAP_DURATION_MS` (60 s) overlap.
  - Methods: `transcribe_audio`, `list_pending_operations`, `get_pending_operations_for_episode`, `reset_pending_operations`, `check_pending_operation`, `download_completed_operation`.
- `elevenlabs_transcriber.py:ElevenLabsTranscriber`: Scribe v1. Supports sync, async and webhook modes. Also `list_pending_operations`.
- `dalston_transcriber.py:DalstonTranscriber`: client for a self-hosted Dalston server using the Dalston SDK.
- `dalston_transcriber.py:iter_job_audio`: streams the audio stored for a Dalston job.
- `sync_track.py` (spec #116): `build_sync_track`, `align`, `SyncTrack` (`to_bytes`/`from_bytes`), `Alignment.to_other`, `iter_mp3_frames`, `iter_file_chunks`, `SyncTrackError`.

### Cleaning, facts and transcript access

- `transcript_cleaning_processor.py:TranscriptCleaningProcessor.clean_transcript`: two passes. Pass 1 extracts facts; pass 2 cleans with those facts. Raises `DegenerateTranscriptError`.
- `transcript_segmenter.py:TranscriptSegmenter.repair`: deterministic diarization repair. Produces an `AnnotatedTranscript`.
- `segmented_transcript_cleaner.py:SegmentedTranscriptCleaner.clean`: cleans the transcript segment by segment with an LLM. Patches use the `CleanupPatch` / `CleanupPatchBatch` schemas. Prompt version is `CLEANUP_PROMPT_VERSION = "v2"`.
- `facts_extractor.py:FactsExtractor`: `extract_episode_facts`, `extract_initial_podcast_facts`.
- `facts_manager.py:FactsManager`: load and save podcast and episode facts as Markdown.
- `transcript_edits.py:load_effective_transcript`: returns an `EffectiveTranscript`, which is the cleaned sidecar plus the replayed edit op log (spec #110).
- `transcript_edits.py`, other public symbols: `live_edits`, `EditConflict`, `EditLogUnavailable`, `is_placeholder_speaker`, `unnamed_content_segments`, `clear_cache`.
- `transcript_formatter.py:TranscriptFormatter`: `format_transcript` and `format_to_file` convert JSON to Markdown.
- `transcript_parser.py`: `parse_srt`, `parse_vtt`, `parse_json`, `parse_plain_text`, `parse_html`, `load_best_external_transcript`.

### Summaries

- `post_processor.py:TranscriptSummarizer.summarize`, `EpisodeMetadata`: produce the summary.
- `summary_profiles.py` (spec #98):
  - `resolve_summary_profile` returns a `ProfileResolution`.
  - `compose_system_prompt`, `available_profile_keys`, `empty_slot_lines`.
  - `PROFILES`, `CATEGORY_PROFILES`, `SummaryProfile`, `SlotSpec`, `PROFILE_SLOTS`.
- `summary_citations.py` (spec #54):
  - `resolve_summary_citations`, `resolve_and_persist_summary_citations`, `backfill_summary_citations_for_episode`.
  - `load_valid_citations_for_api`, `parse_timestamp_label`, `SummaryCitationsSidecar`.
- `summary_artifacts.py`: `write_summary_manifest`, `load_valid_summary_manifest`, `write_translation_metadata`, `load_valid_translation_metadata`, `content_sha256`.
- `summary_translation.py:SummaryTranslator`: `translate`, `detect_language`.
- `summary_resources.py` (spec #92): `parse_resource_list`, `plan_resources`, `ResourcePlan`, `KINDS`, `ResourceParseError`.
- `resource_seeds.py:ResourceSource`, `make_resource_source`: load the inputs that `summary_resources` needs from storage.

### Entities, search and corpus

- `entity_extractor.py:EntityExtractor`: GLiNER extraction over content segments (`extract`, `is_available`). Uses `DEFAULT_CONFIDENCE_THRESHOLD = 0.65`.
- `entity_resolver.py:EntityResolver`: ReFinED resolver (`resolve`, `is_available`). Raises `EntityResolverBrokenError`.
- `entity_type_rules.py:classify_entity_type`, `is_kind_of_person`, `is_ai_model`, `ALLOWED_P31_BY_TYPE`: choose the entity type from Wikidata "instance of" (P31) values.
- `entity_review.py:scan_entities_for_review`, `apply_correction`: admin review and correction. Raises `CorrectionError`.
- `entity_enricher.py:EntityEnricher.enrich`: builds Wikidata and Wikipedia enrichment. Does not persist it.
- `entity_coref.py:resolve_coreferences_for_episode`: coreference within one episode.
- `entity_anchor.py:expand_anchor_variants`, `load_anchor_entities`, `index_variants_by_surface`: host, guest and recurring name variants.
- `entity_alias_hygiene.py:plan_alias_cleanup` / `apply_alias_cleanup`: plan, then apply, alias cleanup. Each `AliasVerdict.reason` is `related`, `spelling_variant`, `evidence`, `allowlisted`, `redundant` or `unsupported`.
- `entity_alias_hygiene.py` name predicates, also used by `thestill/services/entity_curation.py` and `entity_anchor.py`:
  - `lookup_key`: folds ASCII case only, matching the SQL `LOWER(a) = LOWER(b)` of `find_entities_by_name` on both backends.
  - `is_related_alias`: the alias visibly names the canonical name.
  - `is_alias_of`: `is_related_alias`, or the same name up to case and accents ("Jose Alvarez" for "José Álvarez").
- `entity_retype.py:plan_retype` / `retype_in_place`: plan, then apply, an entity re-type.
- `entity_relink.py:plan_relink` / `apply_relink`: plan, then apply, a relink of old ReFinED links.
- `anchor_scan_repair.py:plan_anchor_scan_repair` / `apply_anchor_scan_repair`: plan, then apply, removal of bad anchor-scan mentions.
- `entity_page_writer.py:EntityPageWriter`: `write_entity_page`, `write_all`.
- `wikidata_client.py:WikidataClient`: `fetch_p31`, `fetch_superclasses`, `search_entities`, `lookup_entity`, `fetch_facts`, `fetch_labels`. Also `WikidataUnavailable` and `NullWikidataClient`.
- `wikipedia_client.py:WikipediaClient`: `search_entities`, `fetch_summary`. Also `NullWikipediaClient`.
- `embedding_model.py:EmbeddingModel`: `encode_one`, `encode_batch`, `warmup`, `self_check`, `reload`. Also `centroid_blob`.
- `chunk_writer.py:ChunkWriter.write_episode` (SQLite) and `postgres_chunk_writer.py:PostgresChunkWriter.write_episode` (pgvector).
- `chunk_writer.py:drop_short_segments`, `is_indexable_segment_text`: skip segments under `MIN_CHUNK_WORDS` (4).
- `mojibake_repair.py:MojibakeRepairer.run`: finds and repairs mojibake. Returns a `RepairReport`.

### LLM providers

- `llm_provider.py:LLMProvider` (abstract base class):
  - Completion: `chat_completion`, `generate_structured`, `generate_structured_cached`.
  - Capabilities: `supports_temperature`, `supports_structured_output`, `supports_prompt_caching`.
  - Info: `health_check`, `get_model_name`, `get_model_display_name`, `get_provider_name`, `get_max_output_tokens`.
- Implementations: `OpenAIProvider`, `OllamaProvider`, `AnthropicProvider`, `GeminiProvider`, `MistralProvider`. Most add `chat_completion_with_continuation`; all add `chat_completion_streaming`.
- `llm_provider.py:create_llm_provider`, `create_llm_provider_from_config`, `provider_kwargs_from_config`: construct providers.
- `llm_provider.py:ModelLimits`, `MODEL_CONFIGS`, `get_max_output_tokens`, `DEFAULT_MAX_OUTPUT_TOKENS` (8192): per-model limits.

## Invariants

### Queue

- `queue_manager.py:QueueManager._TASKS_TARGET_CHECK`: every task row has exactly one of `episode_id` or `podcast_id`.
- `queue_manager.py:QueueManager._TASKS_STAGE_CHECK`: the stage `CHECK` constraint is generated from `TaskStage`. `_ensure_table` rebuilds the table when the constraint is missing a stage.
- `queue_manager.py:QueueManager._insert_or_coalesce`: episode enqueueing is coalesced. If a row in `coalescing_statuses(stage)` already exists for `(episode, stage)`, that existing `Task` is returned and nothing new is inserted. The check runs under `BEGIN IMMEDIATE` in SQLite. In Postgres, `postgres_queue_manager.py:_lock_target` takes `pg_advisory_xact_lock` on the whole episode, so writers of any stage of that episode serialise.
- `queue_manager.py:QueueManager.add_feed_task` / `postgres_queue_manager.py:PostgresQueueManager.add_feed_task`: feed enqueueing is coalesced too, but it returns `None` (not the existing row) when a `pending`, `processing` or `retry_scheduled` row exists for `(podcast, stage)`. SQLite checks under `BEGIN IMMEDIATE`. Postgres does not use `_lock_target` here: it takes its own `pg_advisory_xact_lock` keyed `add_feed_task:{podcast_id}:{stage}`.
- `queue_manager.py:coalescing_statuses`: entity-branch stages coalesce only with `pending` and `retry_scheduled` rows. User-chain stages also coalesce with `processing` rows.
- `queue_manager.py:QueueManager.get_next_task`:
  - Picks by `priority DESC, created_at ASC`.
  - Claims with a conditional `UPDATE ... WHERE status IN ('pending','retry_scheduled')`, so two workers cannot claim the same row.
  - In `PostgresQueueManager.get_next_task`, the claim selects the row with `FOR UPDATE SKIP LOCKED`.
- `queue_manager.py:QueueManager.complete_task` / `schedule_retry`, lease guard: when `claim_started_at` is passed, the row changes only if it is still `processing` under that claim. A handler the watchdog abandoned therefore cannot overwrite a row that has since been reclaimed.
- `queue_manager.py:_IDEMPOTENT_STAGES` and `recover_interrupted_tasks`:
  - After a restart, user-chain stages and `REFRESH_FEED` go back to `pending`.
  - Entity-branch stages become `failed` with `error_class='infra'`.
  - Excluded stages are left as they are.
- `queue_manager.py:_NON_USER_FAILING_STAGES` / `is_entity_branch_stage`: entity-branch failures never set `episodes.failed_at_stage`. They only set `entity_extraction_status`.
- `queue_manager.py:QueueManager.supersede_stale_tasks`: only marks DLQ rows `superseded` when they are in the same branch as the completed stage and at or before it.
- `queue_manager.py:QueueManager.find_healable_tasks` / `heal_task`. A task is auto-healed only when all of these hold:
  - status is `failed` (never `dead`);
  - `error_class='infra'`;
  - `heal_attempts < max_heal_attempts`;
  - the cooldown has passed.
- `queue_manager.py:_discovered_episodes_to_enqueue`: an unfollowed feed enqueues nothing. A followed feed enqueues episodes published inside the follow window. It also enqueues the newest `inbox_seed_on_follow` episodes. The rest are marked `auto_process_excluded`.
- `queue_manager.py:QueueManager.DEFAULT_MAX_RETRIES`: 3. `calculate_backoff` caps the delay at 600 s.

### Worker and schedulers

- `task_worker.py:TaskWorker`: every stage has its own semaphore, so a slow stage cannot block another. If a handler outlives its per-stage watchdog, its slot is freed, and the abandoned thread counts against the pool's slack. When the worker is degraded, `request_process_exit` sends `SIGTERM`; `QUEUE_EXIT_ON_DEGRADED=false` disables this.
- `task_worker.py:TaskWorker._safe_schedule_retry` / `_safe_mark_dead`: a failed task is never left in `processing`. An infra failure recorded while the stage's breaker is not `CLOSED` goes through `reschedule_without_budget`, which does not consume the retry budget (`task_worker.py:TaskWorker._handle_transient_failure`).
- `circuit_breaker.py:StageCircuitBreaker`:
  - Trips `OPEN` after `failure_threshold` (3) infra failures within `window_seconds` (120).
  - After `cooldown_seconds` (60) it goes `HALF_OPEN` and allows one probe at a time.
  - State is in memory only, so a restart starts again from `CLOSED`.
- `refresh_failure.py:decide_refresh_action`:
  - `security_policy`, `authentication` and HTTP 410 quarantine a feed immediately.
  - `remote_gone` (404) and `invalid_content` quarantine only after the feed is at the maximum backoff (AIMD) interval and the failure streak has lasted a full maximum interval.
  - `connectivity` and `remote_transient` never quarantine a feed.
- `briefing_scheduler.py:BriefingScheduler.tick`: claims a slot before generating, through `schedule_repository.claim`, a conditional update that advances `next_run_at` to the next future occurrence. A slot therefore fires at most once, even with several instances, a crash or downtime. Narration is best effort.
- `refresh_scheduler.py:RefreshScheduler.tick`: first calls `repository.seed_unscheduled_feeds(default_interval_seconds)`, so a never-scheduled active feed becomes due. It then reads due feeds (`repository.get_due_podcasts`) and quarantine probes (`get_quarantine_probe_due`), each capped at `max_enqueue_per_tick` (500). It skips a feed that `has_pending_feed_task`, and enqueues the rest through the coalescing `add_feed_task`. Parked feeds are skipped.

### Transcripts and summaries

- `transcript_edits.py:load_effective_transcript`:
  - Every reader parses the cleaned sidecar through this function.
  - An edit op applies only when all of these hold: its `raw_sha256` matches the current raw transcript, its anchor resolves to exactly one segment, and that segment's text hash still matches. Otherwise the op is listed as a conflict, never dropped silently.
  - Pipeline stages pass `on_log_error="raise"`. Reader views pass `"fallback"`.
- `segmented_transcript_cleaner.py:CleanupPatch`: the schema has no source-anchor fields, so the LLM cannot rewrite `source_segment_ids` or `source_word_span`.
- `summary_profiles.py:resolve_summary_profile`:
  - Order: the podcast override, then the category map (primary subcategory, primary category, secondary subcategory, secondary category), then `general`.
  - A profile that is not enabled falls back to `general` and is reported as `candidate`.
  - Never raises.
- `summary_artifacts.py:load_valid_summary_manifest` / `load_valid_translation_metadata`: reject metadata whose schema version or content hash no longer matches.
- `summary_resources.py`: pure functions only: no file, database or network access. `ResourceParseError` means section 8 exists but cannot be read. It is never reported as "no resources".

### Entities and LLM

- `entity_extractor.py:EntityExtractor` and `entity_resolver.py:EntityResolver` load their models lazily on first use. `embedding_model.py:EmbeddingModel` loads lazily on its first `encode_*` call.
- `entity_enricher.py:EntityEnricher.enrich` has no side effects. The caller persists the result.
- `entity_alias_hygiene.py:plan_alias_cleanup`: an alias equal to the canonical name up to case and accents is `redundant` (dropped) only when `lookup_key` already equates the two. Otherwise ("Jose" for "José", "Weiss" for "Weiß") it is kept as `spelling_variant`, because the SQL name lookup can find the row only through that alias.
- `entity_alias_hygiene.py:apply_alias_cleanup`: removes aliases by `lookup_key`, not by the accent-folding `_fold`, so dropping a redundant spelling never also drops a kept accent-free variant.
- `entity_anchor.py:_surfaces_for`: anchor surfaces include the canonical name and its `models.entities.display_name` (the name without a "(qualifier)" suffix). Person first/last-name tokens and the alias check use the bare `display_name`, so a qualifier such as "professor" never becomes a match surface. Aliases are kept when `is_alias_of` the bare name, which keeps accent-free spellings because the scan matches accents exactly.
- `wikidata_client.py:WikidataUnavailable` means "retry later". It must never be treated as "no results".
- `llm_provider.py:create_llm_provider`: the `openai`, `gemini`, `anthropic` and `mistral` providers need an API key. `ollama` is health-checked when it is created.
- `llm_provider.py:GeminiProvider._build_config`: always sets the safety thresholds to `OFF`.

## Side Effects

### Databases

- SQLite `tasks` table: `queue_manager.py:QueueManager` runs DDL, migrations and indexes in `_ensure_table` and `_rebuild_tasks_with_extended_check`. All queue reads and writes go through it. On lock contention, `_exec_with_lock_retry` sleeps between attempts.
- Postgres `tasks` table: `postgres_queue_manager.py:PostgresQueueManager` uses psycopg, advisory transaction locks and `FOR UPDATE SKIP LOCKED`.
- Search index:
  - `chunk_writer.py:ChunkWriter` deletes and inserts SQLite `chunks` rows (a trigger fans them out to `chunks_vec` and `chunks_fts`) and upserts `episode_vectors`.
  - `postgres_chunk_writer.py:PostgresChunkWriter` does the same on Postgres `chunks` and `episode_vectors`.
- `mojibake_repair.py:MojibakeRepairer.run` scans and optionally updates `podcasts`, `episodes`, `entities` and `entity_mentions`, plus stored files and the chunk index.
- `briefing_scheduler.py:BriefingScheduler.tick` advances the `user_briefing_schedules` cursor through `schedule_repository.claim`.
- `refresh_scheduler.py:RefreshScheduler.tick` calls `repository.seed_unscheduled_feeds` on every tick, before it reads due feeds. That call sets `next_refresh_at` and `refresh_interval_seconds` on active `podcasts` rows that were never scheduled or attempted.
- Other modules persist through injected repositories.

### Filesystem and object storage

- Audio:
  - `audio_downloader.py:AudioDownloader` writes files under `original_audio/`.
  - `audio_preprocessor.py:AudioPreprocessor` writes 16 kHz WAV files with pydub.
  - `youtube_downloader.py:YouTubeDownloader` downloads with `yt_dlp`.
- Facts: `facts_manager.py:FactsManager` writes `podcast_facts/*.facts.md` and `episode_facts/<podcast>/*.facts.md` under the storage path.
- Summaries: `summary_artifacts.py:write_summary_manifest`, `write_translation_metadata` and `summary_citations.py:resolve_and_persist_summary_citations` write summary sidecars through `FileStorage`.
- `entity_page_writer.py:EntityPageWriter` writes `corpus/{persons,companies,topics}/<slug>.md`.
- Google Cloud Storage: `google_transcriber.py:GoogleCloudTranscriber` uploads audio, sets a 7-day lifecycle rule on the bucket, and deletes outputs. Pending operations are persisted through `PendingOperationsRepository`.

### Network

- Feeds and audio: `media_source.py:RSSMediaSource` uses a pooled `requests.Session` behind `utils.url_guard` server-side request forgery (SSRF) validation. `audio_downloader.py` and `external_transcript_downloader.py` fetch through `guarded_redirect_fetch`, and `feed_manager.py` through `guarded_get`.
- Transcription APIs:
  - Google Speech-to-Text V2: `google_transcriber.py`.
  - ElevenLabs (`https://api.elevenlabs.io`): `elevenlabs_transcriber.py`.
  - Dalston server: `dalston_transcriber.py`.
- LLM APIs: OpenAI, Anthropic, Gemini, Mistral and a local Ollama server, all from `llm_provider.py`. Health checks make real billed calls, and retries sleep for up to 60 s plus a buffer.
- Wikidata (`wikidata_client.py`) and Wikipedia REST (`wikipedia_client.py`).
- Spotify pages, embed pages, Web API and iTunes search/lookup: `spotify_resolver.py`, `spotify_show_probe.py`.
- YouTube via yt-dlp: `youtube_episode_linker.py`, `youtube_channel_discovery.py`.
- IndexNow ping after a summary is published: `task_handlers.py:_ping_indexnow_on_publish`.

### Processes and threads

- Daemon threads: `task_worker.py:TaskWorker`, `refresh_scheduler.py:RefreshScheduler`, `briefing_scheduler.py:BriefingScheduler`, `periodic_runner.py:PeriodicRunner`.
- Locks: `circuit_breaker.py:StageCircuitBreaker` and `progress_store.py:ProgressStore` are lock-guarded.
- `task_worker.py:request_process_exit` sends `os.kill(pid, SIGTERM)` from a timer.

### Logging

- `llm_provider.py:LLMProvider._log_usage` emits a structlog `llm_usage` event for each billed call.

## Verification

- Run: `./venv/bin/pytest tests/unit/core`. For the full suite, run `make test`.

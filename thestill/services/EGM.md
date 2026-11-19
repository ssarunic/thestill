# services

## Overview

The business-logic layer of thestill. The CLI (`thestill/cli.py`), the
FastAPI web app (`thestill/web/`) and the MCP server (`thestill/mcp/`) share
these services. The services sit between the presentation layers and the
repositories (`thestill/repositories/`), core processors (`thestill/core/`)
and utilities (`thestill/utils/`, including `PathManager` and
`FileStorage`). They take their repositories, queue manager, config and
storage through constructor arguments; most take no global state.

The services cover these areas:

- podcasts and episodes: lookup, transcripts, summaries and translations
- follows, the per-user inbox, URL imports and listening progress
- per-user briefings and their email delivery
- authentication, MCP tokens and the MCP OAuth authorization server
- admin curation: entities, import twins, episode reprocess and the
  transcript editor op log
- playback manifests and `https` audio addresses
- platform links, sync tracks, IndexNow pings and analytics

`__init__.py` re-exports a small public set:

- `PodcastService`, `PodcastWithIndex` and `EpisodeWithIndex`
- `RefreshService` and `RefreshResult`
- `StatsService`, `SystemStats` and `ActivityItem`
- `FollowerService` and its errors
- `BriefingScriptGenerator` and its dataclasses

Callers import every other module by its own path.

## Interface

### Subpackages

- [`./abuse/EGM.md`](./abuse/EGM.md): abuse prevention (spec #104). Covers
  the access guard, the signup gate, runtime settings and feature switches,
  the follow gate, quotas, alerts, the audit log and account-admin actions.
  `wiring.build_abuse_services` constructs all of them.
- [`./narration/EGM.md`](./narration/EGM.md): the narrated briefing digest
  (`NarrationRunner`, `NarrationGenerator`, `QuoteSelector`,
  `ThemeClusterer` and `ScriptWriter`).
- `./narration_prompts/` (contract in
  [`./narration/EGM.md`](./narration/EGM.md#anchor-prompts),
  because that folder may hold voice `*.md` files only): the anchor
  voice prompts and the `NOISE_PHRASES` list.

### Podcasts, episodes and refresh

- `podcast_service.py:PodcastService`, by group:
  - Podcasts: `add_podcast`, `remove_podcast`, `get_podcasts`,
    `get_podcast`.
  - Episodes: `get_episode`, `get_episodes`, `get_episodes_count`.
  - Transcripts: `get_transcript`, `get_transcript_for_episode`,
    `get_segmented_transcript_for_episode`,
    `get_transcript_words_for_episode`.
  - Summaries: `get_summary`, `get_summary_for_episode`,
    `get_recorded_summary_language`, `detect_and_record_summary_language`.
  - Translations and citations: `get_or_create_summary_translation`,
    `get_available_summary_languages`,
    `get_summary_citations_for_episode`.

  `get_podcast` resolves a podcast id by 1-based index (an int or a digit
  string), internal UUID, slug, then RSS URL. `get_episode` resolves an
  episode id by 1-based index (1 = latest), `latest`, date (`YYYY-MM-DD`),
  internal UUID, then external id (GUID).
- Supporting types and functions in `podcast_service.py`:
  - `PodcastWithIndex` and `EpisodeWithIndex`
  - `TranscriptResult`, `SegmentedTranscriptResult`, `SegmentWordsResult`
    and `TranscriptWordsResult`
  - `resolve_summary_preview` and `extract_summary_preview`
- `refresh_service.py:RefreshService.refresh(podcast_id=None, max_episodes=None, max_episodes_per_podcast=None, dry_run=False, progress_callback=None) -> RefreshResult`.
- `refresh_on_open.py:RefreshOnOpenService.maybe_trigger(podcast_id, now=None) -> RefreshOnOpenOutcome`.
  Its constant `OPEN_REFRESH_PRIORITY` is 10.
- `podcast_add.py:add_podcast_and_auto_follow(podcast_service, follower_service, auth_service, config, url, *, new_feed_precheck=None)`.
- `stats_service.py:StatsService`: `get_stats() -> SystemStats` and
  `get_recent_activity(limit=20) -> List[ActivityItem]`.
- `playback.py`:
  - `build_playback_manifest(episode, alternate_enclosures)`
  - `build_track_payload(podcast, episode, alternate_enclosures)`
  - `is_video_enclosure(mime_type)`
- `audio_address.py:AudioAddressResolver.playable_url(audio_url, listener="") -> Optional[str]`:
  the `https` file address of an episode whose enclosure is plain `http`
  (spec #116 / Safari). It returns an `https` enclosure unchanged with no
  request, `None` for any other scheme or an unreachable chain. The module
  exports the process-wide instance `resolver` (used by
  `web/routes/api_podcasts.py`) and `USER_AGENT` (`Thestill/1.0`).
- `platform_link_service.py:PlatformLinkService`:
  `link_podcast(podcast, *, dry_run, force) -> PodcastLinkReport` and
  `link_all(...)`. Related exports:
  - `PodcastLinkOutcome`
  - `PodcastLinkReport`
  - `fetch_website_text`
  - `PLATFORM_APPLE`, `PLATFORM_SPOTIFY` and `PLATFORM_YOUTUBE`
  - `DEFAULT_WINDOW` (200)

### Follows, inbox, imports and listening

- `follower_service.py:FollowerService`, by group:
  - Following: `follow(user_id, podcast_id, *, gated=True)`,
    `follow_best_effort`, `unfollow`.
  - Lookups: `is_following`, `get_followed_podcasts`,
    `get_follower_count`.
  - By slug: `follow_by_slug`, `unfollow_by_slug`, `is_following_by_slug`,
    `get_follower_count_by_slug`.
- Follower errors in `follower_service.py`:
  - `FollowerServiceError`, the base class
  - `AlreadyFollowingError`
  - `NotFollowingError`
  - `PodcastNotFoundError`
- `inbox_service.py:InboxService`, by group:
  - Delivery: `fanout_on_publish`, `seed_on_follow`,
    `backfill_existing_followers`, `deliver_to_user`.
  - State: `mark_state`, `mark_read`.
  - Reads: `list`, `unread_count`, `arriving`, `get_entry`,
    `episode_exists`.
  - Construction: `from_config`.
- `inbox_service.py:enqueue_pipeline_for(queue, pending, *, transcription_provider, source)`:
  the shared "delivering implies processing" enqueue loop.
- `inbox_service.py:tokenize_query`: inbox search tokenizer.
  `MAX_QUERY_CHARS` is 200 and `MAX_QUERY_TOKENS` is 8.
- Inbox errors in `inbox_service.py`:
  - `InboxServiceError`, the base class
  - `InvalidInboxQueryError`
  - `InvalidInboxStateError`
  - `InboxEntryNotFoundError`
- `import_service.py:ImportService.import_url(*, user_id, url) -> ImportResult`.
  `ImportResult.outcome` is `new_episode`, `added_existing` or
  `already_in_inbox`.
- Import resolvers in `import_service.py`, each issuing a canonical id:
  - `ApplePodcastsResolver`: `apple:<id>`
  - `YouTubeResolver`: `youtube:<id>`
  - `SpotifyResolver`: `spotify:<id>`
  - `BareAudioResolver`: `audio:<sha256>`

  The resolvers implement the `Resolver` protocol and return
  `CanonicalSource` (or `CanonicalParent` for the show).
- Import errors in `import_service.py`: `ImportError` (the base class),
  `UnsupportedUrlError` and `ResolverError`.
- `listening_progress_service.py:ListeningProgressService`:
  `record(user_id, episode_id, write, *, measure=False) -> WriteOutcome`,
  `get_summary`, `latest` and `list_in_progress`.
- Listening-progress helpers in `listening_progress_service.py`:
  - `plan_row`
  - `EpisodeLookups`
  - `ListeningProgressError`, `InvalidProgressError` and
    `ListeningEpisodeNotFoundError`
  - `MAX_IN_PROGRESS_LIMIT` (20) and `CONTINUE_LISTENING_WINDOW` (3 days)

### Briefings and email

- `briefing_service.py:BriefingService`:
  - Generation: `from_config`, `generate_for_user`.
  - Reads: `latest_for_user`, `recent_for_user`, `upcoming_for_user`.
  - Read tracking: `mark_listened`, `mark_opened`.
- Briefing types in `briefing_service.py`: `Deferred`, `Upcoming`,
  `BriefingNotFoundError` and `EMPTY_WINDOW_REUSE` (24 h).
- `briefing_renderer.py:BriefingRenderer.render`: writes a briefing's
  `script.md`.
- `briefing_script_generator.py:BriefingScriptGenerator`: `generate(episodes, processing_time_seconds, failures)`
  and `write(content, output_path)`. Related exports:
  - `BriefingEpisodeInfo`, `BriefingScriptStats` and
    `BriefingScriptContent`
  - `SummarySections`, `extract_gist` and `extract_summary_sections`
- `briefing_delivery_service.py:BriefingDeliveryService`: `ensure_pending`
  and `deliver_due`.
- `briefing_email_renderer.py:BriefingEmailRenderer.render -> RenderedEmail`.
- `email_markup.py`: `RenderedEmail`, the inline `STYLE_*` constants,
  `markdown_to_html`, `markdown_to_plain`, `absolutize_links` and
  `inline_html`, shared by the briefing and lifecycle renderers.
- `lifecycle/` (spec #118): `build_lifecycle_email_service(config, repos,
  email_sender, settings)`; `service.py:LifecycleEmailService` with `tick`,
  `plan`, `deliver_due`, `candidates` and `next_message`;
  `renderer.py:LifecycleEmailRenderer`; `messages.py:SEQUENCE` of
  `LifecycleMessage`.
- `email_sender.py`:
  - `EmailSender`, the ABC, with `send(*, to, subject, html, text, headers)`
  - `SmtpEmailSender` and `SesEmailSender`
  - `make_email_sender(config)`
  - `EmailSendError`

### Auth, MCP tokens and OAuth

- `auth_service.py:AuthService`, by group:
  - Users: `get_or_create_default_user`, `get_user_from_token`,
    `get_current_user`, `ensure_active`.
  - Google sign-in: `get_google_auth_url`, `handle_google_callback`
    (async).
  - JWTs: `create_jwt`, `verify_jwt`, `revoke_token`.
  - Profile: `maybe_infer_region` (async), `set_user_region`,
    `set_usage_analytics`.
- Constants in `auth_service.py`: `DEFAULT_USER_NAME`, `GOOGLE_AUTH_URL`,
  `GOOGLE_TOKEN_URL` and `GOOGLE_USERINFO_URL`.
- `mcp_token_service.py:McpTokenService`:
  - Minting and lookup: `create_or_rotate`, `resolve_active`, `get`,
    `touch_last_used`.
  - Revocation and display: `revoke`, `state_of`.
  - Static helpers: `hash_token`, `normalize_scopes`.

  The mint returns `McpTokenMint`.
- `mcp_oauth_service.py:McpOAuthService`, by group:
  - Clients: `get_client`, `register_client`.
  - Consent: `park`, `view_pending`, `approve`, `deny`.
  - Codes and tokens: `load_code`, `exchange_code`, `load_refresh`,
    `exchange_refresh`, `verify_access`, `touch`, `revoke_token`.
  - Grants and cleanup: `list_grants`, `disconnect`, `prune`,
    `housekeeping_jobs`.
- `mcp_oauth_service.py` helpers: `hash_secret`, `client_scope_ceiling`,
  `approved_scopes`, `OAuthRequestError`, and the TTL constants
  `PENDING_TTL` (10 min), `CODE_TTL` (60 s) and `ACCESS_TTL` (1 h).
- `oauth_cimd.py`: `CimdResolver.resolve(url)`, `is_cimd_client_id`,
  `fetch_document`, `validate_document` and `CimdError`.
- `legacy_claim_service.py:LegacyClaimService`: `claim_for_new_user`,
  `claim_by_cli` and `discard_by_cli`.

### Admin curation and transcript editing

- `entity_curation.py:EntityCurationService`, by group:
  - Restructuring: `merge`, `move_mentions`, `split`.
  - Correcting: `relink`, `set_type`.
  - Cleanup: `blacklist`, `prune_orphans`.
  - Mentions: `drop_mention`, `repoint_mention`.
  - Entity edits: `add_alias`, `set_visibility`.
  - Roles: `pin_hosts`, `pin_recurring` (a podcast), `pin_guests` (an
    episode). Unknown entity ids are refused (`not_found`) before writing.
  - `correct(*, actor, queue_manager, wikidata_client, **correction)`: the
    admin correction (`core.entity_review.apply_correction`), audited; the
    API's `POST /api/entities/corrections` calls it.

  The results are `MergeResult`, `MoveResult`, `SplitResult`,
  `RelinkResult`, `SetTypeResult` and `PruneResult`. `merge(keeper_id,
  loser_id, *, actor, reason=None, dry_run=False, keep_name=True)` reports
  `alias_added`, `alias_skipped` and `dropped_aliases` in its
  `MergeResult` besides `moved`.
- `podcast_curation.py:PodcastCurationService(podcasts, audit)`: show
  settings, each audited: `set_show_link(podcast_id, platform, url|None)`
  (curated; validates the platform and the link's shape),
  `set_summary_profile(podcast_id, profile|None)` (validated against
  `PROFILES`), `set_public_pages(podcast_id, public)`.
- `episode_curation.py:EpisodeCurationService`: `find_import_twins` and
  `fold_import_twins(*, actor, apply=False, podcast_id=None)`. Related
  exports: `ImportTwin`, `FoldResult` and `PUB_DATE_TOLERANCE` (1 h).
- `episode_reprocess.py:queue_reprocess(*, repository, queue_manager, config, episode_id, from_stage, refresh_episode_facts=False, requested_by=None, dry_run=False) -> ReprocessQueued`.
  Related exports: `REPROCESS_FROM_STAGES` (`transcribe`, `clean`,
  `summarize`, `entities`) and `ReprocessRejected`, whose `kind` is
  `invalid_stage`, `not_found`, `missing_input` or `busy`.
- `transcript_edit_service.py:TranscriptEditService`, by group:
  - Editing: `get_state`, `apply`, `revert`, `revert_by_actor`, `finish`.
  - Refresh: `sweep_refreshes`.
  - Speakers: `save_as_host`, `search_people`.
  - Operator views: `conflicts`, `speaker_stats`, `is_busy`,
    `local_audio_path`.

  Its error is `TranscriptEditError`, whose `kind` is one of `not_found`,
  `no_transcript`, `busy`, `stale`, `rejected` or `invalid`.
- `role_linker.py`, by group:
  - Linking: `link_podcast_roles`, `link_episode_roles`,
    `backfill_all_roles`.
  - Parsing and filtering: `parse_facts_file`, `tally_speakers`,
    `drop_quiet_guests`, `collect_host_evidence`, `role_context`.
  - Types: `FactsRoles`, `SpeakerTally`, `LinkResult`, `BackfillSummary`.

  It is a parse-and-link bridge from facts files to entity ids and makes
  no LLM calls. `LinkResult.cleared_host_guests` lists stored guest ids
  that `link_episode_roles` removed because they are the show's hosts.

### Sync tracks, indexing and analytics

- `sync_track_service.py:SyncTrackService`, by group:
  - Building: `build_pending`, `build_pending_detailed`.
  - Publishing: `publish`, `publish_if_untracked`.
  - Discarding: `discard_pending`, `discard_pending_generation`.
  - Reading: `usable_generation`, `read_current`, `current`, `load`,
    `enabled`.
- `sync_track_backfill.py`: `backfill_episode`, `stamp_cleaned_transcript`
  and `BackfillResult`. The outcome constants are:
  - `HAS_TRACK`
  - `NO_TRANSCRIPT`
  - `NOT_DALSTON`
  - `WOULD_BUILD`
  - `BUILT`
  - `AUDIO_GONE`
  - `UNSUPPORTED`
  - `FAILED`
  - `BUSY`
  - `SUPERSEDED`
- `indexnow.py:IndexNow`: `enabled`, `submit(paths)` and
  `submit_in_background(paths)`. Its constants are `ENDPOINT` and
  `MAX_URLS` (10 000).
- `analytics_service.py:AnalyticsService`: `available`, `window_for`,
  `previous_window`, `report` and `scorecard`. Related exports:
  - `InvalidRangeError`
  - `ALLOWED_RANGES` (`7d`, `28d`, `90d`)
  - `DEFAULT_RANGE`
- `analytics_sink.py`:
  - Sinks: the `AnalyticsSink` Protocol, `PostHogSink` and `NullSink`.
  - Factory and helper: `make_analytics_sink(config)` and `follow_source`.
  - Event models: `PublicPageView`, `LoginStarted`, `LoginCompleted`,
    `LoginCancelled`, `LoginFailed`, `Search`, `Follow`, `Unfollow`,
    `McpToolCall` and `DriftAlignmentResult` (spec #116, with the
    `DriftOutcome` and `DriftSwState` literals).
  - `FollowSource` values: `episode_page`, `episode_pending_follow`,
    `episode_prompt` (retired, kept for past events), `show_page`,
    `pending_follow`, `top_podcasts`, `add_podcast` and `other`.
    `follow_source` maps any other value to `other`. `PublicKind` includes
    `claude`.

## Invariants

### Gates and quotas

- `import_service.py:ImportService.import_url` calls
  `require_feature("imports")` before it resolves the URL or writes a row.
  Only an import that starts a pipeline is charged to `imports_per_day`:
  a new episode, or an untouched discovered orphan.
- `inbox_service.py:InboxService.deliver_to_user` applies the `imports`
  switch and the `imports_per_day` quota only when the delivery starts a
  pipeline.
- `podcast_service.py:PodcastService.get_or_create_summary_translation`
  always serves a cached translation. Only a newly generated one checks
  `require_feature("translations")` and is charged to
  `translations_per_day`. A per-translation-key `threading.Lock` serialises
  generation.
- `follower_service.py:FollowerService.follow` runs the existence check,
  the gate, the quotas and the insert inside one
  `follow_with_gate` transaction. A unique-constraint race is reported as
  `AlreadyFollowingError`.
- `follower_service.py:FollowerService.follow_best_effort` swallows every
  failure except an `AbusePreventionError`, which it re-raises.
- `podcast_add.py:add_podcast_and_auto_follow` auto-follows only in
  single-user mode, and runs the new-feed precheck before any row is
  written.

### Refresh

- `refresh_service.py:RefreshService.refresh` with `dry_run=True` fetches
  and reports what it would discover but writes nothing: it passes `dry_run`
  to `feed_manager.refresh_feeds`, which skips the batch save
  (`save_refresh_batch`) and the transcript links, and it skips the
  platform-link pass and the pipeline enqueue.

### Inbox, briefings and email

- `inbox_service.py:InboxService.mark_read` only changes `unread` to
  `read`, and is a quiet no-op otherwise. `deliver_to_user` never alters an
  existing inbox row. `fanout_on_publish` is idempotent through
  `insert_many`.
- `briefing_service.py:BriefingService.generate_for_user`:
  - It returns `None` for a disabled account (`ensure_user_active`).
  - It throttles by `min_interval` unless `force` is set.
  - Pending pipeline work defers generation (`Deferred`) until the
    readiness grace deadline.
  - It renders the script before inserting the row, so a render failure
    neither stores a briefing nor advances the cursor.
  - An empty window reuses a latest briefing younger than
    `EMPTY_WINDOW_REUSE`.
- `briefing_delivery_service.py:BriefingDeliveryService.deliver_due`:
  - It claims each delivery before sending it.
  - It sends at most `max_per_pass` deliveries per pass.
  - Permanent failures are parked. These include a missing briefing, an
    email-disabled schedule, a missing user or address, a disabled
    account, and a script that is missing or outside the storage root.
  - Retryable failures back off by `backoff * 2^(attempts-1)` until
    `max_attempts`.
- `lifecycle/service.py:LifecycleEmailService`:
  - `tick` does nothing while the `lifecycle_email_enabled` switch is off.
  - `plan` queues at most one message per user per pass, never an expired
    one, and none inside the frequency cap, within `briefing_quiet` of a
    briefing email, or outside the reader's send window.
  - `candidates` pages by `(created_at, user_id)` keyset, so every
    candidate is reached however many there are.
  - `deliver_due` re-reads the facts before sending; a reader who turned
    tips off, is no longer active, or no longer matches the condition, or
    a message past its expiry, gets the row settled as `skipped`. Outside
    the reader's hours or within `briefing_quiet` of a briefing email, the
    row is deferred to the next allowed time without spending an attempt.
- `email_sender.py:make_email_sender` returns `None` for provider `none`,
  which disables delivery, and raises `ValueError` for an unknown provider.
  The transports never retry.

### Auth, MCP tokens and OAuth

- `auth_service.py:AuthService._validate_config` requires
  `JWT_SECRET_KEY` in every mode. Multi-user mode also requires the
  Google client id and secret.
- `auth_service.py:AuthService.verify_jwt` rejects revoked `jti` values.
- `auth_service.py:AuthService.get_user_from_token` re-reads the user,
  calls `ensure_active`, and rejects tokens issued before
  `tokens_valid_after`.
- `auth_service.py:AuthService.handle_google_callback` runs the signup
  policy only when it creates a new account.
- `mcp_token_service.py:McpTokenService.normalize_scopes` always includes
  `read`, ignores unknown scopes, and drops `pipeline` for non-admins. Only
  the SHA-256 hash and a 6-character prefix of a token are stored.
- `mcp_oauth_service.py:McpOAuthService.load_code`, `load_refresh`,
  `exchange_code` and `exchange_refresh` handle replays this way:
  - A replayed code or refresh token revokes its grant
    (`_revoke_for_reuse`).
  - Grants of inactive users never redeem (`is_user_active`).
  - `park` refuses a `resource` other than this server's MCP endpoint.
- `oauth_cimd.py:_check_url` requires an https `client_id` URL without
  credentials or a fragment, and refuses hosts that resolve to
  non-global addresses.
- `oauth_cimd.py:CimdResolver.resolve` caches documents for at most
  `MAX_CACHE` (24 h). Failed URLs wait out `FAILURE_BACKOFF_SECONDS` before
  another fetch.

### Listening progress and stats

- `listening_progress_service.py:plan_row` validates the `session_id`
  length, a non-negative `seq`, and a finite non-negative position. It
  completes on `ended`, or on `pause`/`stop` within 45 s of the end.
  `listened_at` is set only when `measure` (analytics consent) is true.
- `listening_progress_service.py:ListeningProgressService.record` writes
  through `upsert_if_newer`, which rejects stale writes. Manual events
  force the write.

### Reprocess, transcript editing and roles

- `episode_reprocess.py:queue_reprocess`:
  - It refuses an unknown stage or episode, and a missing input for the
    requested stage.
  - It refuses a busy episode. The idle check and the insert are one
    `add_task_if_episode_idle` transaction.
  - `dry_run` queues nothing.
- `transcript_edit_service.py:TranscriptEditService.apply`:
  - It refuses while a download, downsample, transcribe or clean task is
    active.
  - It refuses a stale `base_seq`.
  - It validates the whole batch before writing anything.
  - Appends use `expected_last_seq` optimistic concurrency.
- `transcript_edit_service.py:TranscriptEditService._queue_refresh` marks
  an episode refreshed only after the reprocess task row exists.
- Edits never rewrite transcript files. Readers replay the op log through
  `core.transcript_edits.load_effective_transcript`
  (`podcast_service.py:PodcastService._load_effective`).
- `role_linker.py:_resolve_existing_person` leaves an ambiguous name
  unlinked rather than guessing between namesakes. `_filter_hosts_by_evidence`
  demotes hosts only when enough evidence files exist, and never demotes
  every host.
- `role_linker.py:link_episode_roles`: when nothing else rewrites the
  episode's guest list and a `podcast_id` is given, it removes the show's
  hosts from the stored guests (`role_linker.py:756-764`). A show's host
  is never left as its own guest.

### Sync tracks

- `sync_track_service.py:SyncTrackService` never raises to callers: each
  public mutator catches every exception and logs `sync_track_failed`.
- `publish` treats a pending track as the transcript's own only when its
  generation matches `expected_generation`:
  - On a generation mismatch it logs `sync_track_pending_superseded` and
    publishes nothing from it, but leaves the pending row and its files in
    place (`sync_track_service.py:189-195`).
  - On an audio-duration mismatch (against `transcribed_duration_s`, or
    shorter than `transcript_end_s`) it logs
    `sync_track_transcript_mismatch`, retires the pending row and deletes
    its files (`sync_track_service.py:196-215`).
- `publish_if_untracked` never replaces a current track.

### Curation and platform links

- `entity_curation.py:EntityCurationService` refuses a merge or move onto
  the same id, a merge across types, a malformed QID, and a QID already
  held by another entity. A dry run writes nothing and records no audit
  row.
- `entity_curation.py:EntityCurationService.merge` keeps the loser's name
  as a keeper alias only when `keep_name` is set, the keeper does not
  already answer to it, and `core.entity_alias_hygiene.is_alias_of` accepts
  it. A name that fails `is_alias_of` is reported in `alias_skipped` and not
  stored. The loser's own aliases are never carried over
  (`dropped_aliases`). The alias is upserted after the references are
  repointed and before the loser is deleted.
- `episode_curation.py:EpisodeCurationService.find_import_twins` folds only
  pairs whose publish dates are within `PUB_DATE_TOLERANCE` and that pair
  exactly once.
- `platform_link_service.py:PlatformLinkService.link_podcast` never raises
  for network or data errors, and a dry run writes nothing.
  `_store_show_url` writes through `set_podcast_platform_url(..., source=...)`.
  When that call reports no write, the curated value is kept and
  `platform_links_show_kept_curated` is logged.

### Analytics and indexing

- `analytics_sink.py:PostHogSink.capture` never raises. It drops an
  identified event when GPC is set or the user opted out, and anonymous
  events skip person profiles. `make_analytics_sink` returns `NullSink`
  unless multi-user mode, analytics events and a PostHog key are all
  present.
- `analytics_sink.py:_Event` models are frozen and use `extra="forbid"`.
- `indexnow.py:IndexNow.submit` never raises. It is enabled only with a
  key, a base URL, multi-user mode and public pages.

### Audio addresses

- `audio_address.py:AudioAddressResolver._resolve` rewrites every hop to
  `https` (dropping an explicit `:80`), calls `validate_public_url` on each
  hop before requesting it, and follows at most `MAX_HOPS` (8) redirects
  within `DEADLINE_S` (10 s). Each request sends only `USER_AGENT` and
  `Range: bytes=0-0`, never anything about the listener. It returns the
  address that answers 200 or 206, and `None` on any other status, an
  unsafe URL or a `requests` error.
- `playable_url` caches per `(enclosure, listener)`: a resolved address
  for its signed lifetime (`Expires`, `X-Amz-*` or `X-Goog-*`) less a
  margin, capped at `MAX_TTL_S` (4 h), or `DEFAULT_TTL_S` (300 s) when it
  states none; a failure for `FAILURE_TTL_S` (15 s). Only one walk per key
  runs at a time; concurrent callers wait for it. The cache holds at most
  `CACHE_MAX_ENTRIES` (512), evicting expired entries first, then the
  oldest.

## Side Effects

### Database

- Writes through the repositories:
  - podcasts: `podcast_service.py:PodcastService.add_podcast` and
    `remove_podcast`, through `PodcastFeedManager.add_podcast` /
    `remove_podcast`
  - discovered episodes and refresh state:
    `refresh_service.py:RefreshService.refresh`, through
    `feed_manager.refresh_feeds` (also on a dry run, see Invariants)
  - follows: `follower_service.py:FollowerService.follow`
  - inbox: `inbox_service.py:InboxService` (`insert_many`,
    `find_or_create`, `update_state`, `mark_read_if_unread`)
  - imported episodes and auto-added parent podcasts:
    `import_service.py:ImportService._find_or_create_episode`
  - briefings: `briefing_service.py:BriefingService.generate_for_user`,
    `mark_listened` and `mark_opened`
  - deliveries: `briefing_delivery_service.py:BriefingDeliveryService`
    (`claim`, `mark_sent`, `mark_retry`, `mark_failed`)
  - listening progress: `listening_progress_service.py:ListeningProgressService.record`
  - users, regions and revoked tokens: `auth_service.py:AuthService`
  - MCP tokens: `mcp_token_service.py:McpTokenService`
  - OAuth clients, pending requests, codes, tokens and grants:
    `mcp_oauth_service.py:McpOAuthService` and
    `oauth_cimd.py:CimdResolver.resolve`
  - platform links: `platform_link_service.py:PlatformLinkService`, and
    best-effort per refreshed podcast from
    `refresh_service.py:RefreshService.refresh` (not on a dry run; a
    failure logs `platform_link_resolution_failed`)
  - entity, host, guest and recurring role columns:
    `entity_curation.py:EntityCurationService` and
    `role_linker.py:link_podcast_roles` / `link_episode_roles`
    (including clearing show hosts from stored guests)
  - keeper aliases: `entity_curation.py:EntityCurationService.merge`
    (`upsert_entity` with the loser's name)
  - the transcript edit op log:
    `transcript_edit_service.py:TranscriptEditService.apply`
  - sync-track rows: `sync_track_service.py:SyncTrackService`
  - lazy summary-preview backfill: `podcast_service.py:resolve_summary_preview`
  - import-twin folds: `episode_curation.py:EpisodeCurationService`
  - legacy account claims: `legacy_claim_service.py:LegacyClaimService`
- Audit rows go through `abuse.AuditLog.record`:
  - `entity.*`, `mention.drop`, `mention.repoint`, `podcast.set_hosts`,
    `podcast.set_recurring` and `episode.set_guests` from
    `entity_curation.py:EntityCurationService` (`entity.merge` also records
    `alias_added`, `alias_skipped` and `dropped_aliases`)
  - `podcast.set_show_link`, `podcast.set_summary_profile` and
    `podcast.set_public_pages` from `podcast_curation.py:PodcastCurationService`
  - `episode.fold_import_twin` from
    `episode_curation.py:EpisodeCurationService.fold_import_twins`
  - `transcript.edit_session`, `transcript.revert_bulk` and
    `facts.add_host` from
    `transcript_edit_service.py:TranscriptEditService`

### Task queue

- Full-pipeline enqueues:
  - `import_service.py:ImportService.import_url` calls
    `enqueue_full_pipeline`.
  - `inbox_service.py:enqueue_pipeline_for` serves inbox delivery and
    imports.
  - `refresh_service.py:RefreshService.refresh` calls
    `enqueue_discovered_episodes`.
- `refresh_on_open.py:RefreshOnOpenService.maybe_trigger` calls
  `add_feed_task` for `REFRESH_FEED` at priority 10, or promotes a pending
  task.
- `episode_reprocess.py:queue_reprocess` calls
  `add_task_if_episode_idle`, which transcript-edit refreshes reuse.

### Storage (local or S3)

- `podcast_service.py:PodcastService` reads transcripts and summaries, and
  writes translations, citation sidecars and summary manifests.
- `briefing_script_generator.py:BriefingScriptGenerator.write` and
  `briefing_renderer.py:BriefingRenderer.render` write `script.md`.
  Summary reads fan out over a `ThreadPoolExecutor` of up to 8 workers.
- `sync_track_service.py:SyncTrackService.build_pending_detailed` writes
  sync-track bytes, and `_delete_files` deletes retired tracks.
- `sync_track_backfill.py:stamp_cleaned_transcript` rewrites the
  cleaned-transcript sidecar.
- `transcript_edit_service.py:TranscriptEditService.save_as_host` saves
  podcast facts through `FactsManager`.
- `stats_service.py:StatsService.get_stats` globs the local audio
  directory.

### Network

- RSS feeds: `refresh_service.py:RefreshService.refresh` (through
  `feed_manager.refresh_feeds`) and `podcast_service.py:PodcastService.add_podcast`.
- Enclosure redirect hops (one-byte range GETs, no redirects followed by
  `requests`): `audio_address.py:AudioAddressResolver.playable_url`.
- Google OAuth token and userinfo calls:
  `auth_service.py:AuthService.handle_google_callback`.
- GeoIP lookup: `auth_service.py:AuthService.maybe_infer_region`.
- Import resolvers:
  - iTunes lookup and Apple pages: `import_service.py:_guarded_get`, which
    goes through the SSRF-guarded `guarded_session`
  - yt-dlp metadata: `import_service.py:_default_youtube_metadata_fetch`
  - Spotify: `import_service.py:SpotifyResolver`, through
    `core.spotify_resolver.SpotifyLinkResolver`
- Platform links: iTunes, YouTube listings and search, Spotify, and
  publisher websites (`platform_link_service.py:PlatformLinkService`,
  `fetch_website_text`).
- `oauth_cimd.py:fetch_document` fetches CIMD documents with httpx (64 KB
  cap, 8 s deadline).
- `indexnow.py:IndexNow.submit` posts to `https://api.indexnow.org/indexnow`.
  `submit_in_background` does so on a daemon thread.
- Email:
  - `email_sender.py:SmtpEmailSender.send` uses SMTP.
  - `email_sender.py:SesEmailSender.send` uses AWS SES `send_raw_email`.
- `analytics_sink.py:PostHogSink` uses the PostHog SDK with a background
  queue.
- `sync_track_backfill.py:_job_audio` downloads Dalston job audio.

### LLM

- Summary translation runs through `SummaryTranslator` in
  `podcast_service.py:PodcastService.get_or_create_summary_translation`.
- Narration LLM calls happen in [`./narration/EGM.md`](./narration/EGM.md).

### In-process state

- `podcast_service.py:PodcastService` keeps per-key translation locks.
- `briefing_service.py:BriefingService._lazy_deferrals` is a dict guarded
  by a `threading.Lock`.
- `oauth_cimd.py:CimdResolver._failed` is a backoff map guarded by a
  `threading.Lock`.
- `audio_address.py:resolver` holds the per-listener address cache and the
  in-flight walk map, guarded by a `threading.Lock`. The listener key is
  never sent anywhere.

## Verification

- Run: `./venv/bin/pytest tests/unit/services`
- Full suite: `make test`

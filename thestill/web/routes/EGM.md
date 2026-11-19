# thestill.web.routes

## Overview

These are the FastAPI routers for the web server. They cover the
liveness/readiness probes, signed unsubscribe links, ElevenLabs webhooks,
Google sign-in, the signed-in `/api/*` surface used by the React SPA, the
operator/admin consoles and the anonymous `/api/public/*` read API. Each
module defines an `APIRouter`. `thestill/web/app.py:create_app` mounts them
and decides each prefix and router-level guard; the routes themselves
declare only the path suffix. Handlers stay thin: they call services and
repositories from `thestill/web/dependencies.py:AppState`, and wrap
responses with `thestill/web/responses.py` (`api_response`,
`paginated_response`, `etag_json_response`, `not_found`, ...).

"session" below means the router-level dependencies
`[require_auth, enforce_read_only, user_rate_limit]` (`app.py:create_app`,
`require_session`).

## Interface

- `__init__.py` re-exports the modules `health`, `webhooks`, `auth`,
  `api_status`, `api_dashboard`, `api_podcasts`, `api_episodes`,
  `api_commands`, `api_top_podcasts` and `unsubscribe`. `app.py` imports the
  other modules directly.
- Routers:
  - Most modules define one `router`.
  - `api_commands.py:admin_router` is `APIRouter(dependencies=[Depends(require_admin)])`.
  - `webhooks.py:admin_router` adds `require_admin` on top of the webhook rate limit.
  - `api_oauth.py` defines `pending_router` and `grants_router`.
  - `api_public.py:router` is `APIRouter(dependencies=[Depends(public_rate_limit)])`.
  - `auth.py:router` has the dependency `rate_limit_dependency(AUTH_LIMIT, "auth")`.
- Helpers other modules import:
  - `api_public.py`: `public_entity_payload`, `current_entity_path`, `stale_entity_path`,
    `shell_visitor_country` and the other `public_*_payload` builders, used by
    `thestill/web/public_shell.py` and `public_prerender.py`;
  - `api_entities.py`: `url_candidates`, `url_owner`, `entity_page_path`,
    `entity_path_resolver` and `get_episode_entities`;
  - `api_commands.py:_get_starting_stage`, used by `api_episodes.py`;
  - `api_podcasts.py:DRIFT_OUTCOME_MAX_BYTES` (4096), used by `app.py:create_app` as the
    `/drift-outcome` suffix limit of `BodySizeLimitMiddleware`.
- `auth.py:safe_next_path` is public but used only inside `auth.py` (`google_login`,
  `auth.py:284`, and `google_callback`, `auth.py:386`) and by its tests.
- Request/response models are Pydantic classes declared next to their handlers. Examples:
  - `api_commands.py`: `RunPipelineRequest`, `DLQTaskResponse`
  - `api_episodes.py`: `EpisodeReprocessRequest`
  - `api_briefings.py`: `ScheduleUpdateRequest`
  - `api_search.py`: `SearchResponse`, `QuickSearchResponse`
  - `api_admin.py`: `EntityMergeRequest` (`keep_name`, default `True`, keeps the
    loser's name as an alias of the keeper)
  - `api_podcasts.py`: `DriftOutcomeRequest` and `DriftSpan` (both `extra="forbid"`)
  - `api_entities.py`: `EpisodeEntity`
  - `api_transcript_edits.py`: `ApplyEditsRequest`
  - `api_me_listening.py`: `ProgressWriteRequest`
  - `api_public.py`: `PageViewRequest`

### Routes and their guards

Every route (method, path, purpose) is documented in
[docs/web-server.md](../../../docs/web-server.md), which
`tests/docs/test_api_route_drift.py` checks against the mounted FastAPI app in
both directions. This contract does not repeat that list. It records which
guard each router gets when `thestill/web/app.py:create_app` mounts it:

- `require_session` (`[require_auth, enforce_read_only, user_rate_limit]`):
  `api_podcasts`, `api_transcript_words`, `api_top_podcasts`, `api_episodes`,
  `api_transcript_edits`, `api_entities` (prefix `/api`), `api_inbox`,
  `api_briefings`, `api_narrations`, `api_imports`, `api_me_listening`,
  `api_me_mcp_token`, `api_oauth.pending_router`, `api_oauth.grants_router`,
  `api_commands.router`.
- `require_session` plus a router-level `require_admin`:
  `api_commands.admin_router`.
- `require_session` plus `user_search_rate_limit`: `api_search`.
- `require_operator` (`[require_admin]` only): `api_status`, `api_dashboard`,
  `api_admin`. See the Known issue under Invariants.
- `rate_limit_dependency(AUTH_LIMIT, "auth")`, no session: `auth`.
- `public_rate_limit`, no session, mounted only when
  `config.multi_user and config.public_pages_enabled`: `api_public`.
- No session: `health`; `unsubscribe` (HMAC-signed `token`); `webhooks.router`
  (`WEBHOOK_LIMIT` plus the HMAC signature check in the handler);
  `webhooks.admin_router` adds `require_admin`.

Per-route guards on top of the router guard:

- `require_admin` on every handler in `api_episodes.py`, and on
  `get_review_queue` and `post_correction` in `api_entities.py`.
- `require_permission(TRANSCRIPT_EDIT)` (`_EDITOR`) on every handler in
  `api_transcript_edits.py`.
- `api_admin.py` write handlers take `admin = Depends(require_admin)` to get the
  acting admin for `Actor.web(admin.id)`, except `send_test_alert`.

## Invariants

### Authentication and ownership

- **Default deny.** Every `/api` router except `auth.py:router`, `api_public.py:router`
  and the operator routers `api_admin`, `api_dashboard` and `api_status` gets the
  session guards when `app.py:create_app` mounts it. The operator routers get only
  `require_operator = [Depends(require_admin)]` (`app.py:879`), which still
  authenticates. A new router added to that block needs a session unless someone opts
  it out on purpose.
- Known issue: `api_admin`, `api_dashboard` and `api_status` skip `enforce_read_only`
  and `user_rate_limit` (`app.py:881`, `:898`, `:901`), so their calls are never counted
  against the per-user budget.
- **Admin routes.** Admin-only routes use `require_admin` at the router level
  (`api_admin`, `api_dashboard`, `api_status`, `api_commands.admin_router`,
  `webhooks.admin_router`) or per route:
  - `api_episodes.py`: all six handlers;
  - `api_entities.py`: `get_review_queue` and `post_correction`;
  - `api_admin.py`: every write handler except `send_test_alert`, to get the acting
    admin for `Actor.web(admin.id)`. `send_test_alert` (`api_admin.py:266-269`) relies on
    the router-level guard and takes no `Actor`.
- **Transcript edits.** The transcript editor uses
  `api_transcript_edits.py:_EDITOR = Depends(require_permission(TRANSCRIPT_EDIT))`,
  never `require_admin` directly.
- **Ownership returns 404, not 403**, so ids cannot be enumerated:
  - `api_briefings.py:_require_owned`;
  - `api_narrations.py:_resolve_owned_briefing`, used by `get_narration` and
    `get_narration_script`;
  - `api_oauth.py:disconnect_grant`.
  `api_inbox.py`, `api_me_listening.py`, `api_imports.py` and `api_me_mcp_token.py`
  scope every service call to `user.id`.
- **Task status is readable by its owner.** In `api_commands.py`:
  - In-memory command tasks (add podcast, refresh) record who started them
    (`task_manager.py:Task.user_id`). `get_add_podcast_status`,
    `get_refresh_status` and `get_all_tasks_status` show a task only to that
    user or an admin (`_can_read_command_task`). Without a `task_id`,
    `get_add_podcast_status` returns the caller's own latest add; another
    user's add answers 404 by id and is left out of the 409 detail of
    `add_podcast`.
  - Pipeline queue tasks have no single initiator, so `get_queued_task_status`,
    `stream_task_progress` and `get_current_progress` serve a task only to an
    admin or to a user who has its episode in their library (follows its
    podcast or holds it in the inbox; a feed task needs a follow). Anyone else
    gets the same 404 as an unknown id (`_readable_queue_task`).
    `get_episode_tasks` returns an empty list instead of a 404, because the
    episode reader polls it for every viewer.
- **Reads by slug are open.** `api_podcasts.py` reads by slug (`get_podcast`,
  `get_episode_by_slugs`, `.../transcript`, `.../summary`, `.../sync-track/...`,
  `.../audio`) and `api_transcript_words.py:get_episode_transcript_words` have no follow
  or ownership check; neither does `report_drift_outcome`. `api_podcasts.py:get_podcasts`
  lists only the caller's followed podcasts
  (`follower_repository.get_followed_podcast_ids`). `get_episode_by_slugs` returns
  `podcast_is_following` for the caller and `podcast_followable`, which is false for the
  synthetic audio-imports parent (`SYNTHETIC_AUDIO_IMPORTS_ID`).
- **`failure_reason` is admin-only.** `api_podcasts.py:_visible_failure_reason` hides it
  from non-admins (`is_effective_admin`). The admin-only `api_episodes.py:list_failed_episodes`
  and `get_episode_failure` return it in full.

### Sign-in, webhooks and signed links

- **`auth.py` (Google sign-in):**
  - `google_callback` checks that the `oauth_state` cookie equals the `state` query
    param (CSRF). A callback for a stale attempt leaves the newer attempt's cookies alone.
  - `safe_next_path` accepts only same-origin paths of at most 512 characters
    (`_NEXT_MAX_LENGTH`). It rejects `//`, `/\`, backslashes and control characters,
    and, on the path without its query or fragment, exactly `/login`, `/login/...` and
    `/api/...` (`auth.py:242`). `/login-help` passes.
  - `_get_redirect_uri` trusts `X-Forwarded-Proto` and `X-Forwarded-Host` only from a
    trusted proxy. Otherwise it uses `config.public_base_url` and fails closed with a
    500 when that is unset.
  - `_set_secure_cookie` always sets `httponly`. The auth cookie is `samesite=strict`.
  - `google_login` and `google_callback` return 400 in single-user mode.
- **`webhooks.py` (ElevenLabs):**
  - `_process_elevenlabs_webhook` fails closed with a 401 when no secret is set, unless
    `DEV_ALLOW_UNSIGNED_WEBHOOKS=1`.
  - `_verify_signature` uses HMAC-SHA256 over `"<ts>.<body>"` with `hmac.compare_digest`,
    and allows at most 300 s of clock skew (`_MAX_WEBHOOK_CLOCK_SKEW_SECONDS`).
  - Processing errors are reported inside a 200 response.
- **`unsubscribe.py`:** `unsubscribe_briefings` (GET) never changes anything, so mail
  scanners that prefetch the link are harmless. `unsubscribe_briefings_one_click` (POST)
  is idempotent and shows the same success page whether or not a row changed. The only
  credential is the HMAC token checked in `_verify`. `unsubscribe_tips` /
  `unsubscribe_tips_one_click` (spec #118) follow the same rules for "Product tips by
  email" and accept only `TIPS_PURPOSE` tokens.

### Public API (`api_public.py`)

- **Mounting.** The router is mounted only when
  `config.multi_user and config.public_pages_enabled` (`app.py:create_app`).
- **Allowlisted fields.** Every response is built field by field from an allowlist and
  must never leak pipeline state, storage paths, feed URLs or failure details (module
  docstring).
- **Nothing is spent.** The module makes no LLM calls and enqueues nothing. Its only
  write is the page-view counter `_count_public_view`.
- **Summaries and transcripts.** No transcript is served: `get_public_episode` exposes
  only `has_transcript`. `_summary_lead` returns only the gist and key-takeaways sections.
- **Checks before any query.** `_check_slug` requires `^[a-z0-9][a-z0-9-]{0,110}$`, and
  entity types are limited to `person|company|product|topic`.
- **Hidden content.** Hidden podcasts, episodes and entities return 404 or are filtered
  out of lists, through `thestill/web/public_access.py:PublicVisibility`.
- **Re-typed entities.** `current_entity_path` and `stale_entity_path` give the target
  for a 301 when an entity's type changed. The caller performs the redirect.
- **Cache headers.** Every GET calls `public_access.py:apply_public_cache_headers`.
  `list_public_top_podcasts` switches to `private, max-age=300` when the region was
  chosen from the visitor's IP.

### Commands queue (`api_commands.py`)

- `refresh_feeds` and `add_podcast` return 409 while a task of the same `TaskType` is
  running (`task_manager.start_task` returns `None`), so only one of each runs at a time
  across the server.
- `_validate_episode_for_stage` returns:
  - 404 for an unknown slug;
  - 400 when the episode is not in the stage's required `EpisodeState`;
  - 409 when `queue_manager.has_pending_task` finds the stage already queued.
  `run_pipeline` follows the same rules and also requires `target_state` to come after
  the episode's current state.
- `retry_dlq_task` and `skip_dlq_task` work only on `DEAD` or `FAILED` tasks.
  `retry_dlq_task` returns 409 if the stage is already pending. `retry_all_dlq_tasks`
  only logs a warning for `_DB_OPERATIONAL_ERRORS` hit while clearing failure banners;
  it does not return a 500.
- `stream_task_progress` (SSE) ends after a `completed` or `failed` event. It sends
  `: keepalive` every 30 s and polls the database while the task is silent.

### Inbox, briefings, listening and search

- **Inbox.** No inbox route deletes a row (`api_inbox.py`).
  - `mark_inbox_read` returns `marked:false`, not 404, when there is no row.
  - `send_to_inbox` returns 201 when it creates the row, or 200 with the existing row
    unchanged.
  - `set_inbox_state` returns 400 on `InvalidInboxStateError`.
- **Briefings and listening.** Static paths are declared before `/{id}` paths, so the
  literal path wins (`api_briefings.py` `/schedule`, `api_me_listening.py` `/latest`).
  `api_me_listening.py:put_listening` returns `accepted:false` for a write with a lower
  `seq`.
- **Search.**
  - `search_corpus` and `search_quick` return 503 when `state.search_backend` is `None`.
  - When `sentence_transformers` is missing, `search_corpus` falls back to
    `SearchMode.LEXICAL` and reports the mode it actually used.
  - The quote group in `search_quick` is always lexical.

### Episode audio, drift reports and entities

- **Audio address.** `api_podcasts.py:get_episode_audio` answers 302 to the address
  `services.audio_address.resolver.playable_url(audio_url, listener=user.id)` returns,
  with `Cache-Control: no-store`. An `https` enclosure is returned as it is; an `http`
  one is resolved to an `https` address. It returns 502 when no `https` address can be
  reached, and 404 for an unknown slug. The audio itself is never proxied.
- **Drift reports.** `api_podcasts.py:report_drift_outcome` changes nothing and returns
  `{"accepted": true}`.
  - `DriftOutcomeRequest` forbids extra fields and bounds every field. `audio_host` is a
    host name only (`^[A-Za-z0-9.-]+$`), and nothing in the body identifies the
    listener.
  - A report for a generation that is not the episode's current sync track is still
    logged, with `generation_current=false` (`sync_track_service.usable_generation`).
  - The `drift_map_reported` event is logged at error level when the report shows a
    verification bug: an `aligned` outcome whose checked ranges did not all match, or
    `applied` without both checks passing. Otherwise it is logged at info.
- **Episode entities.** `api_entities.py:get_episode_entities` sorts guests first, then
  hosts, recurring and mentioned-only (`kind_priority`), each by salience. Its
  `EpisodeEntity.takes_part` is true for a guest, and for a host or recurring person only
  when one of their mentions is `speaking`. `api_public.py:_episode_quote_set` passes it
  on as each person's `role` (`None` when they do not take part).

### Other

- `api_transcript_edits.py:_STATUS` maps `TranscriptEditError` kinds to status codes:

  | Kind | Status |
  | --- | --- |
  | `not_found` | 404 |
  | `no_transcript` | 400 |
  | `busy` | 409 |
  | `stale` | 409 |
  | `rejected` | 409 |
  | `invalid` | 422 |

  `_refused` renames `stale` to `stale_base_seq` and `rejected` to `edit_rejected`.
- `api_admin.py:_abuse` returns 503 when `state.abuse` is `None`, which also covers
  entity curation. Refusals from the services (acting on yourself, demoting the last
  admin, curation conflicts) become 409 through the shared handler in
  `thestill/web/error_handlers.py`.
- `api_me_mcp_token.py:create_or_rotate_mcp_token` returns the plaintext token only in
  its POST response. It returns 400 unless `config.mcp_http_enabled` is set and the
  request comes over a secure origin (`_issuing_over_secure_origin`). `_token_view`
  exposes only the token prefix.
- `health.py:readiness_check` returns 503 with no details unless both
  `HealthService.ping_database` and `check_worker` pass.

## Side Effects

- **Database writes go through services**, one service per area:

  | Area | Calls |
  | --- | --- |
  | Follows | `follower_service.follow_by_slug` and `unfollow_by_slug` (`api_podcasts.py`); `follow_best_effort` (`api_commands.py:run_add_podcast_task`) |
  | Inbox | `InboxService.mark_read`, `mark_state` and `deliver_to_user` |
  | Listening | `ListeningProgressService.record` |
  | Briefings | `briefing_schedule_repository.upsert` (`put_briefing_schedule`; `_ensure_default_schedule` on `GET /api/briefings/latest`); `briefing_service.mark_opened` and `mark_listened` |
  | Imports | `ImportService.import_url` |
  | Transcript edits | `TranscriptEditService.apply`, `revert`, `finish` and `save_as_host` |
  | Admin | `account_admin.*`, `settings.set` / `reset` and `EntityCurationService.*` (`merge` passes `keep_name`), all audited (`api_admin.py`) |
  | MCP and OAuth | `mcp_token_service.create_or_rotate` / `revoke`; `McpOAuthService.approve`, `deny` and `disconnect` |
  | Auth | `auth_service.revoke_token`, `set_user_region`, `set_usage_analytics` and `maybe_infer_region` |
  | Unsubscribe | `briefing_schedule_repository.set_email_enabled(user_id, False)` |

- **Queue enqueues** call `queue_manager.add_task`:
  - `api_commands.py`: `queue_*` and `run_pipeline`;
  - `api_episodes.py`: `bulk_process_episodes` and `retry_failed_episode`
    (`retry_failed_episode` first calls `repository.clear_episode_failure`).
  Other queue operations:
  - `services.episode_reprocess.queue_reprocess` (`api_episodes.py:reprocess_episode`);
  - `core.entity_review.apply_correction` (`api_entities.py:post_correction`), which also
    calls Wikidata through `WikidataClient`;
  - `refresh_on_open.maybe_trigger` (`api_podcasts.py:resolve_podcast` and `get_podcast`);
  - `queue_manager.bump_task`, `cancel_task`, `fail_task`, `retry_dead_task(s)` and
    `complete_task` (`api_commands.py`).
- **Background threads.** `api_commands.py:refresh_feeds` and `add_podcast` start daemon
  `threading.Thread`s running `run_refresh_task` and `run_add_podcast_task`. These update
  the in-memory `thestill/web/task_manager.py:TaskManager`.
- **LLM calls inside requests:**
  - `api_podcasts.py:get_episode_summary_by_slugs` may detect and record the summary
    language and build a translation synchronously
    (`podcast_service.get_or_create_summary_translation`).
  - `api_briefings.py:get_latest_briefing` may call `briefing_service.generate_for_user`.
  - `api_briefings.py:narrate_briefing` runs `narration_runner.run` synchronously and
    writes `<briefing_id>-<slug>.json` and `.md`.
- **Filesystem:**
  - `webhooks.py:_save_webhook_result` writes `webhook_data/elevenlabs_<id>.json` with
    mode 0600, and `delete_webhook_result` unlinks it.
  - `webhooks.py` also calls `WebhookTranscriptProcessor`, which writes the raw transcript.
  - Narration and script reads: `api_briefings.py`, `api_narrations.py` and
    `api_dashboard.py:get_narration_dashboard`.
  - `api_transcript_edits.py:get_editor_audio` streams local audio.
- **Cookies.** `auth.py:google_login` sets `oauth_state` and `oauth_next`.
  `google_callback` sets `auth_token` (a JWT valid for 30 days) and deletes the
  `oauth_*` cookies. `logout` deletes `auth_token`.
- **Analytics** go through `thestill/web/analytics_request.py:capture_event`:
  - `api_podcasts.py`: `Follow` and `Unfollow`;
  - `api_commands.py:add_podcast`: `Follow`;
  - `api_search.py:search_corpus`: `Search`, without the query text;
  - `auth.py`: `LoginStarted`, `LoginCompleted`, `LoginCancelled` and `LoginFailed`;
  - `api_public.py:public_page_view`: `PublicPageView` (browsers only), plus
    `public_page_view_repository.increment`;
  - `api_podcasts.py:report_drift_outcome`: `DriftAlignmentResult`, a summary of the
    report. The full report, spans included, goes only to the `drift_map_reported`
    log event.
- **Outbound HTTP.** `api_podcasts.py:get_episode_audio`, for an `http` enclosure, has
  `services.audio_address.resolver` follow the redirect chain from the server over
  `https` (one-byte range requests, `validate_public_url` on every hop). The answer is
  kept in the resolver's in-process cache, keyed on the URL and the listener.
- **In-process caches:**
  - `api_public.py`: many `TtlCache`s keyed on the visibility fingerprint, plus
    `_visitor_country_cache`, which calls `lookup_country_from_ip` when it misses;
  - `api_dashboard.py:_analytics_cache`: 600 s.
- **SSE.** `api_commands.py:stream_task_progress` subscribes to
  `progress_store.subscribe(task_id)` and unsubscribes in `finally`.

## Verification

- Run: `./venv/bin/pytest tests/unit/web tests/integration/web` (full suite: `make test`)

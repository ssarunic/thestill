# abuse

## Overview

Abuse prevention and user administration (spec #104). The package holds:

- the kill switches: account status, the signup gate, and runtime feature
  switches
- the follow gate and the per-user quotas, kept in a usage ledger
- operator alerts
- the audit log
- the account-admin actions that the `thestill user` / `admin` / `settings`
  CLI commands and the admin API share

`wiring.py:build_abuse_services` is the one constructor that the web app,
the CLI and the MCP server use. The package `__init__.py` is kept
import-light on purpose, because `services/__init__` imports the follower
service, which imports `abuse.errors`.

## Interface

- Wiring:
  - `wiring.py:build_abuse_services(config, repos, *, email_sender=None, clock=...) -> AbuseServices`
  - `wiring.py:AbuseServices`: a bundle of `settings`, `audit`, `guard`,
    `signup`, `follow_policy`, `new_feed_precheck`, `account_admin`,
    `quota` and `alerts`.
- Guards:
  - `access_guard.py:AccessGuard`: `ensure_active(user)`,
    `ensure_user_active(user_id)` and `require_feature(feature)`.
  - `access_guard.py:NULL_GUARD`: allows everything; services and tests use
    it by default.
- Actors:
  - `actor.py:Actor`: has `web(user_id)`, `cli()` and `system(name)`.
  - `actor.py:ActorKind`: `web`, `cli` or `system`.
- `account_admin_service.py:AccountAdminService` methods, by group:
  - Reads: `list_users`, `count_users`, `audit_page`, `footprint`, `detail`,
    `resolve_user`.
  - Account status: `suspend`, `ban`, `reinstate`, `set_status`,
    `logout_everywhere`, `remove_introduced_feeds`.
  - Quotas and admin rights: `set_quota_override`,
    `replace_quota_overrides`, `set_admin`, `grant_admin`, `revoke_admin`.
- Account-admin result types in `account_admin_service.py`:
  - `UserFootprint`
  - `FollowInfo`
  - `UserDetail`
  - `BanResult`
  - `ReinstateResult`
  - `RemovedFeeds`
- Account-admin helpers in `account_admin_service.py`:
  - `QueuedTaskCanceller`, a Protocol
  - `revocation_instant(now)`
- Audit:
  - `audit_service.py:AuditLog`: `record(actor, action, target_user_id, **details)`,
    `entries(...)` and `count(...)`.
  - `audit_service.py:AUDIT_ACTIONS`: the allowed actions, in groups:
    - `user.*`
    - `admin.grant` and `admin.revoke`
    - `setting.set`
    - `entity.*` and `mention.drop` / `mention.repoint`
    - `podcast.set_hosts`, `podcast.set_recurring`, `podcast.set_show_link`,
      `podcast.set_summary_profile`, `podcast.set_public_pages` and
      `episode.set_guests`
    - `transcript.edit_session` and `transcript.revert_bulk`
    - `facts.add_host`
    - `episode.fold_import_twin`
- Errors in `errors.py` (status code and `error` code):
  - `AbusePreventionError`: the base class, 400.
  - `AccountDisabledError`: 403, `account_disabled`.
  - `FeaturePausedError`: 503, `feature_paused`.
  - `ReadOnlyError`: 503, `read_only`.
  - `QuotaExceededError`: 429, `quota_exceeded`. `headers()` sets
    `Retry-After` only when `retry_after_seconds` is set
    (`errors.py:121-122`); it is `None` for a quota without a window,
    such as `follows_total` (`quota_service.py:162`).
  - `SignupRefusedError`: 403, `signups_closed`.
  - `AdminActionError`: 409. Account admin
    (`account_admin_service.py`) raises these codes:
    - `cannot_modify_self`
    - `last_admin`
    - `not_found`
    - `single_user_mode`
    - `reason_required`
    - `unavailable`
    - `invalid_quota`

    Entity curation (`services/entity_curation.py`) reuses the class with
    `not_found`, `same_entity`, `type_mismatch`, `invalid_qid`,
    `qid_taken`, `exists`, `unchanged`, `no_mentions` and `no_surface`.
- `errors.py:FEATURE_PAUSED_MESSAGES`: the user-facing message for each
  paused feature.
- Runtime settings:
  - `settings_service.py:SettingsService` methods: `effective`, `get`,
    `all_effective`, `non_default`, `refusal`, `allowed`, `require`, `set`,
    `modify`, `reset`, `invalidate`.
  - `settings_service.py:EffectiveSetting` and
    `settings_service.py:DEFAULT_TTL_SECONDS` (15 s).
- Settings registry:
  - `settings_registry.py:SETTINGS` holds these keys:
    - `signup_mode`
    - `signup_allowlist`
    - `new_feeds_enabled`
    - `imports_enabled`
    - `translations_enabled`
    - `narration_enabled`
    - `read_only`
    - `public_prerender_enabled`
    - `entity_noindex`
    - `quota_overrides`
    - `alerts`
  - Other exports:
    - `settings_registry.py:SettingSpec`
    - `settings_registry.py:get_spec`
    - `settings_registry.py:SIGNUP_MODES`
    - `settings_registry.py:FEATURES`
    - `settings_registry.py:ALERT_THRESHOLDS`
    - `settings_registry.py:SPEND_BREAKER_ACTIONS`
    - `settings_registry.py:normalize_allowlist_entry`
- Signup gate:
  - `signup_policy.py:SignupPolicy.check(email)`
  - `signup_policy.py:evaluate_signup`, a pure function
- Follow gate:
  - `follow_policy.py:FollowPolicy.bind(user_id, podcast_id) -> FollowGate`
  - `follow_policy.py:UNGATED`
  - `follow_policy.py:NewFeedPrecheck.check(existing, *, will_follow, user_id=None)`
- `quota_service.py:QuotaService` methods: `enforced`, `limit_for`,
  `reserve`, `follow_limits`, `check_follow`, `usage_last_day`,
  `usage_since`, `daily_usage`, `overrides_for`, `effective_limits`.
- Quota reservations and limits:
  - `quota_service.py:Reservation`, a context manager with
    `commit(subject_id)`
  - `quota_service.py:NO_RESERVATION`
  - `quota_service.py:reserve_quota`
  - `quota_service.py:FollowLimits`
  - `quota_service.py:retry_after_seconds`
- Quota definitions:
  - `quotas.py:QUOTAS` holds `feeds_introduced_per_day`, `follows_total`,
    `follows_per_day`, `imports_per_day`, `translations_per_day` and
    `narrations_per_day`.
  - Also exported: `quotas.py:QuotaSpec`, `quotas.py:QUOTA_NAMES` and
    `quotas.py:DAY` (24 h).
- Alerts:
  - `alerts.py:AlertService` methods: `evaluate`, `tick`, `send(message)`
    (per-channel success), `send_test`, `maybe_send_digest`,
    `build_digest`, `thresholds`, `with_channels`, and the
    `channel_names` property.
  - Supporting types: `alerts.py:AlertSignals`, `alerts.py:Alert` and
    `alerts.py:PipelineQueue`.
- Alert channels:
  - Channel classes: `alert_channels.py:LogChannel`,
    `alert_channels.py:EmailChannel` and
    `alert_channels.py:WebhookChannel`.
  - Functions: `alert_channels.py:build_alert_channels` and
    `alert_channels.py:deliver`.
  - Types: `alert_channels.py:AlertMessage` and
    `alert_channels.py:AlertDeliveryError`.
- Rate-limit hit log:
  - `rate_limit_hits.py:USER_HITS`, a module singleton `UserHitLog`
  - `rate_limit_hits.py:user_rate_limit_hits`
- Housekeeping: `housekeeping.py:usage_housekeeping_jobs` returns the
  periodic `release_stale_reservations` (60 s) and `prune_usage_events`
  (6 h) jobs. `housekeeping.py:USAGE_RETENTION` is 90 days.

## Invariants

- `access_guard.py:AccessGuard.ensure_active` raises `AccountDisabledError`
  only in multi-user mode, and the error message never includes the
  operator's reason (`errors.py:AccountDisabledError`).
- `account_admin_service.py:AccountAdminService._require_multi_user`: every
  read and action except `footprint` raises `single_user_mode` in
  single-user mode.
- `account_admin_service.py:AccountAdminService._guard_target`: an admin
  cannot suspend, ban, reinstate or revoke themself
  (`cannot_modify_self`).
- `account_admin_service.py:AccountAdminService._disable`: `suspend` and
  `ban` need a non-blank reason, and the status change keeps at least one
  active admin (`last_admin`). `revoke_admin` applies the same last-admin
  rule.
- `account_admin_service.py:AccountAdminService.ban` derives orphaned feeds
  from the durable suspended-follow rows. A rerun after a crash therefore
  still cancels queued work for them.
- `account_admin_service.py:AccountAdminService._remove_introduced` removes
  only feeds the user introduced that no other active user follows.
- `account_admin_service.py:revocation_instant` rounds up to the next whole
  second, so a JWT issued in the same second is also rejected.
- `audit_service.py:AuditLog.record` raises `ValueError` for any action not
  in `AUDIT_ACTIONS`.
- `settings_service.py:SettingsService.refusal`: `read_only` pauses every
  feature (`ReadOnlyError`) whatever that feature's own switch says.
- `settings_service.py:SettingsService.set` and `modify` validate before
  writing. `effective` ignores an invalid stored row and falls back to the
  env default, then to the spec's `failsafe`.
- `settings_registry.py:SETTINGS`: `signup_mode` fails safe to `closed`, and
  the feature switches fail safe to `False`.
- `settings_service.py:SettingsService._stored`: when a DB read fails it
  serves the last cached rows, and raises only when nothing is cached. A
  read that started before an `invalidate` does not store its rows.
- `signup_policy.py:evaluate_signup`: `open` allows everyone, `allowlist`
  matches the address or its `@domain` case-insensitively, and any other
  mode refuses.
- `follow_policy.py:FollowPolicy.bind` applies the `new_feeds` refusal only
  when the follow introduces a feed (zero followers), and admins are not
  exempt. A quota breach refuses only when `limits.enforce`; otherwise it
  logs `quota_would_refuse`.
- `quota_service.py:QuotaService.reserve` behaves as follows:
  - Quotas in the follow lock group raise `ValueError`;
    `follow_with_gate` checks those instead.
  - Admins get limit 0 (unlimited) but their usage is still recorded.
  - When the repository refuses the reservation, it raises
    `QuotaExceededError` with a `Retry-After` value.
- `quota_service.py:QuotaService._applies_to`: quotas apply only in
  multi-user mode with a known user; otherwise `reserve` returns
  `NO_RESERVATION`.
- `quota_service.py:Reservation._settle` settles at most once, and leaving
  the context without `commit` releases the reservation.
- `alerts.py:_tripped`: a threshold of 0 disables the rule.
- `alerts.py:AlertService.tick` sends an alert only if it wins the atomic
  claim on `alerts.last_sent.<key>` (one-hour hold).
- `alerts.py:AlertService.maybe_send_digest` sends only at
  `admin_digest_hour`, with a 20-hour claim.
- `alert_channels.py:deliver` never raises.
  `alert_channels.py:WebhookChannel.send` never puts the webhook URL in an
  error.

## Side Effects

- Database writes for account status:
  - `account_admin_service.py:AccountAdminService._disable` calls
    `UserRepository.apply_status`, `McpTokenRepository.revoke` and
    `OAuthRepository.revoke_user_grants`.
  - `account_admin_service.py:AccountAdminService.logout_everywhere` calls
    `UserRepository.set_tokens_valid_after` plus the same token and grant
    revocation.
- Database writes for follows:
  - `account_admin_service.py:AccountAdminService.ban` calls
    `SuspendedFollowRepository.move_follows_aside`.
  - `account_admin_service.py:AccountAdminService.reinstate` calls
    `SuspendedFollowRepository.restore_follows` and
    `PodcastRepository.reschedule_unscheduled_feed`.
  - `account_admin_service.py:AccountAdminService._remove_introduced` calls
    `PodcastFollowerRepository.remove` and
    `SuspendedFollowRepository.discard`.
- Task queue: `account_admin_service.py:AccountAdminService.ban` and
  `_remove_introduced` call `cancel_queued_for_podcasts` on the injected
  `QueuedTaskCanceller`.
- Database write for admin rights:
  `account_admin_service.py:AccountAdminService.grant_admin` and
  `revoke_admin` call `UserRepository.set_admin`.
- Audit trail:
  - `audit_service.py:AuditLog.record` calls `AuditLogRepository.append`
    and logs `admin_audit`.
  - Every account-admin action records an audit row.
  - `SettingsService.set` and `modify` record `setting.set` unless called
    with `audit=False`. `AccountAdminService._edit_overrides` passes
    `audit=False` (`account_admin_service.py:505`); its callers record
    `user.quota_override` instead.
  - `SettingsService.reset` records `setting.set` (with `reset=True`) only
    when a stored row was deleted (`settings_service.py:214`).
- Settings:
  - `settings_service.py:SettingsService.set` and `modify` write through
    `SettingsRepository.upsert` and `SettingsRepository.modify`, then
    invalidate the cache.
  - `settings_service.py:SettingsService.reset` deletes the stored row.
  - `settings_service.py:SettingsService._stored` reads
    `SettingsRepository.get_all` into an in-process cache (15 s TTL,
    guarded by a `threading.Lock`).
- Usage ledger:
  - `quota_service.py:QuotaService.reserve` calls
    `UsageEventRepository.reserve`.
  - `quota_service.py:Reservation._settle` calls
    `UsageEventRepository.settle`.
  - `quota_service.py:Reservation.commit` calls
    `UsageEventRepository.record` after an expiry.
  - `housekeeping.py:usage_housekeeping_jobs` calls `release_stale` and
    `prune`.
- Alerts:
  - `alerts.py:AlertService.tick` and `maybe_send_digest` claim through
    `SettingsRepository.claim`.
  - `alerts.py:AlertService._pause_new_feeds` sets `new_feeds_enabled=False`
    as `Actor.system("spend_breaker")`.
- Network:
  - `alert_channels.py:WebhookChannel.send` sends an `httpx.post` to
    `config.alert_webhook_url` with a 5 s timeout.
  - `alert_channels.py:EmailChannel.send` sends mail through `EmailSender`.
- Logging: `signup_policy.py:SignupPolicy.check` logs `signup_refused` with
  the email domain only, never the address.
- In-process state: `rate_limit_hits.py:USER_HITS` is a module-global hit
  log guarded by a `threading.Lock`. `access_guard.py:NULL_GUARD` is a
  module singleton.

## Verification

- Run: `./venv/bin/pytest tests/unit/services/abuse`
- Full subsystem: `./venv/bin/pytest tests/unit/services`

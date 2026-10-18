# Abuse Prevention and User Administration

> **Status:** 🚧 In progress. Phases 1–2 implemented (see [Implementation notes](#implementation-notes)); Phases 3–4 open
> **Created:** 2026-10-17
> **Updated:** 2026-10-18
> **Priority:** High. Signup is open to any Google account, and one user can make the server transcribe as many feeds as they like.
> **Author:** Product & Engineering
> **Related:** [#06 authentication](06-authentication.md) (lists per-user quotas as a goal), [#13 multi-user-shared-podcasts](13-multi-user-shared-podcasts.md) (leaves the follow limit open), [#25 security-audit-and-hardening](25-security-audit-and-hardening.md), [#63 universal-follower-gate](63-universal-follower-gate.md) (why a follow costs money), [#78 remote-mcp-access](78-remote-mcp-access.md), [#95 product-analytics](95-product-analytics.md) (the admin `/analytics` page this sits beside)

---

## Executive summary

Thestill is open to anyone with a Google account, and the operator can do little about a user who misbehaves. No account can be disabled. Signups can't be stopped. Admins are made by editing the database. There is no limit on follows. Nothing tells the operator when something looks wrong.

The expensive risk is spending, not traffic. Under spec #63 a podcast is processed while it has at least one follower. The first follow of a feed queues its newest `INBOX_SEED_ON_FOLLOW` episodes (default 2) for download, transcription, cleaning, summarising and entity extraction, and then every future episode. A script that follows 500 obscure feeds commits the server to transcribing all of them for good. Following a feed someone already follows costs almost nothing.

This spec adds four layers, each shippable on its own:

1. **Kill switches.** Account status (active, suspended, banned), a signup gate (open, allowlist, closed), global switches for the costly paths, and CLI commands that work when the web UI does not.
2. **Quotas.** Per-user daily limits on the actions that cost money, counted in one usage ledger, with the main limit on *feeds a user brings into the system* rather than on follows.
3. **Alerts.** Email (and an optional webhook) to the operator when a threshold trips, plus a daily digest.
4. **Admin console.** A `/admin/users` page to see who is doing what and act on it, a settings tab for the switches, and an audit log.

## Current state

| Area | Today | Reference |
|---|---|---|
| Sign-in | Google OAuth only. A new Google identity creates a user, with no gate | [auth_service.py:172](../thestill/services/auth_service.py#L172), create branch at [:253](../thestill/services/auth_service.py#L253) |
| User row | No status, ban or deleted column. `is_admin` is set by hand in the database | [user.py:28](../thestill/models/user.py#L28) |
| Session check | `get_user_from_token` verifies the JWT and loads the user, with no status check | [auth_service.py:334](../thestill/services/auth_service.py#L334) |
| MCP check | Loads the user fresh on every request, with no status check | [mcp_http.py:176](../thestill/web/mcp_http.py#L176) |
| Admin gate | `require_admin` and `is_effective_admin` (always true in single-user mode) | [dependencies.py:301](../thestill/web/dependencies.py#L301), [:343](../thestill/web/dependencies.py#L343) |
| Follow | No limit. Re-arms the feed schedule and seeds the inbox | [follower_service.py:91](../thestill/services/follower_service.py#L91) |
| Follower gate | Unfollowed feeds are skipped when enqueuing | [queue_manager.py:1063](../thestill/core/queue_manager.py#L1063) |
| Add feed | Any signed-in user. Only one add runs at a time across the server | [api_commands.py:388](../thestill/web/routes/api_commands.py#L388) |
| Import | 24h count per user is logged, not enforced | [import_service.py:851](../thestill/services/import_service.py#L851) |
| Summary translation | Synchronous LLM call on the first `?lang=` request | [api_podcasts.py:470](../thestill/web/routes/api_podcasts.py#L470) |
| Rate limits | In-process sliding window, keyed by IP (auth, public, webhooks) or by token (MCP). No per-user limit on `/api` | [rate_limit.py:99](../thestill/web/middleware/rate_limit.py#L99) |
| Alerting | None. Email is used only for briefings | [email_sender.py:195](../thestill/services/email_sender.py#L195) |
| Runtime settings | None. Everything is an environment variable | [config.py:304](../thestill/utils/config.py#L304) |

## Goals

1. The operator can stop one user, or stop all new signups, in under a minute, from the web or the CLI, with no redeploy.
2. No single user can commit the server to unlimited transcription or LLM spend.
3. The operator hears about abuse from an alert or the daily digest, not from the bill.
4. One admin page shows each user's activity and lets the operator act on it.
5. Every admin action is recorded.
6. Single-user mode behaves exactly as today. None of this applies there.

## Non-goals

- **Volumetric attacks.** A flood of traffic has to be stopped in front of the app (Cloudflare, Caddy rate limits, the host's firewall). This spec covers one signed-in user misbehaving. See [Deployment note](#deployment-note).
- **Billing or paid tiers.** Quotas here are abuse limits, not product plans.
- **Invite codes.** The allowlist covers the near-term need. Codes can come later on the same signup gate.
- **Automatic banning.** The system suspends nothing on its own. It limits, alerts and leaves the decision to a person. The one exception is the optional global spend breaker in Phase 3, which pauses processing, not users.
- **Content moderation.** Users don't publish anything other users see.

## Design

### Phase 1: Kill switches

#### 1.1 Account status

Add to `users`:

| Column | Type | Notes |
|---|---|---|
| `status` | text, not null, default `'active'` | `active`, `suspended` or `banned` |
| `status_reason` | text, null | Free text the operator wrote, shown only to admins |
| `status_changed_at` | timestamptz, null | |
| `status_changed_by` | uuid, null | The admin who changed it. Null for the CLI |

`User` gains `status: UserStatus` (a `str` enum) and an `is_active` property.

The two non-active states differ in what they do to follows:

- **Suspended** blocks sign-in and every request. Follows are kept, so lifting a suspension restores the account exactly. Use it while you look into something.
- **Banned** blocks sign-in and every request, *and* stops the spending the user caused. Use it when you've decided.

**Where status is checked.** One helper, `AuthService.ensure_active(user)`, raises `AccountDisabledError` for a non-active user. It is called in:

- `get_user_from_token` (every cookie and Bearer request). The web layer maps the error to `403 {"detail": {"error": "account_disabled", ...}}`. It does not return `None`, because the frontend treats `None` as "signed out" and would loop through `/login`. The one exception is `/api/auth/status`, which the app calls on every page load: it answers `authenticated: false` plus `account_disabled: true`, so the app lands on `/login` and shows the message.
- `handle_google_callback`, after the user is found. Sign-in redirects to `/login?error=account_disabled`, and `/login` shows "This account has been disabled. Contact <operator email>."
- The MCP HTTP auth path at [mcp_http.py:176](../thestill/web/mcp_http.py#L176), returning 403.

All three already load the user row fresh on each request, so a status change takes effect on the user's next request with no cache to clear.

**Where status is checked in background work.** Authentication is not the only way a user causes spend. The briefing scheduler runs per user on its own clock ([briefing_scheduler.py:145](../thestill/core/briefing_scheduler.py#L145)). It generates a script with the LLM, chains narration (an LLM-written narration script), and queues an email. Its deferral retries and the delivery pass also run without a request. So `ensure_active` is also enforced at each costly boundary, where the work starts, not only where it is scheduled:

| Boundary | Check | On a non-active user |
|---|---|---|
| `BriefingService.generate_for_user` ([briefing_service.py:140](../thestill/services/briefing_service.py#L140)) | User status, read fresh | Returns `None`, as for an empty inbox, and logs `briefing_skipped_account_disabled`. The scheduler still claims and advances the slot, so a suspended user isn't reconsidered every tick, and a reinstated user's next briefing covers everything since their last one |
| `BriefingScheduler._retry_pending_briefings` | Same call, so same check | The deferral is dropped |
| `NarrationRunner.run` ([narration_runner.py:107](../thestill/services/narration/narration_runner.py#L107)) | Status of the briefing's owner | Raises `AccountDisabledError` before any LLM or TTS call. The scheduler's narration chain already logs and moves on after a failure |
| `BriefingDeliveryService._send_one` ([briefing_delivery_service.py:182](../thestill/services/briefing_delivery_service.py#L182)) | Status, next to the existing opt-in re-check at send time | Raises `_PermanentDeliveryError("account disabled")`. This parks the row as failed without spending retries, so the check also covers deliveries already queued and those waiting on a backoff retry. A reinstated user doesn't get the parked emails |

Putting the check inside each service rather than in the scheduler means a new caller can't skip it. That includes the manual narrate route ([api_briefings.py:546](../thestill/web/routes/api_briefings.py#L546)), which authentication already covers. The lookup is one primary-key read per briefing, cheap next to the LLM call it guards. Changing status also revokes the user's outstanding JWTs. Each `jti` is not stored per user today, so revocation adds a `tokens_valid_after timestamptz` column on `users`. `verify_jwt` rejects any token whose `iat` is earlier. This also gives "sign out everywhere" for free. MCP tokens are revoked through their existing table.

**Banning and the follower gate.** About 33 queries read `podcast_followers` to decide whether a feed has followers. Changing all of them to skip banned users is invasive and easy to get wrong. Banning instead *moves* the user's follows aside:

1. Copy the user's rows from `podcast_followers` into a new `suspended_follows(user_id, podcast_id, created_at, suspended_at)` table.
2. Delete them from `podcast_followers` through the follower repository. This does not go through `FollowerService.unfollow`, so it fires no unfollow side effects. Inbox rows stay so an unban is lossless.
3. Feeds left with no followers drop out of the follower gate at the next enqueue. Tasks already queued for them are cancelled. Tasks already running finish.

Unbanning moves the rows back with their original `created_at` and re-arms each feed's schedule (`reschedule_unscheduled_feed`), like a follow does. A ban plus "remove the feeds they brought in" (1.5) is the full clean-up.

#### 1.2 Signup gate

A runtime setting `signup_mode`:

| Mode | A new Google identity | An existing user |
|---|---|---|
| `open` (default) | Account created | Signs in |
| `allowlist` | Created only if the email, or its domain, is on `signup_allowlist` | Signs in |
| `closed` | Refused | Signs in |

The check sits only in the create-new-user branch of `handle_google_callback`, after the account-linking lookup by email. An existing user is never locked out by the gate. A refused signup redirects to `/login?error=signups_closed`. The page says signups are paused, and offers a mailto link when `ADMIN_CONTACT_EMAIL` is set. Every refusal is logged as `signup_refused` with the email domain only, not the address.

`signup_allowlist` holds entries like `alice@example.com` or `@example.com`, compared case-insensitively.

#### 1.3 Runtime settings

New table `app_settings(key text primary key, value jsonb not null, updated_at timestamptz, updated_by uuid null)`. On SQLite, `value` is JSON text.

`SettingsService` reads through an in-process cache with a 15-second TTL and clears its own cache on write. With several workers, a switch takes effect everywhere within 15 seconds. A setting with no row falls back to an environment default. This lets an operator pin a value in `.env` and still override it at runtime.

| Key | Type | Env default | Effect when changed from default |
|---|---|---|---|
| `signup_mode` | `open` \| `allowlist` \| `closed` | `SIGNUP_MODE=open` | See 1.2 |
| `signup_allowlist` | list of strings | `SIGNUP_ALLOWLIST=` (comma-separated) | See 1.2 |
| `new_feeds_enabled` | bool | true | Off: refuses any action that would take a feed from zero active followers to one. That covers add-feed, resolve, MCP `add_podcast`, *and a follow of an existing feed nobody follows*. Following a feed that already has followers still works. See below |
| `imports_enabled` | bool | true | Off: `POST /api/imports` and its MCP equivalent return 503 |
| `translations_enabled` | bool | true | Off: `?lang=` serves a translation that already exists and returns 503 for one that doesn't. Enforced in the service, not by HTTP method. See below |
| `narration_enabled` | bool | `NARRATION_ENABLED` | Same as the env flag, but can be changed at runtime. Off: manual and scheduled narration both stop |
| `read_only` | bool | false | Panic mode. Every non-admin `POST`, `PATCH`, `PUT` and `DELETE` under `/api` and every MCP mutating tool returns `503 read_only`. **It also turns off `new_feeds_enabled`, `imports_enabled`, `translations_enabled` and `narration_enabled`**, whatever their own values, so spending a user can start through a `GET` or a background job stops too. Reading cached content still works. Pipeline processing of feeds that already have followers, and scheduled briefing *scripts*, carry on. To stop those, see the open question on a processing pause |
| `quota_overrides` | Phase 2 | | |
| `alerts` | Phase 3 | | |

A 503 carries a short message the frontend shows as a toast, for example "Adding new podcasts is paused right now."

**Switches are enforced where the cost starts, not by HTTP method.** A filter on `POST` routes misses spending that a `GET` or a background job starts. The summary route, for example, is a `GET` that generates a missing translation on the spot ([api_podcasts.py:530](../thestill/web/routes/api_podcasts.py#L530)). So each switch is checked in the service method that makes the paid call, through `SettingsService.allowed(feature)`, which returns false when the feature's switch is off *or* `read_only` is on:

| Feature | Checked in | Cached content still served |
|---|---|---|
| `translations` | `PodcastService.get_or_create_summary_translation` ([podcast_service.py:1040](../thestill/services/podcast_service.py#L1040)), before the LLM call and after the lookup for an existing translation | Yes: an existing translation is returned |
| `narration` | `NarrationRunner.run`, before any LLM or TTS call. This also covers the scheduler's narration chain | Yes: existing narration audio still plays |
| `imports` | `ImportService`, before the episode row is created | n/a |
| `new_feeds` | The follow gate below, and the add-feed and resolve paths before a podcast row is created | n/a |

The service raises `FeaturePausedError(feature)`. Routes map it to 503 and MCP tools to a tool error. The scheduler logs it and moves on. The `read_only` method filter stays as a blunt second layer, but no costly path relies on it.

**The follow gate.** A follow is a costly action only when it takes a feed from zero active followers to one. That follow seeds the newest `INBOX_SEED_ON_FOLLOW` episodes through the pipeline and turns on processing for every future episode (spec #63). `new_feeds_enabled` therefore gates that zero-to-one step, whatever route makes it:

- `FollowerService.follow` and the add-feed auto-follow call one new repository method, `follow_with_gate(user_id, podcast_id, gate)`. In a single transaction it:
  1. takes a per-podcast lock (Postgres `pg_advisory_xact_lock`; SQLite `BEGIN IMMEDIATE`);
  2. counts the feed's current followers;
  3. if the count is zero, refuses with `FeaturePausedError("new_feeds")` when the feature isn't allowed, and otherwise takes the `feeds_introduced_per_day` reservation from Phase 2;
  4. inserts the follow.

  Locking means two concurrent first follows can't both see a non-zero count from each other's uncommitted row.
- An unban (1.1) restores follows through the same table but bypasses the gate, since an admin chose it. It is logged in the audit log with the number of feeds it reactivated.
- Admins are not exempt from the gate. A tripped breaker should stop the operator's own clicks too, and the operator can flip the switch back.

#### 1.4 CLI

These commands run straight against the database, so they work when the web server is down or being abused. In single-user mode they print an explanation and exit 1.

```bash
thestill user list [--status banned] [--sort created|last-login|follows] [--json]
thestill user show <email-or-id>          # status, follows, feeds brought in, 24h usage
thestill user suspend <email-or-id> --reason "..."
thestill user ban <email-or-id> --reason "..." [--remove-introduced-feeds]
thestill user reinstate <email-or-id>
thestill user logout-everywhere <email-or-id>
thestill admin grant <email>              # also replaces hand-editing is_admin
thestill admin revoke <email>
thestill settings get [key]
thestill settings set <key> <value>       # e.g. settings set signup_mode closed
```

Every command writes to the audit log (1.6) with `actor = 'cli'`.

#### 1.5 "Feeds this user brought in"

A feed is *brought in* by a user when that user's follow made it go from zero followers to one. Phase 2 records this in the usage ledger at the moment it happens. For data from before Phase 2, the earliest `podcast_followers.created_at` per podcast is a good-enough stand-in.

`--remove-introduced-feeds`, and the console button of the same name, applies only to feeds the user brought in *that no other active user follows now*. For each such feed it:

- deletes the user's follow;
- cancels its queued tasks;
- leaves the podcast row, episodes and anything already transcribed in place. Deleting processed data is a separate, explicit `thestill remove`.

#### 1.6 Audit log

New table `admin_audit_log(id, created_at, actor_user_id null, actor text, action text, target_user_id null, details jsonb)`. Here `actor` is `'web'` or `'cli'`.

Actions: `user.suspend`, `user.ban`, `user.reinstate`, `user.logout_everywhere`, `user.remove_introduced_feeds`, `user.quota_override`, `admin.grant`, `admin.revoke`, `setting.set`.

`details` holds the reason and the old and new values. It never holds secrets or tokens. The log is append-only, with no API to edit or delete entries.

### Phase 2: Quotas

#### 2.1 Usage ledger

New table `usage_events(id, user_id, kind text, state text, created_at, settled_at null, subject_id text null)`, indexed on `(user_id, kind, created_at)`. `state` is `reserved`, `committed` or `released`. One row is written per costly action, *before* the action runs (see 2.2):

| `kind` | Reserved when | `subject_id` |
|---|---|---|
| `feed_introduced` | A follow, add-feed or MCP `add_podcast` is about to take a feed from 0 to 1 followers | podcast id |
| `follow` | Any follow | podcast id |
| `import` | An import is about to be accepted | episode id |
| `translation` | A translation is about to be generated, not when an existing one is served | episode id |
| `narration` | A narration is about to start | briefing id |
| `quota_exceeded` | A quota refuses a request. Written `committed`, never reserved | the quota name |

The import counter already logged in [import_service.py:851](../thestill/services/import_service.py#L851) moves to this ledger, as its comment there asks. The ledger also feeds the console and the digest, so the number that limits a user is the number the operator sees.

Rows older than 90 days are deleted by the existing housekeeping job, keeping `feed_introduced` rows so 1.5 stays exact.

#### 2.2 Limits

| Quota | Counts | Default | Env |
|---|---|---|---|
| `feeds_introduced_per_day` | `feed_introduced` in the last 24h | 10 | `QUOTA_FEEDS_INTRODUCED_PER_DAY` |
| `follows_total` | Current rows in `podcast_followers` | 200 | `QUOTA_FOLLOWS_TOTAL` |
| `follows_per_day` | `follow` in the last 24h | 50 | `QUOTA_FOLLOWS_PER_DAY` |
| `imports_per_day` | `import` in the last 24h | 5 | `QUOTA_IMPORTS_PER_DAY` |
| `translations_per_day` | `translation` in the last 24h | 20 | `QUOTA_TRANSLATIONS_PER_DAY` |
| `narrations_per_day` | `narration` in the last 24h | 3 | `QUOTA_NARRATIONS_PER_DAY` |

The windows roll over 24 hours rather than resetting at midnight, so there is no burst at the turn of the day. `0` means unlimited. Admins are exempt.

**Check and record must be one atomic step.** If the check reads the count first and the usage row is written after the action, concurrent requests all pass with one allowance left. The window is widest for translations and narration, which take seconds. So the quota is enforced by an atomic *reserve*, and the action then *commits* or *releases* the reservation:

```python
with quota_service.reserve(user, "translations_per_day", subject_id=episode_id) as reservation:
    summary = generate_translation(...)   # the paid call
    reservation.commit()
# Leaving the block without commit() (an exception, a FeaturePausedError) releases it.
```

`QuotaService.reserve` runs one short database transaction:

1. Take a lock scoped to `(user_id, quota)`. On Postgres: `pg_advisory_xact_lock(hashtextextended(user_id || ':' || quota, 0))`. On SQLite: `BEGIN IMMEDIATE`, which serialises writers on the whole database. That's acceptable for a handful of costly actions a minute.
2. Count the user's rows for the quota's kind in the window with `state IN ('reserved', 'committed')`. A pending reservation counts against the limit, so a slow translation can't let a second one through.
3. If the count is at the limit, write a `quota_exceeded` event and raise `QuotaExceededError`. Otherwise insert the row as `reserved`.
4. Commit, which releases the lock.

The lock is held only for the count and the insert, never during the paid call. Two requests from the same user for the same quota wait for each other for milliseconds. Requests from different users, or for different quotas, never wait.

Settling the reservation:

- **Commit** sets `state = 'committed'` once the paid work has been handed off. For translation and narration that is when the result is stored. For a follow or import, it is in the same transaction as the follow or episode insert, so a crash can't leave a follow without its usage row.
- **Release** sets `state = 'released'`, which stops it counting. This happens when the action fails *before* any money is spent: validation errors, `FeaturePausedError`, a provider error before the first token. A failure after the provider has charged (a timeout after the response started, a storage write failing after generation) still commits, because the quota tracks spend, not success.
- **Abandoned reservations.** A process killed between reserve and settle leaves a `reserved` row. It keeps counting, so the system fails closed. The housekeeping job releases `reserved` rows older than `QUOTA_RESERVATION_TTL_SECONDS` (default 900, longer than any translation or narration) and logs `quota_reservation_expired`. A burst of these points at a crash loop.
- **Releasing never refunds a quota at the user's request.** No API releases a reservation except the code path that made it.

`follows_total` counts live rows in `podcast_followers` rather than the ledger. It is checked inside `follow_with_gate`'s transaction (1.3) under a per-user lock taken *before* the per-podcast lock. Every path takes locks in the same order: user quota locks, then the podcast lock. That ordering rules out deadlocks between two follows.

The reservation runs in the service layer (`FollowerService.follow` through `follow_with_gate`, the add-feed route, `ImportService`, `get_or_create_summary_translation`, `NarrationRunner.run`). The REST routes and MCP tools then share one rule. Scheduled narration reserves against the briefing owner's `narrations_per_day` like a manual one. A refusal there is logged and the briefing ships without audio. A refusal:

- raises `QuotaExceededError(quota, limit, retry_after_seconds)`;
- is mapped to `429 {"error": "quota_exceeded", "quota": "...", "limit": N, "retry_after": S}` with a `Retry-After` header, or to an MCP tool error with the same text;
- writes a `quota_exceeded` usage event;
- shows in the frontend as "You've added 10 new podcasts today. You can add more in 6 hours."

Following a feed that already has followers counts against `follows_*` but not against `feeds_introduced_per_day`. That is the key distinction: the cheap action stays generous and the costly one is tight.

**Per-user overrides.** The runtime setting `quota_overrides` is a map `{user_id: {quota: limit}}`, edited from the console or with `thestill user quota <email> <quota> <limit>`. It is for trusted users who need more.

#### 2.3 Per-user request rate limit

A new dependency, `user_rate_limit`, uses the existing `_SlidingWindow`, keyed `user:<id>`, on every authenticated `/api` router:

| Limit | Default | Env |
|---|---|---|
| General | 300 / 60s | `USER_API_LIMIT` |
| Search (`/api/search/*`) | 30 / 60s | `USER_SEARCH_LIMIT` |

It runs after authentication so it can key on the user. Signed-out traffic is still covered by the per-IP limits. Hits log `rate_limit_exceeded` with `user_id`, which Phase 3 counts.

### Phase 3: Alerts

#### 3.1 Channels

- **Email** through the existing `make_email_sender` (SMTP or SES), to `ADMIN_ALERT_EMAIL` (comma-separated). If no email provider is configured, alerts go only to the log, and `thestill status` says so.
- **Webhook** (optional) via `ALERT_WEBHOOK_URL`. It POSTs `{"text": "...", "alert": "...", "details": {...}}`, which ntfy, Slack and Discord incoming webhooks all accept. It has a 5s timeout and failures are logged, not retried.

Every alert is also logged as `admin_alert` with its kind, so a JSON log pipeline can alert from logs instead.

#### 3.2 Instant alerts

An `AlertService` evaluates conditions on a background tick every 5 minutes. The scheduler loop the refresh scheduler already uses runs the tick, so no new process is needed. Each alert kind is sent at most once per hour (per user, where it is about a user). That state lives in `app_settings` under `alerts.last_sent` so it is shared across workers.

| Alert | Fires when | Default threshold |
|---|---|---|
| `quota_pressure` | One user has ≥ N `quota_exceeded` events in the last hour | 5 |
| `rate_limit_pressure` | One user has ≥ N rate-limit hits in the last hour | 50 |
| `signup_spike` | ≥ N new users in the last hour | 10 |
| `feed_spike` | ≥ N `feed_introduced` across all users in the last hour | 30 |
| `queue_backlog` | Pending pipeline tasks exceed N | 500 |
| `spend_breaker` | Estimated audio minutes queued for transcription in the last 24h exceed N | off |

Thresholds live in the runtime setting `alerts` (with env defaults), so they can be tuned without a redeploy.

Each alert names the user (email and a link to their console page) and the numbers, and suggests the obvious action. For example: "<alice@example.com> hit the new-feed quota 7 times in the last hour. Suspend: <link>".

`spend_breaker` is the only alert that acts on its own. When `alerts.spend_breaker_action = "pause_new_feeds"` it also sets `new_feeds_enabled = false` and says so in the alert. That stops every route to new spend, including a first follow of a dormant feed already in the database (see the follow gate in 1.3). It never suspends users. The estimate uses episode durations from the feed, which every queued episode already has.

`rate_limit_pressure` is counted per process from the in-memory limiter and summed by the tick reading the log events it emits. With several workers each process reports its own count, so the threshold is a per-worker lower bound. That is good enough to spot a user hammering the server.

#### 3.3 Daily digest

The digest is sent at `ADMIN_DIGEST_HOUR` (default 08:00 in the server's timezone) to the same channels. It is skipped when the day had no signups, no quota hits and no alerts, unless `ADMIN_DIGEST_ALWAYS=true`. It contains:

- new users in the last 24h (email, signup source);
- the top 5 users by `feed_introduced`, `follow` and requests;
- every user who hit a quota, and how often;
- alerts sent in the last 24h;
- the current signup mode and any switch that is not at its default, so a forgotten `read_only` gets noticed.

### Phase 4: Admin console

New admin-only pages wrapped in the existing `AdminRoute`, under an "Admin" section in the nav beside Status, Failed, Queue and Analytics.

#### 4.1 `/admin/users`

A table with one row per user:

| Column | Source |
|---|---|
| Email, name, avatar | `users` |
| Joined, signup source | `users.created_at`, `signup_source` |
| Last sign-in | `users.last_login_at` |
| Follows | count of `podcast_followers` |
| Feeds brought in (24h / all time) | `usage_events` |
| Imports, translations (24h) | `usage_events` |
| Quota hits (24h) | `usage_events` |
| Status | `users.status`, with a badge |
| Admin | `users.is_admin` |

It can be searched by email, filtered by status, sorted by any column, and paginated with the API's standard pagination. The default sort is "quota hits in the last 24h, then feeds brought in in the last 24h", so the users most worth a look are at the top.

#### 4.2 `/admin/users/:id`

- Header: identity, status with reason and who set it and when, and the admin badge.
- Usage: 24h and 7-day counts per usage kind, with a small sparkline per kind.
- Follows: the user's feeds, marking the ones they brought in and how many other users follow each.
- Actions, each behind a confirm dialog. Suspend, ban and reinstate require a reason.
  - Suspend, Ban (with a "Also remove feeds they brought in" checkbox), Reinstate
  - Sign out everywhere
  - Remove feeds they brought in
  - Quota overrides (per-quota number fields)
  - Grant or revoke admin. An admin can't revoke themselves or disable their own account, so the last admin can't be locked out.
- History: this user's entries from the audit log.

#### 4.3 `/admin/settings`

- Signup mode (a radio group) and the allowlist (a textarea, one entry per line).
- A toggle per switch from 1.3. `read_only` has a red confirm dialog.
- Alert thresholds and channels, with a "Send test alert" button.
- A banner at the top of every admin page whenever a switch is not at its default.

#### 4.4 `/admin/audit`

The audit log, newest first, filterable by action and target.

#### 4.5 API

All routes are under `/api/admin`, mounted with `require_admin`, and return the standard envelope. Every write writes to the audit log.

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/admin/users` | List with the columns in 4.1. Query: `q`, `status`, `sort`, pagination |
| GET | `/api/admin/users/{id}` | Detail for 4.2 |
| POST | `/api/admin/users/{id}/status` | Body `{status, reason, remove_introduced_feeds?}` |
| POST | `/api/admin/users/{id}/logout-everywhere` | |
| POST | `/api/admin/users/{id}/remove-introduced-feeds` | |
| PUT | `/api/admin/users/{id}/quotas` | Body `{quota: limit}`. An empty body clears the overrides |
| POST | `/api/admin/users/{id}/admin` | Body `{is_admin}` |
| GET | `/api/admin/settings` | Effective value and source (`runtime` or `env`) for each key |
| PUT | `/api/admin/settings/{key}` | Body `{value}`, validated per key. `DELETE` reverts to the env default |
| POST | `/api/admin/alerts/test` | Sends a test alert to every configured channel |
| GET | `/api/admin/audit` | Query: `action`, `target_user_id`, pagination |

`UserRepository` gains `list_users(...)` with the aggregate counts, `set_status`, `set_admin` and `set_tokens_valid_after`, with SQLite and Postgres variants.

## Data model changes

| Change | Phase |
|---|---|
| `users`: `status`, `status_reason`, `status_changed_at`, `status_changed_by`, `tokens_valid_after` | 1 |
| New `suspended_follows` | 1 |
| New `app_settings` | 1 |
| New `admin_audit_log` | 1 |
| New `usage_events` (with `state` and `settled_at` for reservations) | 2 |

Each change follows the repo's three-part pattern:

1. an Alembic revision (`0021_user_status_admin_tables`, then `0022_usage_events`; 0019 and 0020 went to spec #103);
2. the same idempotent DDL in `postgres_schema.SCHEMA_SQL`;
3. the SQLite `CREATE TABLE` plus `PRAGMA table_info` / `ALTER TABLE` in `SqlitePodcastRepository._run_migrations`.

The SQLite-to-Postgres copy tool ([db_migration.py](../thestill/utils/db_migration.py)) needs no change: it discovers tables from `sqlite_master`.

## Single-user mode

Everything in this spec is off. In single-user mode:

- `ensure_active` is not called;
- the signup gate doesn't exist;
- quotas and `user_rate_limit` are no-ops;
- the `/admin/users` and `/admin/audit` pages say "Multi-user mode only".

The global switches in 1.3 *do* apply, because they protect spend, not users, and alerts work. This matches how `is_effective_admin` treats single-user mode today.

## Deployment note

The rate limiter counts inside each process. With N workers, every per-IP and per-user limit is effectively N times its setting. That's acceptable for catching one misbehaving user, but it is not flood protection. The production Caddy setup ([#364](https://github.com/ssarunic/thestill/pull/364) made `TRUSTED_PROXIES` CIDR-aware for it) should add a per-IP request rate at the proxy, or the site should sit behind Cloudflare. A shared limiter (Postgres or Redis) is out of scope until there is more than one host.

## Rollout

Each phase is its own PR and works without the later ones.

1. **Phase 1**: status, signup gate, settings, CLI, audit log. Ship first: it gives the emergency controls.
2. **Phase 2**: usage ledger, quotas, per-user rate limit. Ship with defaults generous enough that no current user hits them. Check the digest numbers (or `thestill user list --sort follows`) against the defaults before deploying.
3. **Phase 3**: alerts and digest. Needs `EMAIL_PROVIDER` and `ADMIN_ALERT_EMAIL` set in production.
4. **Phase 4**: the console. Until it ships, the CLI from Phase 1 does everything the console will.

`docs/configuration.md` gains every new env var. `docs/web-server.md` and `specs/02-api-reference.md` gain the `/api/admin` routes. `CLAUDE.md` gains the `thestill user` / `admin` / `settings` commands.

## Implementation notes

Where the build differs from, or adds to, the design above.

**Phase 1** (branch `feat/104-p1-kill-switches`):

- **Layout.** Everything lives in `thestill/services/abuse/`. `build_abuse_services(config, repos)` is the one place the web app, the CLI and the MCP server construct the services, so no entry point can forget the guard. Costly services take an `AccessGuard` that defaults to allow-all, so hand-built test fixtures keep working. Each new table has its own repository (ABC, SQLite, Postgres). `UserRepository.list_users` became a separate `UserDirectoryRepository`, which keeps cross-table counts off the per-request auth path.
- **Error bodies** sit under `detail`, the shape `HTTPException(detail=dict)` produces and the frontend already parses: `{"detail": {"error": "feature_paused", "feature": "new_feeds", "message": "..."}}`. One exception handler maps every refusal (403 disabled, 503 paused, 409 forbidden admin action).
- **Token revocation** stores `tokens_valid_after` rounded *up* to the next whole second, because a JWT's `iat` has whole-second precision. A re-login inside the same second is also rejected.
- **Account status is checked before revocation** in `get_user_from_token`, so a banned user's old cookie still reaches the "account disabled" message rather than a sign-in loop.
- **Narration.** `NarrationRunner` writes the script with the LLM; there is no TTS step. The runner is now always built, and `NARRATION_ENABLED` only sets the default of the runtime switch. The operator's own `thestill narrate` is not gated, since it always ignored `NARRATION_ENABLED`.
- **Add-feed and resolve.** The new-feed check runs once the URL is canonical, before any podcast row is written. Add-feed and MCP `add_podcast` refuse an unknown feed, or a known feed nobody follows. Multi-user resolve only browses, so it refuses only an unknown feed. Add-feed runs as a background task, so a refusal reaches the user as the task's failure message, not as an HTTP 503.
- **Read-only filter.** `enforce_read_only` rides on every session router, next to `require_auth`. MCP checks `_MUTATING_TOOLS`. Admins pass both, but the follow gate still applies to them.
- **Audit actors** are `web`, `cli` and `system` (`system` is for the Phase 3 breaker). The CLI also has `thestill user remove-introduced-feeds` and `thestill settings reset`.
- **New config.** `ADMIN_CONTACT_EMAIL` is new, alongside `SIGNUP_MODE` and `SIGNUP_ALLOWLIST`.
- **Housekeeping.** Phase 2's reservation expiry and ledger pruning need a new periodic loop: there was no housekeeping job to reuse.

**Phase 2** (branch `feat/104-p2-quotas`, stacked on Phase 1):

- **Ledger.** `usage_events` (alembic 0022) has its own repository. `reserve` takes a per-(user, lock group) Postgres advisory lock, or SQLite `BEGIN IMMEDIATE`, then counts reserved and committed rows and inserts. Follow quotas share the `follow` group, so a follow takes one user lock, always before the podcast lock.
- **Follows don't reserve.** `follow_with_gate` counts the user's `follow` and `feed_introduced` rows under the same locks, and writes the new rows, or the `quota_exceeded` row, in the follow's own transaction. The follow and its rows carry the instant the limits were measured at.
- **Imports** reserve only when the episode is new, and commit once it is created. Re-importing an existing episode is free even at the limit. The old inbox-based import counter in the log now reads the ledger.
- **Add-feed and remote MCP `add_podcast`** also run a read-only follow-quota precheck before any podcast row is written. The follow's own check stays authoritative and records the refusal.
- **`Retry-After`** for a reserved quota comes from the row whose expiry brings the count back under the limit, which stays right after an override lowers a limit below current use. For follows it is the oldest row in the window, a lower bound in that case.
- **The quota table** (`services/abuse/quotas.py`) holds each quota's kind, window, lock group, env field and refusal text once.
- **A commit after expiry still counts.** If a paid call outlives `QUOTA_RESERVATION_TTL_SECONDS`, its late commit records a fresh committed row and logs `quota_commit_after_expiry`.
- **Translation** charges the requesting user. The summary route now takes the session user. A cached translation is free.
- **Refusal messages are written by the server** (for example "You've added 10 new podcasts today. You can add more in 6 hours."), so the frontend shows the `message` field.
- **The per-user rate limit** uses the existing `RATE_LIMIT_*` naming: `RATE_LIMIT_USER_MAX` / `_WINDOW_SECONDS` and `RATE_LIMIT_USER_SEARCH_MAX` / `_WINDOW_SECONDS`, not `USER_API_LIMIT` / `USER_SEARCH_LIMIT`. Hits are kept per process, trimmed to an hour on every record, ready for Phase 3's `rate_limit_pressure` (nothing reads them until then), so they are not parsed from logs.
- **`QUOTA_ENFORCE`** defaults to `true`. With `false`, usage is recorded and refusals are only logged (`quota_would_refuse`), which answers open question 3.
- **Housekeeping** is a new `core/periodic_runner.py` loop started in the app lifespan. It releases expired reservations every minute and prunes the ledger every 6 hours.
- **CLI.** `thestill user quota <email> <quota> <limit|default>` audits `user.quota_override` with only that user's limit, not the whole overrides map. `thestill user show` lists the last 24 hours of usage.

## Testing

- **Status:**
  - A suspended or banned user's cookie, Bearer token and MCP token each get 403, never a redirect loop.
  - A token issued before `tokens_valid_after` is rejected.
  - Reinstating restores access.
- **Status in background work:**
  - A due schedule for a suspended user advances its slot without generating a briefing.
  - A deferred briefing for a user suspended mid-deferral is dropped on the next retry.
  - `NarrationRunner.run` for a disabled owner makes no LLM or TTS call.
  - Three delivery cases are parked as failed without sending and without using a retry: a pending delivery, one waiting on backoff, and one queued just before the suspension.
- **Ban and follows:**
  - A ban moves the follows to `suspended_follows`, a feed left with no followers stops being enqueued, and its queued tasks are cancelled.
  - A feed another user still follows is unaffected.
  - Unban restores the follows with their original `created_at`.
- **Signup gate:**
  - Each mode works for a new identity.
  - An existing user (by `google_id` and by email-linking) always gets in.
  - Allowlist entries for a single address and for a domain both work, case-insensitively.
- **Settings:**
  - The env default applies with no row, and a row overrides it.
  - The cache expires after the TTL.
  - Each switch blocks exactly its paths. `read_only` lets admins and reads through.
  - With `translations_enabled` off, or `read_only` on, a `GET .../summary?lang=` for a missing translation returns 503 and makes no LLM call, while an existing translation is still served.
  - `read_only` stops scheduled narration.
  - With `new_feeds_enabled` off:
    - following a feed with zero followers is refused, through both REST and MCP;
    - following a feed that already has followers succeeds;
    - two concurrent first follows of the same dormant feed are both refused, with no follow row left behind;
    - an unban that restores follows to dormant feeds succeeds and writes an audit entry.
- **Quotas:**
  - Each quota refuses at limit + 1 with 429 and `Retry-After`.
  - **Concurrency, against Postgres and SQLite.** With one allowance left, N parallel requests (threads, each with its own connection) for the same quota let exactly one through, and record N-1 `quota_exceeded` events. Run this for a slow translation (the paid call is stubbed to sleep) and for a follow.
  - A pending reservation counts: while one translation is in progress at limit - 1, a second request is refused.
  - A failure before spend (stub raises a validation or provider error) releases the reservation. A later request succeeds.
  - A failure after spend (stub raises after "charging") commits it.
  - A `reserved` row older than the TTL is released by housekeeping and stops counting. A fresher one keeps counting.
  - No deadlock: parallel follows by several users of overlapping feeds all finish within a timeout.
  - Following an already-followed feed doesn't count as `feed_introduced`.
  - Admins are exempt. Overrides raise and lower limits. MCP tools hit the same quotas as REST.
- **Alerts:**
  - Each condition fires once and stays quiet for an hour.
  - The breaker flips `new_feeds_enabled` only when configured.
  - Email and webhook failures don't raise.
- **Console:**
  - The routes are admin-only.
  - An admin can't demote or disable themselves.
  - Every write produces an audit entry.
- **Single-user mode:** every no-op above holds, and the existing single-user test suite passes unchanged.

## Open questions

1. **Ban notice.** Should a banned user get an email? The proposal is no: the `/login` message is enough, and an email invites argument. Revisit if users ask.
2. **Account deletion.** Should "ban" eventually become "delete" after N days? `UserRepository.delete` exists but cascades to inbox and briefings. This is left to a GDPR-driven spec.
3. **Quota defaults.** The defaults in 2.2 are guesses. Phase 2 should look at real usage from `usage_events` in the first week, with limits logged but not enforced (`QUOTA_ENFORCE=false`) before switching on.
4. **Processing pause.** `read_only` stops what users can start, but feeds that are already followed keep being processed, and scheduled briefing scripts keep being written. A separate `processing_paused` switch would hold the pipeline queue and the briefing scheduler. Left out for now because spec #63's follower gate and the `spend_breaker` cover the realistic cases. Add it if an incident needs it.
5. **Per-IP signup limit.** Google accounts are cheap to make. If `signup_spike` fires in practice, add a cap on new accounts per IP per day, or move the default to `allowlist`.

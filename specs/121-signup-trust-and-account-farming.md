# Signup Trust and Account Farming

> **Status:** 📝 Draft (2026-11-17)
> **Created:** 2026-11-17
> **Updated:** 2026-11-17
> **Priority:** Medium (severity #4 of the anti-abuse series 118–123). Every spec #104 quota is per account, and a Google account costs a farmer minutes. Twenty accounts turn 10 new feeds and 5 imports a day into 200 and 100.
> **Related:** [#104 abuse-prevention-and-user-admin](104-abuse-prevention-and-user-admin.md) (signup gate, quotas, alerts, console; open question 5), [118-per-feed-processing-budget.md](118-per-feed-processing-budget.md) (what one introduced feed may cost), [119-audio-duration-cap.md](119-audio-duration-cap.md), [120-spend-guardrails.md](120-spend-guardrails.md), [122-introduced-feed-visibility.md](122-introduced-feed-visibility.md), [123-abuse-triage-sweep.md](123-abuse-triage-sweep.md) (reads the signals defined here), [#95 product-analytics](95-product-analytics.md) (`signup_source`), [#94 public-pages](94-public-pages.md) (privacy page)

---

## Executive summary

Spec #104 limits what one account can spend. It does nothing about one person holding many accounts. Signup is open to any Google identity, the new account gets the full allowance at once, and nothing records where it came from. A script that signs up `name1234`, `name1235`, … gets ten fresh allowances for ten sign-ins. The `signup_spike` alert fires only at 10 accounts in an hour, and its advice is to close signups for everyone.

This spec makes trust something an account earns:

1. **Signup signals.** At account creation, record a salted hash of the IP prefix (/24 or /64), a coarse browser family, Google's `email_verified` and `hd` claims, and the signup source. The IP hash is deleted after 30 days.
2. **Per-prefix signup cap.** Over N new accounts a day from one IP prefix, the next account is created *restricted* (it can read and follow existing shows, not bring in new ones) rather than refused. Runtime setting, env default, log-only first.
3. **Probation.** New accounts start at trust level `new`, with tighter limits on the costly quotas (`feeds_introduced_per_day`, `imports_per_day`, `translations_per_day`). They become `established` after 7 days *and* real reading, with no quota hits. Implemented inside `QuotaService`, not beside it. Admins can promote or restrict.
4. **Cluster alerts.** Alert when several accounts from one prefix bring in feeds or imports, or when a freshly brought-in feed is followed mostly by new or same-prefix accounts. That is the sock-puppet pattern spec 118 worries about.
5. **Admin surfacing.** Trust and "same network" columns, a same-prefix panel on the user page, and an audited "ban all in cluster" action, in the console and the CLI.

Single-user mode is unaffected.

## Current state

| Area | Today | Reference |
|---|---|---|
| Account creation | Only in the create branch of `handle_google_callback`, after the Google-id and email-linking lookups. The signup gate is the only check | [auth_service.py:188](../thestill/services/auth_service.py#L188), gate at [:276](../thestill/services/auth_service.py#L276), insert at [:278](../thestill/services/auth_service.py#L278) |
| Google claims read | `sub`, `email`, `name`, `picture` from the v3 userinfo endpoint (scope `openid email profile`). `email_verified` and `hd` are returned but ignored | [auth_service.py:50](../thestill/services/auth_service.py#L50), [:179](../thestill/services/auth_service.py#L179), [:238](../thestill/services/auth_service.py#L238) |
| Account linking | A new Google identity whose email matches an existing user is linked to it, without checking `email_verified` | [auth_service.py:261](../thestill/services/auth_service.py#L261) |
| Signup source | Stored once on `users.signup_source` (spec #95) | [auth_service.py:286](../thestill/services/auth_service.py#L286), [user.py:100](../thestill/models/user.py#L100) |
| Client IP at callback | Resolved only for region inference, then discarded | [auth.py:413](../thestill/web/routes/auth.py#L413), [:429](../thestill/web/routes/auth.py#L429) |
| Client IP resolution | Peer address, or the first untrusted `X-Forwarded-For` hop when the peer is in `TRUSTED_PROXIES` (CIDR-aware). Production trusts Docker's bridge range behind Caddy | [rate_limit.py:170](../thestill/web/middleware/rate_limit.py#L170), [docker-compose.prod.yml:53](../deploy/aws-ec2/docker-compose.prod.yml#L53) |
| Per-IP limit on sign-in | `AUTH_LIMIT`, 10 requests a minute per IP across `/api/auth`, in process | [rate_limit.py:66](../thestill/web/middleware/rate_limit.py#L66), [auth.py:52](../thestill/web/routes/auth.py#L52) |
| User-agent classing | `classify_user_agent` (browser, bot, preview) exists for page views | [user_agent.py](../thestill/utils/user_agent.py) |
| Quota limits | `limit_for` returns a `quota_overrides` entry or the env default. Admins exempt. No notion of account age | [quota_service.py:187](../thestill/services/abuse/quota_service.py#L187), [:227](../thestill/services/abuse/quota_service.py#L227) |
| Quota table | One `QuotaSpec` per quota (kind, window, lock group, env attr, message) | [quotas.py](../thestill/services/abuse/quotas.py) |
| Signup alert | `signup_spike`, ≥ 10 new users an hour, global, advises closing signups | [alerts.py:215](../thestill/services/abuse/alerts.py#L215), [config.py:577](../thestill/utils/config.py#L577) |
| Engagement facts | Inbox state (`read`, `saved`) with `state_changed_at`; listening positions. `user_episode_views` is analytics-only and not written when usage analytics is off | [postgres_schema.py:464](../thestill/repositories/postgres_schema.py#L464), [:485](../thestill/repositories/postgres_schema.py#L485), [analytics_request.py:41](../thestill/web/analytics_request.py#L41) |
| Admin list | Repository sorts `attention`, `created`, `last-login`, `follows`, `email`; the CLI exposes only the middle three | [user_directory_repository.py:28](../thestill/repositories/user_directory_repository.py#L28), [cli.py:5901](../thestill/cli.py#L5901) |
| Privacy page | Says request logs (with IP) are kept up to `log_retention` (30 days) for security and rate limiting | [privacy.md:15](../thestill/web/frontend/public/legal/privacy.md#L15), [operator.json](../thestill/web/frontend/public/legal/operator.json) |

## Threat

A farmer scripts Google sign-ins, through one machine or a residential proxy pool, and uses each account up to its quota:

- **Allowance multiplication.** Each account brings in 10 feeds and imports 5 episodes a day. Under spec #63 every introduced feed is processed for good. Spec 118 caps what one feed may cost; this spec caps how many accounts one actor can hold at full allowance.
- **Looking trusted.** Sock-puppet accounts follow the farmer's own feed so it looks popular. Specs 118 and 122 treat follower count as a trust signal, so it has to count independent followers.
- **Staying under alerts.** Nine signups an hour never trips `signup_spike`. Each account stays under its quota, so `quota_pressure` stays quiet too.

Today's numbers make farming visible. Real signups run at about 10 a week (spec #111's graded cohorts), so 5 accounts from one network in a day are unusual.

## Goals

1. A burst of accounts from one network gets at most a few full-allowance accounts a day. The rest can use the product but can't bring in spend.
2. A new account can spend far less than an established one until it has behaved like a reader.
3. The operator is told about a likely farm by name (the accounts, the feed), not by a global count.
4. The operator can review a cluster and ban it in one audited action, from the console or the CLI.
5. The data kept for this is minimal, pseudonymous and deleted on a schedule, and the privacy page says so.
6. A real person behind a university NAT or mobile CGNAT can still sign up and read.

## Non-goals

- **Proving identity.** No phone, CAPTCHA or payment check. Google already does some of that, and it adds friction for everyone.
- **Device fingerprinting.** No canvas, font or cookie fingerprint. The browser family is the only client trait kept.
- **Storing full IP addresses** on accounts. Request logs keep them for 30 days as they do today. This spec stores none.
- **Automatic bans.** As in spec #104, the system limits and alerts. Banning stays a human decision. LLM review of new accounts is spec 123.
- **Per-feed cost.** Specs 118–120.

## Design

### 1. Signup signals

New table `signup_signals`, one row per account, written in the create branch right after the user row:

| Column | Type | Notes |
|---|---|---|
| `user_id` | uuid, primary key, `ON DELETE CASCADE` | |
| `created_at` | timestamptz | The signup instant |
| `ip_prefix_hash` | text, null | See below. Null when the IP was not a public address, and after retention |
| `ip_version` | smallint, null | 4 or 6 |
| `ua_family` | text | `chrome`, `safari`, `firefox`, `edge`, `other-browser`, `bot`, `none`. No version, no OS |
| `email_verified` | boolean, null | Google's claim |
| `hosted_domain` | text, null | Google's `hd` claim (Workspace accounts only) |
| `email_kind` | text | `consumer` (gmail.com, googlemail.com), `workspace` (`hd` set) or `other` |

`signup_source` stays on `users`. It is not copied.

**The IP prefix.** `resolve_client_ip` gives the client address. If it is not a public, routable address (reuse `_is_routable_public_ip`, [geoip.py:45](../thestill/utils/geoip.py#L45)), no prefix is recorded. That covers a deployment without `TRUSTED_PROXIES`, where every client looks like the proxy and one key would cap everyone. Otherwise the address is truncated to its /24 (IPv4) or /64 (IPv6) network and hashed:

```python
ip_prefix_hash = hmac_sha256(signal_key, f"v{version}|{network}")[:32]  # hex
```

`signal_key` is `SIGNUP_SIGNAL_SECRET`, or else derived from `JWT_SECRET_KEY` with a fixed label, as `login_flow_key` does ([analytics_request.py:54](../thestill/web/analytics_request.py#L54)). The key must stay stable for the retention window, so it is not rotated daily like the visitor key. Rotating it only breaks matching across the rotation.

**Browser family.** A new `browser_family(user_agent)` in [user_agent.py](../thestill/utils/user_agent.py) reuses `classify_user_agent` for `bot`, then picks a family by the usual tokens (Edge before Chrome, Chrome before Safari). A headless browser already counts as `bot`.

**Email claims.** `handle_google_callback` passes `email_verified` and `hd` through. Two rules use them:

- An unverified email never links to an existing account (the linking branch skips to "create"). This is a fix in its own right, independent of farming.
- An account created with `email_verified = false` starts `restricted`.

`handle_google_callback` gains a `client` argument (`ip`, `user_agent`), filled by the callback route, which already resolves the IP. Signal writing is best-effort like region inference: a failure logs `signup_signals_failed` and never blocks sign-in. The signals table has its own repository (ABC, SQLite, Postgres), like every spec #104 table.

### 2. Per-prefix signup cap

Two runtime settings in [settings_registry.py](../thestill/services/abuse/settings_registry.py), with env defaults in [config.py](../thestill/utils/config.py):

| Key | Env | Default | Meaning |
|---|---|---|---|
| `signup_prefix_daily_cap` | `SIGNUP_PREFIX_DAILY_CAP` | 3 | New accounts per IP prefix in a rolling 24h before the action applies. `0` turns the cap off |
| `signup_prefix_action` | `SIGNUP_PREFIX_ACTION` | `log` | `log`, `restrict` or `refuse` |

The check runs in the create branch after the signup gate, counting `signup_signals` rows with the same `ip_prefix_hash` in the last 24h:

- **`log`** writes `signup_prefix_over_cap` (with the first 8 characters of the hash and the count) and creates a `new` account.
- **`restrict`** creates the account at trust level `restricted` (§3). The reader is not told at signup. They find out only if they try to bring in a feed or import (§3 messages).
- **`refuse`** raises `SignupRefusedError` with a new error `signups_limited`. `/login` says "Too many accounts were created from your network today. Try again tomorrow, or contact <operator>."

`restrict` is the intended steady state. A class demo at a university, or a family behind one router, still gets in and can read and follow shows that are already processed. Only the costly paths wait for an admin or for the account to earn trust. `refuse` is for an active attack.

An email or domain on `signup_allowlist` skips the cap in every signup mode. This gives the operator a way to let a known organisation in.

The count and the insert are not locked together. Concurrent signups from one prefix can overshoot the cap by a few. `AUTH_LIMIT` (10 requests a minute per IP) bounds the burst, and §4 alerts on the result. A lock across the OAuth callback isn't worth it.

**Shared networks.** Mobile carriers' CGNAT and large NATs put unrelated people behind one IPv4 /24. At about 10 signups a week across the whole site, two unrelated signups from one /24 in a day are rare, and three are rarer still. IPv6 /64 is normally one household or one phone. An attacker with a routed /48 can spread across 65,536 /64s. That is open question 1.

### 3. Trust levels and probation

Add to `users`:

| Column | Type | Notes |
|---|---|---|
| `trust_level` | text, not null, default `'established'` | `restricted`, `new` or `established` |
| `trust_pinned` | boolean, not null, default false | Set when an admin chose the level. The promotion job leaves pinned rows alone |
| `trust_changed_at` | timestamptz, null | |

The column default is `established`, so existing users and the single-user default user need no backfill to keep today's limits. The create branch sets `new` (or `restricted`, per §1 and §2). The migration sets `new` for accounts created in the last 7 days. `trust_level` joins `PRIVATE_USER_FIELDS` ([user.py:42](../thestill/models/user.py#L42)): the reader never sees a label.

**Limits by level, in `QuotaService`.** `QuotaSpec` gains `probation_attr: Optional[str]`, a `Config` field with the limit for `new` accounts. No parallel system: `limit_for` takes the user and resolves in this order:

1. a `quota_overrides` entry (the admin decided);
2. `restricted` and the quota has a `probation_attr`: blocked (below);
3. `new` and the quota has a `probation_attr`: the probation limit;
4. the env default.

| Quota | Established | New (probation) | Env | Restricted |
|---|---|---|---|---|
| `feeds_introduced_per_day` | 10 | 2 | `QUOTA_PROBATION_FEEDS_INTRODUCED_PER_DAY` | none |
| `imports_per_day` | 5 | 2 | `QUOTA_PROBATION_IMPORTS_PER_DAY` | none |
| `translations_per_day` | 20 | 5 | `QUOTA_PROBATION_TRANSLATIONS_PER_DAY` | none |
| follows, narrations | unchanged | unchanged | | unchanged |

Separate limits rather than a multiplier: a multiplier on single digits rounds badly (0.2 × 5 = 1, 0.2 × 10 = 2), and each probation limit is a decision worth stating. Following a show someone already follows stays generous at every level, since it costs almost nothing.

**Restricted is not limit 0.** In spec #104, `0` means unlimited. A restricted account's blocked quotas are refused before counting, through the same `reserve` path and the same `FollowLimits.breach` check. The refusal is a `QuotaExceededError` with `retry_after = None` and a new optional `reason` field (`"trust"`), so routes, MCP tools and the frontend need no new error. It writes a `quota_exceeded` ledger row like any refusal.

Messages, written by the server:

- new: "New accounts can add 2 new podcasts a day in their first week. You can add more in 6 hours."
- restricted: "Adding new podcasts isn't available on this account yet. Contact <operator> if you need it."

**Earning trust.** A periodic job on the spec #104 runner (hourly, [housekeeping.py:39](../thestill/services/abuse/housekeeping.py#L39) pattern) promotes an unpinned `new` account to `established` when all of these hold:

| Condition | Default | Env |
|---|---|---|
| Account age | ≥ 7 days | `TRUST_PROBATION_DAYS` |
| Reading | ≥ 3 inbox episodes moved to `read` or `saved`, or ≥ 1 listening position past 60 seconds | `TRUST_MIN_READS` |
| No quota hits | no `quota_exceeded` row in the last 7 days | |
| Not in an open cluster | no §4 cluster alert naming the account in the last 7 days | |

Reading uses `user_episode_inbox` and `user_episode_progress`. Both are facts the product needs to work. It never uses `user_episode_views` or `listened_at`, which exist only to measure and disappear when a reader turns analytics off, so turning analytics off doesn't slow promotion. Promotion logs `user_trust_promoted` and is not written to the audit log, which stays for admin actions. A `restricted` account is never promoted automatically.

**Admin control.** `AccountAdminService.set_trust(user_id, level | "auto", actor)` sets the level and pins it, or unpins it (`auto`) and lets the job decide. Audited as `user.trust_set` with old and new values. Granting admin also sets `established` (admins are exempt from quotas anyway).

**Quota enforcement switch.** Probation refusals respect `QUOTA_ENFORCE`. A new `TRUST_ENFORCE` (default `false` at first) does the same for this spec alone: when off, a refusal that only the probation or restricted limit would cause is logged as `quota_would_refuse` with `reason=trust` and allowed.

### 4. Cluster alerts

Three new rules in `AlertService.evaluate` ([alerts.py:176](../thestill/services/abuse/alerts.py#L176)), thresholds in the `alerts` setting with `ALERT_*` env defaults, de-duplicated per cluster through the existing atomic claim (`alerts.last_sent.<kind>.<key>`):

| Alert | Fires when | Default | Key |
|---|---|---|---|
| `signup_cluster` | ≥ K accounts created from one prefix in 24h, whatever `signup_prefix_action` is | 3 | prefix hash (first 12 chars) |
| `introducer_cluster` | ≥ K accounts from one prefix, each under 30 days old, committed `feed_introduced` or `import` rows in the last 24h | 2 | prefix hash |
| `follow_cluster` | a feed brought in within 14 days has ≥ K followers, and ≥ 75% of them are `new`/`restricted` or share one prefix | 3 | podcast id |

Each alert names the accounts (email, trust, age) and, for `follow_cluster`, the feed and who brought it in. It links to the user page's cluster panel and suggests `thestill user ban-cluster`. Machine-facing `details` carry user ids, podcast id and counts only, as spec #104 requires. No raw IP exists to leak.

`signup_spike` stays as the global backstop.

**Independent followers.** For specs 118 and 122, this spec defines one helper, `TrustSignals.independent_followers(podcast_id)`. It counts the feed's followers that are `established`, not banned, and come from distinct prefixes (accounts with no prefix count once each). "Follower count" means this whenever it is used as a trust signal, so sock puppets don't add to it.

### 5. Signals for spec 123

`TrustSignals.for_user(user_id)` returns one read model that spec 123's triage sweep consumes and the console shows:

| Field | Source |
|---|---|
| `trust_level`, `trust_pinned`, `account_age_days` | `users` |
| `signup_source`, `ua_family`, `email_kind`, `email_verified`, `hosted_domain` | `users`, `signup_signals` |
| `same_prefix_accounts_24h`, `same_prefix_accounts_30d` | `signup_signals` (null once the hash is gone) |
| `minutes_to_first_feed_introduced`, `feeds_introduced_7d`, `imports_7d` | `usage_events` |
| `quota_exceeded_7d` | `usage_events` |
| `reads_7d` | `user_episode_inbox`, `user_episode_progress` |
| `cluster_alerts_7d` | the alert claim rows |

### 6. Admin surfacing

**CLI** (straight against the database, audited with `actor = 'cli'`, exit 1 in single-user mode):

```bash
thestill user list [--trust new|restricted|established] [--sort attention|created|last-login|follows|email|cluster]
thestill user show <email-or-id>                 # gains: trust, signup signals, same-network accounts
thestill user cluster <email-or-id> [--json]     # accounts sharing the signup prefix (30 days)
thestill user trust <email-or-id> new|restricted|established|auto
thestill user ban-cluster <email-or-id> --reason "..." [--remove-introduced-feeds] [--exclude ID]... [--apply]
```

`--sort` exposes the repository's existing `attention` and `email` sorts plus a new `cluster` (size of the account's 24h prefix cohort). `ban-cluster` is a dry run unless `--apply`. It lists the anchor and every same-prefix account from the last 30 days, and skips admins and pinned `established` accounts. Each ban goes through `AccountAdminService.ban` ([account_admin_service.py:282](../thestill/services/abuse/account_admin_service.py#L282)) and writes its own `user.ban`. One extra `user.ban_cluster` entry records the anchor, the reason, the banned ids and the excluded ids.

**Console.**

- `/admin/users` gains a Trust badge column, a "Same network" count (24h prefix cohort), a trust filter, and the `cluster` sort.
- `/admin/users/:id` gains a Signup panel: source, browser family, email kind and verified flag, Workspace domain. It also lists the same-network accounts with age, trust, feeds brought in and status, each linked, with a checkbox. Actions: set trust (with `auto`), and **Ban selected** (reason required, "also remove feeds they brought in" checkbox), which posts the checked ids.

**API** (under `/api/admin`, `require_admin`, audited):

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/admin/users/{id}/signals` | The §5 read model and the cluster list |
| PUT | `/api/admin/users/{id}/trust` | Body `{level}`, where `auto` unpins |
| POST | `/api/admin/users/{id}/ban-cluster` | Body `{user_ids, reason, remove_introduced_feeds}`. The server re-checks that each id shares the anchor's prefix and refuses admins |

`AUDIT_ACTIONS` ([audit_service.py:31](../thestill/services/abuse/audit_service.py#L31)) gains `user.trust_set` and `user.ban_cluster`.

### Data model changes

| Change | Notes |
|---|---|
| New `signup_signals` | Index on `(ip_prefix_hash, created_at)` |
| `users`: `trust_level`, `trust_pinned`, `trust_changed_at` | `CHECK (trust_level IN ('restricted','new','established'))` |

As in spec #104: an Alembic revision (the next free number after `0028`, shared with the sibling specs), the same idempotent DDL in `postgres_schema.SCHEMA_SQL`, and the SQLite table plus `ALTER TABLE` in `SqlitePodcastRepository._run_migrations` ([sqlite_podcast_repository.py:249](../thestill/repositories/sqlite_podcast_repository.py#L249)).

## Privacy

What is stored per account, and for how long:

| Data | Why | Kept |
|---|---|---|
| Keyed hash of the IP's /24 or /64 network | Count accounts from one network | 30 days (`SIGNUP_SIGNAL_RETENTION_DAYS`), then set to null by the housekeeping job. Matches request-log retention |
| Browser family | Spot scripted signups | While the account exists |
| `email_verified`, Workspace domain, email kind | Spot unverified or bulk-made accounts | While the account exists |
| Trust level | Quota limits | While the account exists |

- The full IP is never stored on the account, and the stored hash can't be reversed without the server key. It is still personal data (pseudonymous), processed for fraud prevention under legitimate interests (GDPR Recital 47).
- None of it goes to PostHog, alerts' webhook details or logs beyond an 8-character hash prefix. Global Privacy Control does not switch it off, because it is security processing, not analytics.
- Deleting an account deletes its signals (cascade).
- The privacy page ([privacy.md](../thestill/web/frontend/public/legal/privacy.md)) gains a paragraph under the request-log one: "When you create an account we keep a scrambled code made from the first part of your IP address, and the kind of browser you used, to stop one person making many accounts. We delete the code after {{log_retention}}." The retention table gains a row for it. Ship the page change with Phase 1.

## Rollout

Log-only first. Each phase is a PR.

1. **Signals.** The table, the callback changes, the `email_verified` linking fix, retention cleanup, the privacy page. The cap runs with `signup_prefix_action = log`. The `signup_cluster` and `introducer_cluster` alerts go to the operator.
2. **Trust levels.** The columns, probation limits in `QuotaService`, the promotion job and admin `set_trust`, with `TRUST_ENFORCE=false`. After two weeks, count `quota_would_refuse reason=trust` events from real accounts. If no real reader would have been refused, turn enforcement on.
3. **Cap action and clusters.** Set `signup_prefix_action = restrict`. Add `follow_cluster` and `independent_followers` (specs 118 and 122 can start using it).
4. **Console and CLI.** Columns, the signup panel, `user cluster`, `user trust`, `ban-cluster`. The CLI parts can ship with Phase 1 if a farm shows up earlier.

`docs/configuration.md` gains the new env vars. `CLAUDE.md` gains the `user cluster`, `user trust` and `user ban-cluster` commands.

## Testing

- **Signals:**
  - IPv4 and IPv6 addresses in one /24 or /64 hash alike, and different networks hash differently.
  - A private, loopback or proxy address records no prefix and never trips the cap.
  - A failure writing signals doesn't block sign-in.
  - The housekeeping job nulls hashes past retention and keeps the other columns.
- **Linking:** an unverified email doesn't link to an existing account. A verified one still does. An unverified new account starts `restricted`.
- **Cap:**
  - Each action (`log`, `restrict`, `refuse`) at cap + 1.
  - An allowlisted email skips the cap.
  - An existing user from a capped prefix always signs in.
- **Trust and quotas:**
  - Each probation limit refuses at limit + 1 with the probation message.
  - A restricted account is refused at its first introduction or import, writes `quota_exceeded`, and can still follow an already-followed feed.
  - An override beats the trust level. Admins are exempt.
  - `TRUST_ENFORCE=false` logs and allows.
  - The spec #104 concurrency test passes again with a probation limit.
- **Promotion:**
  - The job promotes exactly when every condition holds.
  - Reads count with usage analytics off.
  - A quota hit or a cluster alert holds promotion.
  - Pinned and restricted accounts are never changed.
- **Clusters:**
  - Each rule fires once per cluster per hour, and names the accounts.
  - `independent_followers` counts one per prefix and excludes `new` accounts.
- **Admin:**
  - `ban-cluster` dry-runs by default and skips admins and pinned established accounts.
  - The API refuses an id outside the anchor's cluster.
  - Every write is audited.
- **Single-user mode:** no signals, no cap, every user `established`, the existing suite passes unchanged.

## Open questions

1. **IPv6 width.** /64 is a household, but a /48 costs an attacker nothing. Should the cap also count a /48 at a higher threshold (for example 10 a day)?
2. **Residential proxy pools** defeat any prefix rule. Probation and `follow_cluster` are the defence there. Is browser family plus signup timing worth a fourth cluster rule, or is that spec 123's job?
3. **Probation limits.** 2 feeds and 2 imports a day for a week may block a real power user on day one. Phase 2's log-only fortnight should answer this. An onboarding flow that imports a whole OPML file would hit it first.
4. **Telling the reader.** Should the new-account limit be visible up front (for example in Settings), or only in the refusal message as proposed?
5. **Workspace domains.** A farmer with their own Workspace domain gets `email_verified` accounts with one `hd`. Should the cap also count accounts per `hd` (excluding large known domains)?

# Legacy Account Claim

> **Status:** 🚧 Implemented on `feat/64-legacy-account-claim` (2026-07-23, stacked on `feat/63`) — atomic claim/discard repository (both backends), auto-claim on first real login, `claim-local-user` CLI. Dual-backend contract tests green.
> **Created:** 2026-07-23
> **Updated:** 2026-07-23
> **Author:** Engineering (auth / data migration)
> **Related:** [#13 multi-user-shared-podcasts](13-multi-user-shared-podcasts.md) (introduced the synthetic local user this spec retires on cutover), [#29 per-user-inbox-fanout](29-per-user-inbox-fanout.md) / [#50 briefing-scheduling](50-briefing-scheduling.md) (the per-user data being transferred), [#63 universal-follower-gate](63-universal-follower-gate.md) (companion: makes follows the pipeline-demand signal this migration cleans up)

---

## Executive summary

Before multi-user mode, the single-user identity `local@thestill.me`
(`AuthService.DEFAULT_USER_EMAIL`) accumulated the operator's real
preference data: podcast follows (blanket-auto-followed on every boot),
inbox rows, briefings, and a briefing schedule. After switching to
`MULTI_USER=true` that account is frozen ballast — its follows keep the
whole fleet "followed" (defeating spec #63's gate), and the operator's
real Google account starts empty.

This spec models the transition explicitly: **the local account is the
person before they had auth, and their first real login claims it.**

- **Auto-claim** — when the OAuth callback creates a brand-new user
  (`is_new_user`), the route best-effort transfers the local account's
  data to them and grants `is_admin`. Gated purely on the local row's
  existence: a successful claim deletes the row, so the trigger
  self-limits; a claim failure never blocks login.
- **CLI** — `thestill claim-local-user --to <email> | --discard
  [--dry-run]` for databases where real users already exist (the auto
  path never fires for them), and for operators who want the local data
  gone rather than transferred.
- **Deleting the local `users` row is the durable idempotency marker.**
  `ON DELETE CASCADE` sweeps the leftovers; single-user mode recreates
  the row fresh via `get_or_create_default_user` if the operator ever
  reverts.

## 1. Design

### 1.1 One-transaction repository (the correctness core)

[`LegacyClaimRepository`](../thestill/repositories/legacy_claim_repository.py)
(ABC) with SQLite and Postgres implementations. The entire operation runs
in ONE row-locked transaction — `BEGIN IMMEDIATE` (SQLite) /
`SELECT … FOR UPDATE` on the local `users` row (Postgres) — so two
concurrent claim attempts (racing first logins, a login racing the CLI)
resolve to exactly one winner; the loser unblocks and observes
`found=False`. Nothing partially commits: a crash rolls back to the
untouched pre-call state, so retries are always safe. This is why the
transfer is NOT spread across the four existing per-user repositories:
independent connections cannot make "move an account's data" atomic, and
a TOCTOU race could split the data between two destinations.

Statement order inside the transaction:

1. `podcast_followers` → `UPDATE … SET user_id = target` guarded by
   `NOT EXISTS` on `(target, podcast_id)` — collisions stay behind.
2. `user_episode_inbox` → same shape, keyed on `episode_id`.
3. `user_briefings` → unconditional reassign (no uniqueness);
   `briefing_deliveries` ride along via `briefing_id`, untouched.
4. `user_briefing_schedules` (`user_id` IS the PK) → move only if the
   target has none.
5. `UPDATE users SET is_admin = true` on the target (the local operator
   was the admin; the claimant inherits it — `UserRepository.save()`
   deliberately never writes `is_admin`).
6. `DELETE FROM users WHERE id = local` — **last**: cascades away the
   deliberately-skipped collision rows and durably marks the claim done.

`discard_local_account` is steps counts + 6 only: one `DELETE`, FK
cascade does the rest (verified on both backends by contract tests,
including the transitive `briefing_deliveries` cascade).

`dry_run=True` on either method only counts — no writes. A repo-level
guard also refuses `target == local` (defense in depth below the
service's email check).

### 1.2 Trigger points

- **Auto**: `handle_google_callback` now returns
  `(user, jwt, is_new_user)`; the callback route
  ([`auth.py`](../thestill/web/routes/auth.py)) calls
  `legacy_claim_service.claim_for_new_user(user)` when `is_new_user` —
  directly next to the existing `maybe_infer_region` best-effort
  side-effect, and inside the same never-blocks-login contract
  (`claim_for_new_user` swallows and logs all errors).
  *Deliberate consequence*: if the operator toggles back to single-user
  (recreating the local row) and later a new user logs in, that user
  absorbs the interim local data and admin. Accepted per design review —
  the row-existence gate is the simpler, self-limiting policy.
- **CLI**: [`claim-local-user`](../thestill/cli.py) resolves `--to
  <email>` via `UserRepository.get_by_email` (fails fast before any
  transaction; refuses the local email itself), threads `--dry-run`,
  and prints per-table counts. `--discard` covers the "my real account
  already has the follows I want" case.

### 1.3 Wiring

`RepositoryBundle.legacy_claim` (both backends,
[`factory.py`](../thestill/repositories/factory.py)) →
[`LegacyClaimService`](../thestill/services/legacy_claim_service.py)
constructed in `web/app.py` (→ `AppState.legacy_claim_service`) and
`cli.py` (→ `CLIContext.legacy_claim_service`). `AuthService` itself
gained no new dependency — only the `is_new_user` return element.

## 2. Testing

- [`tests/integration/test_legacy_claim_repository_contract.py`](../tests/integration/test_legacy_claim_repository_contract.py)
  — dual-backend (SQLite + real Postgres), raw-SQL white-box: full
  transfer, collision-skip + schedule-if-absent (skipped rows cascade
  with the local row), admin grant, dry-run writes nothing, idempotency
  (second claim → `found=False`), self-claim refusal, discard cascade
  including transitive `briefing_deliveries`.
- [`tests/unit/services/test_legacy_claim_service.py`](../tests/unit/services/test_legacy_claim_service.py)
  — target resolution before any transaction, self-claim guard,
  `claim_for_new_user` never raises.
- [`tests/unit/cli/test_claim_local_user.py`](../tests/unit/cli/test_claim_local_user.py)
  — CliRunner against a real tmp SQLite DB: transfer, dry-run, unknown
  target, discard, exactly-one-mode validation, gone-is-noop.

## 3. Operator runbook (this deployment)

The live Postgres DB predates the auto-claim (two real users exist), so
the auto path will never fire there. One-time cleanup, in order:

1. Deploy #63 + #64.
2. `thestill claim-local-user --discard --dry-run` — verify the counts
   (expected: ~90 follows, plus inbox/briefing rows).
3. `thestill claim-local-user --discard` — the ~39 podcasts only the
   local account followed go dormant under #63's gate; the real
   account's 51 follows keep processing.
4. Snapshot `get_refresh_health_counts` before/after to confirm the
   expected `active` drop.

(To transfer instead of discard: `--to <your-email>` — then prune in the
UI.)

## Open questions

- Concurrency contract test exercises the lock indirectly (idempotent
  second claim); a true two-thread race test against Postgres was
  considered and deferred — the lock primitives (`BEGIN IMMEDIATE`,
  `FOR UPDATE`) are DB-guaranteed and the loser path is covered by the
  gone-is-noop tests.
- Should `/auth/status` surface a one-time `claimed_legacy_data` flag so
  the frontend can toast "we imported your library"? Deferred — silent
  server-side migration matches house style.

## Revision history

- **v1 (2026-07-23)** — Initial implementation: atomic
  `LegacyClaimRepository` (SQLite `BEGIN IMMEDIATE` / Postgres
  `FOR UPDATE`), `LegacyClaimService`, `is_new_user` from
  `handle_google_callback`, route-layer auto-claim, `claim-local-user`
  CLI, dual-backend contract suite. Design choice: row-existence gate
  instead of a first-user count check (accepted trade-off documented in
  §1.2).

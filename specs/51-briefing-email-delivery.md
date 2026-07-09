# Briefing Email Delivery

> **Status:** 🚧 Implemented Phases 1–3 (2026-07-08); Phase 4 (SES bounce hardening) pending #43
> **Created:** 2026-07-07
> **Author:** Product & Engineering
> **Related:** [#50 scheduled-briefings](50-scheduled-briefings.md), [#36 per-user-digest-from-inbox](36-per-user-digest-from-inbox.md), [#43 aws-hosting](43-aws-hosting.md), [#34 briefing-audio-and-feeds](34-briefing-audio-and-feeds.md)

---

## Executive Summary

[#50](50-scheduled-briefings.md) makes the briefing *exist* by each user's
scheduled hour. This spec makes it *arrive*: the scheduled run emails the
briefing to the user, so the morning ritual starts in the inbox they already
check instead of requiring them to open the app.

The core design move is **decoupling "generated" from "delivered"**. A
briefing row is produced once (scheduled or lazy trigger — #36 semantics
unchanged); a *delivery* is a separate record with its own state machine
(pending → sent / failed), its own bounded retries, and the rule "send if
this briefing hasn't been emailed yet" — never "send if a new briefing was
generated". That distinction is what keeps the 7:30-lazy-open /
8:00-scheduled-run interaction correct: generation is throttled to the
existing briefing, but the email still goes out, exactly once.

Delivery channels were an explicit non-goal of #50; this is the follow-up it
pointed at.

---

## Motivation

1. **The briefing shouldn't require a visit.** #50 gets the artifact ready
   by 8am, but the user still has to remember to open the app. Email is the
   push channel every account already has (`users.email` is populated by
   auth).
2. **"Generated" and "delivered" are different facts.** The #50 scheduler
   deliberately returns the existing briefing when generation is throttled.
   Without a delivery record, an email step naively keyed on "a new briefing
   was created" silently drops the send in the lazy-then-scheduled case —
   an FM-4-shaped silent degradation.
3. **Email failure must not look like briefing failure.** SMTP/SES being
   down at 8am must not lose the briefing (it's persisted), must not block
   other users' generations, and must retry on its own cadence.

---

## Product Requirements

### User stories

| As a... | I want... | So that... |
|---|---|---|
| User | My scheduled briefing emailed to me | The morning ritual starts in my inbox, no app visit needed |
| User | The email to link back to episodes, the full script, and audio | I can jump into anything that catches my eye |
| User | To turn email delivery off while keeping the schedule | I can prefer the in-app card without losing automation |
| User | An unsubscribe link that works | One click stops the emails (also: the law) |
| Self-hoster | SMTP config via env vars | I can use my own relay without an AWS account |

### Core behaviors

1. **Delivery is opt-in per user, on the schedule row.** A new
   `email_enabled` flag on `user_briefing_schedules` (default false). The
   #50 settings UI grows one checkbox. Email delivery requires an enabled
   schedule — no schedule, no sends.
2. **Send-once semantics via delivery records.** When the scheduler's slot
   fires and `email_enabled` is set, it *ensures a delivery row exists* for
   (briefing_id, channel='email') — whether `generate_for_user` returned a
   fresh briefing or the throttled existing one. Sending is driven off
   pending delivery rows, so a briefing is emailed at most once no matter
   how many triggers touch it.
3. **Empty window → no email.** A `None` from `generate_for_user` produces
   no delivery row. Honest silence over "nothing new" filler (matches #36's
   empty-state philosophy). Revisit for weekly cadence if silence proves
   ambiguous with breakage — see Open Questions.
4. **Delivery retries independently, bounded.** A failed send increments
   `attempts`, sets `next_attempt_at` with backoff, and never touches the
   briefing or the schedule cursor. After `max_attempts` (default 3) the
   delivery parks as `failed` and surfaces in logs/status — it does not
   burn every tick (FM-1 discipline from #49/#50 applied to sends).
5. **Email body is standalone HTML.** Rendered from the briefing script
   markdown with absolute URLs built from a new `PUBLIC_BASE_URL` config —
   episode links, "Read in app", audio link when #34 lands. Plain-text
   alternative part included (deliverability + accessibility).
6. **Unsubscribe is honored without login.** A signed one-click unsubscribe
   link (token embedding user_id, HMAC over the app secret) sets
   `email_enabled = false`. `List-Unsubscribe` + RFC 8058 one-click headers
   included. Legal requirement (CAN-SPAM/GDPR), not a nicety.
7. **Provider-pluggable transport.** `EmailSender` interface with two v1
   implementations: `SmtpEmailSender` (env-configured, self-host default)
   and `SesEmailSender` (aligns with the #43 AWS story). Selected via
   `EMAIL_PROVIDER`.

### Non-Goals

- **Other channels** (push, Slack, Telegram). The delivery-record shape
  has a `channel` column so they slot in later; only `email` ships here.
- **Digest-style marketing/analytics** (open tracking, click tracking).
- **Per-episode notification emails.** This is one briefing email per
  scheduled slot, nothing more granular.
- **Bounce-driven auto-disable.** v1 logs provider errors; wiring SES
  bounce/complaint SNS callbacks to auto-disable is a fast-follow (noted
  in Open Questions).
- **Reply handling.** The from-address is no-reply.

---

## Architecture Overview

```
┌────────────────────────────────────────────────────────────────────┐
│  BriefingScheduler tick (#50) — slot fires for user U              │
│    briefing = BriefingService.generate_for_user(U)                 │
│    if briefing and schedule.email_enabled:                         │
│        BriefingDeliveryService.ensure_pending(briefing.id, 'email')│
└────────────────────────────────────────────────────────────────────┘
                              │  (same tick, after slot loop)
┌────────────────────────────────────────────────────────────────────┐
│  Delivery pass: pending/retryable deliveries, oldest first         │
│    for each: render email (script.md → HTML + text,                │
│              absolute links via PUBLIC_BASE_URL)                   │
│              EmailSender.send(...)                                 │
│              → sent_at   | attempts+1 + backoff | parked 'failed'  │
└────────────────────────────────────────────────────────────────────┘
                              │
┌────────────────────────────────────────────────────────────────────┐
│  briefing_deliveries (new table): briefing_id · channel · status · │
│  attempts · next_attempt_at · sent_at · last_error                 │
└────────────────────────────────────────────────────────────────────┘
```

The delivery pass runs inside the existing scheduler loop (a second phase
of `tick()`), not a new daemon: the fleet is user-sized, sends are
I/O-cheap, and the pending-scan is indexed. If #34 audio attachments or
fleet growth make sends heavy, the pass promotes to its own worker the
same way #50 reserved a queue-stage escape hatch for generation. The
episode-centric task queue is deliberately *not* used — a delivery is
keyed by briefing + user, and contorting the queue's
`episode_id`/`podcast_id` invariant (#48) for it buys nothing at this
scale.

Claim semantics: a delivery is claimed with the same conditional-UPDATE
idiom as #50 slots (`status = 'pending' AND next_attempt_at <= now` →
`status = 'sending'`), so multi-instance deployments don't double-send.

---

## Database Schema Changes

### `user_briefing_schedules` — one new column

```sql
ALTER TABLE user_briefing_schedules
    ADD COLUMN email_enabled INTEGER NOT NULL DEFAULT 0;  -- boolean on PG
```

### `briefing_deliveries` — new table

```sql
CREATE TABLE IF NOT EXISTS briefing_deliveries (
    id              TEXT PRIMARY KEY NOT NULL,           -- uuid on PG
    briefing_id     TEXT NOT NULL,
    channel         TEXT NOT NULL DEFAULT 'email',
    status          TEXT NOT NULL DEFAULT 'pending',
    attempts        INTEGER NOT NULL DEFAULT 0,
    next_attempt_at TIMESTAMP NULL,                      -- NULL once terminal
    sent_at         TIMESTAMP NULL,
    last_error      TEXT NULL,
    created_at      TIMESTAMP NOT NULL
                    DEFAULT (strftime('%Y-%m-%dT%H:%M:%f+00:00','now')),

    FOREIGN KEY (briefing_id) REFERENCES user_briefings(id) ON DELETE CASCADE,
    UNIQUE (briefing_id, channel),                       -- send-once anchor
    CHECK (channel IN ('email')),
    CHECK (status IN ('pending','sending','sent','failed')),
    CHECK (attempts >= 0)
);

CREATE INDEX IF NOT EXISTS idx_briefing_deliveries_due
    ON briefing_deliveries(next_attempt_at)
    WHERE status IN ('pending','sending');
```

The `UNIQUE (briefing_id, channel)` constraint *is* the send-once rule:
`ensure_pending` is an `INSERT … ON CONFLICT DO NOTHING`, so racing
triggers collapse to one delivery. Recipient email is resolved from
`users` at send time (via the briefing's `user_id`), not denormalized —
an address change between generation and retry sends to the current
address.

---

## Configuration

```bash
EMAIL_PROVIDER=smtp            # smtp | ses | none (default: none = delivery off globally)
EMAIL_FROM="Thestill <briefings@example.com>"
PUBLIC_BASE_URL=https://app.example.com   # absolute links in email bodies

# smtp provider
SMTP_HOST=…  SMTP_PORT=587  SMTP_USERNAME=…  SMTP_PASSWORD=…  SMTP_STARTTLS=true

# ses provider (uses the ambient AWS credential chain, #43)
SES_REGION=eu-central-1

BRIEFING_EMAIL_MAX_ATTEMPTS=3
BRIEFING_EMAIL_BACKOFF_SECONDS=300   # doubled per attempt
```

`EMAIL_PROVIDER=none` short-circuits the delivery pass entirely, so #50
deployments without email config pay zero overhead and `email_enabled`
checkboxes are hidden in the UI (surfaced via a capability flag on an
existing status/config endpoint).

---

## Service Layer Changes

- **New: `BriefingDeliveryService`** — `ensure_pending(briefing_id, channel)`,
  `deliver_due(now)` (claim → render → send → settle), backoff math.
  Owns no SMTP details.
- **New: `EmailSender` ABC** + `SmtpEmailSender` (stdlib `smtplib`, no new
  dependency) and `SesEmailSender` (boto3, lazy-imported — same pattern as
  the Postgres repos so self-host installs never import it).
- **New: `BriefingEmailRenderer`** — script markdown → (html, text) parts.
  Reuses the existing markdown pipeline; absolute URLs from
  `PUBLIC_BASE_URL`; unsubscribe token via `itsdangerous`-style HMAC
  signing with the app secret (mechanism already available to the auth
  layer).
- **`BriefingScheduler` (#50)** — slot loop additionally calls
  `ensure_pending` when `email_enabled`; tick gains the delivery pass.

### API

| Method | Path | Description |
|---|---|---|
| `PUT` | `/api/briefings/schedule` | Gains optional `email_enabled` field (default false, rejected with 422 when `EMAIL_PROVIDER=none`). |
| `GET` | `/unsubscribe/briefings?token=…` | Signed one-click unsubscribe; sets `email_enabled=false`, renders a plain confirmation page. Unauthenticated by design. |

---

## Implementation Phases

### Phase 1 — Delivery records + transport

- [x] Schema: `email_enabled` column + `briefing_deliveries` table
      (SQLite block, `postgres_schema.py`, Alembic `0004` — `0003` was
      taken by the digest-table retirement).
- [x] `BriefingDeliveryRepository` (ensure_pending / due-scan / claim /
      settle) for both backends.
- [x] `EmailSender` ABC + `SmtpEmailSender`; config knobs; provider factory.
- [x] Unit tests: send-once under racing ensure_pending, backoff/parking,
      claim contention (plus a dual-backend contract suite).

### Phase 2 — Rendering + scheduler integration

- [x] `BriefingEmailRenderer` (HTML + text, absolute links, unsubscribe
      footer, List-Unsubscribe headers).
- [x] Scheduler tick: ensure_pending on slot fire + delivery pass.
- [x] Integration test: scheduled slot → briefing generated → exactly one
      email captured by a fake sender; lazy-then-scheduled → still exactly
      one.

### Phase 3 — Settings + unsubscribe

- [x] `email_enabled` in PUT /schedule + settings UI checkbox (hidden when
      provider is `none`; capability flag on `GET /api/auth/status`).
- [x] Signed unsubscribe route + confirmation page (GET for humans, POST
      for RFC 8058 one-click).
- [x] E2E: enable email → slot fires → email contains working script link
      and unsubscribe; unsubscribe link flips the flag.

### Phase 4 — Hosted hardening (interlocks with #43)

- [x] `SesEmailSender` (lazy boto3, `send_raw_email` so List-Unsubscribe
      headers survive). SES identity/DKIM setup notes in the #43 runbook
      still pending.
- [ ] Bounce/complaint SNS webhook → auto-disable `email_enabled` (spam-
      trap protection).

---

## Open Questions

1. **Weekly empty-window email.** Daily silence is fine; a weekly user
   whose Monday email never comes can't tell "quiet week" from "broken".
   Option: a minimal "nothing new this week" note for weekly cadence only.
   Punted for v1; decide after real usage.
2. **Audio in the email (#34).** Attach, link to hosted audio, or embed a
   player link in the personal feed? Deferred until #34 ships; the
   renderer leaves a slot for it.
3. **Digest of the digest.** Should the email body be the full script or a
   teaser + link? v1 ships the full script (the reader is the deliverable);
   revisit if size/clipping (Gmail's 102KB clip) bites on catch-up
   briefings.

---

## References

- [#50 scheduled-briefings](50-scheduled-briefings.md) — the trigger this
  spec delivers on; declared delivery channels out of scope.
- [#36 per-user-digest-from-inbox](36-per-user-digest-from-inbox.md) —
  briefing semantics (throttle, empty-window honesty) that shape the
  send-once and no-filler rules.
- [#43 aws-hosting](43-aws-hosting.md) — SES fits the hosted deployment;
  SMTP keeps self-host dependency-free.
- [#42 robustness](42-robustness-and-failure-mode-hardening.md) — FM-1
  (per-delivery isolation), FM-4 (no silent send-drops; parked failures
  are visible).

---

## Decision Log

| Date | Decision | Rationale |
|---|---|---|
| 2026-07-07 | Delivery records decoupled from briefing rows | "Generated" ≠ "delivered"; lazy-then-scheduled must email exactly once; email failure must not lose or re-generate the briefing |
| 2026-07-07 | `UNIQUE (briefing_id, channel)` as the send-once anchor | Constraint-level idempotency beats application-level checks under racing triggers |
| 2026-07-07 | Delivery pass inside the #50 scheduler tick, not the task queue | Fleet is user-sized; the queue's episode/podcast invariant (#48) doesn't fit briefing-keyed work; escape hatch to a worker mirrors #50's |
| 2026-07-07 | Empty window sends nothing (v1) | Matches #36's honest empty state; weekly-cadence exception left as an open question |
| 2026-07-07 | Recipient resolved at send time from `users` | Address changes between generation and retry go to the current address |
| 2026-07-07 | SMTP default, SES optional, `none` global off-switch | Self-host stays dependency-free; hosted aligns with #43; zero overhead when unconfigured |

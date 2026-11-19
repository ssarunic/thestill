# Welcome and Product-Tips Email

> **Status:** 🚧 Implemented on `feat/lifecycle-email-prereqs` (2026-11-19); pending PR + merge. Ships with the switch off
> **Created:** 2026-11-19
> **Updated:** 2026-11-19
> **Priority:** Medium. Activation (spec #111) stalls at the first follow,
> and a reader who leaves on signup day never hears from thestill again.
> **Related:** [#51 briefing-email-delivery](51-briefing-email-delivery.md)
> (sender, delivery state machine, unsubscribe tokens),
> [#95 product-analytics](95-product-analytics.md) (why this is not built
> on PostHog), [#104 abuse-prevention](104-abuse-prevention-and-user-admin.md)
> (runtime switches, account status), [#111 product-health-metrics](111-product-health-metrics.md)
> (activation, experiment 3 in [docs/experiments.md](../docs/experiments.md))

---

## Overview

A short email sequence for new accounts, sent by thestill itself:

| Key | Earliest | Expires | Sent only if |
|---|---|---|---|
| `welcome` | 24 h after signup | day 3 | always; the copy differs for readers with and without a follow |
| `follow_nudge` | day 3 | day 7 | the reader follows no show |
| `claude_connector` | day 7 | day 12 | no active Claude connector (OAuth grant or MCP token) |
| `search_and_people` | day 14 | day 21 | the reader follows at least one show |

Readers control it with a new Settings switch, **Product tips by
email**, separate from the usage-events switch.

## Decisions

- **Built in, not on a vendor.** PostHog Workflows and Customer.io were
  considered. Both need the reader's email address in the vendor. That
  breaks the privacy policy's promise that PostHog receives "not your name
  or email" and spec #95 §5. It would also tie the sequence to analytics
  consent: PostHog only sees readers who have usage events on. A future
  customer-facing Slack or in-app channel would have to be built here
  anyway, and two systems contacting one reader would duplicate caps,
  opt-outs and ban handling. The ledger has a `channel` column for that.
- **State, not events.** No timer starts at signup. A periodic tick
  (every 15 min, on the spec #104 periodic runner) reads the current
  facts and queues what is due. Deploys, restarts and analytics opt-outs
  cannot lose a message, and a message is never stale: its condition is
  checked when it is queued and again just before it is sent.
- **Send once.** `user_messages` has `UNIQUE (user_id, message_key,
  channel)`. The delivery pass copies spec #51: leased claim, attempts
  spent at claim time, bounded exponential backoff, and a terminal
  `skipped` status for a message that no longer applied at send time.
- **Separate consent.** `users.product_tips_email_enabled`, off by
  default. The sign-in page tells new readers about tips only when they
  can be sent (an email provider is configured), and the Google callback
  turns the switch on for a new account in exactly that case (PECR soft
  opt-in: the chance to refuse where the address is collected). Accounts
  created before the column existed, or while no provider was configured,
  stay off, so configuring email later never writes to readers who were
  never told. Each email carries a
  one-click unsubscribe with its own token purpose, so a tips link can
  never turn off briefing emails, and the other way round.
- **Frequency.** At most one lifecycle email per user per pass, none
  within 2 days of the last one, none within 12 hours of a briefing email,
  and only 09:00–19:00 in the briefing schedule's time zone (UTC without
  one). Expired messages are passed over, so a catch-up after an outage
  sends only what is still current. A send that happens later than
  planned (a retry, a backlog) checks the hours and the briefing gap
  again and waits for the next allowed time without spending a retry; a
  message that expired while waiting is skipped. The planner pages
  through candidates by keyset, so a large signup cohort can't starve
  newer readers.
- **Ships off.** The `lifecycle_email_enabled` runtime switch (env default
  `LIFECYCLE_EMAIL_ENABLED=false`). Turn it on after experiment 2 is read,
  so two activation changes don't overlap.

## Implementation

| Part | Where |
|---|---|
| Migration (column + ledger) | `migrations/versions/0029_lifecycle_email.py`, `postgres_schema.py`, `sqlite_podcast_repository.py` |
| Ledger + candidate facts | `repositories/user_message_repository.py` (+ SQLite, Postgres) |
| Sequence and copy | `services/lifecycle/messages.py` |
| Renderer | `services/lifecycle/renderer.py`, shared markup in `services/email_markup.py` |
| Planner + delivery | `services/lifecycle/service.py`, wired in `web/app.py` |
| Unsubscribe | `/unsubscribe/tips` in `web/routes/unsubscribe.py`; `TIPS_PURPOSE` in `utils/unsubscribe_token.py` |
| Settings | `PATCH /api/auth/me` `product_tips_email_enabled`; `product_tips_email_available` in `/api/auth/status`; `ProductTipsSettings.tsx`; sign-in page line |
| Operator CLI | `thestill lifecycle status \| plan \| preview \| send-test` |

Facts come from service state only (`podcast_followers`,
`user_episode_inbox`, `oauth_grants`, `mcp_tokens`,
`user_briefing_schedules`, `briefing_deliveries`), never from
measurement-only tables, so readers with usage events off are judged on
the same facts as everyone else.

## Rollout

1. Merge and deploy (alembic 0029 runs on boot).
2. `thestill lifecycle preview <key> --out /tmp/preview` for each message,
   and `thestill lifecycle send-test welcome --to <you>` to see it in a
   real inbox.
3. After experiment 2 is read: `thestill settings set
   lifecycle_email_enabled true`, add the PostHog annotation, and fill in
   experiment 3's "Shipped".
4. `thestill lifecycle status` shows sends per message; `thestill
   lifecycle plan` shows who would get what next, and why.

## Out of scope

- Slack and in-app channels (the ledger is ready for them).
- A holdout group. At current volume a holdout would leave too few
  readers in either arm; the read is before vs after, per
  docs/experiments.md.
- Editing copy without a deploy.

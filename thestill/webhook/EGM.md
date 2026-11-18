# webhook

## Overview

An in-process, thread-safe tracker for pending transcription webhook
callbacks. It has three consumers, all through `get_tracker`:

- `thestill/core/elevenlabs_transcriber.py:351-355` registers each submitted
  episode with `add_pending` when the transcriber runs in webhook mode
  (`wait_for_webhook`).
- The web route `thestill/web/routes/webhooks.py:357-359` calls
  `mark_completed` when the ElevenLabs callback arrives.
- `thestill/cli.py:2361-2390` (`transcribe`) reads `pending_count`,
  `is_all_done`, `completed_count` and `wait_for_all(timeout=1.0)` to block
  until every callback has arrived or the user presses Ctrl+C.

Episodes are keyed by `episode_id`, because
ElevenLabs returns a different request id in its callback than the one in the
submission response (`tracker.py:WebhookTracker` docstring).

## Interface

- `__init__.py` - re-exports `WebhookTracker`, `get_tracker`, `reset_tracker`.
- `tracker.py:WebhookTracker` - pending/completed tracker:
  - `add_pending(episode_id)`
  - `mark_completed(episode_id, success=True)`
  - `pending_count`, `completed_count` and `is_all_done` properties
  - `wait_for_all(timeout=None) -> bool`
  - `reset()`
- `tracker.py:get_tracker()` - creates the process-global singleton on first
  call, then returns it.
- `tracker.py:reset_tracker()` - clears the singleton's state (the instance is
  kept).

## Invariants

- All state changes and reads hold `WebhookTracker._lock`. The singleton is
  created under `_tracker_lock` (`tracker.py:get_tracker`).
- `add_pending` clears the all-done event. The event is set only when the
  last pending episode is marked completed (`tracker.py:WebhookTracker.mark_completed`).
- A callback for an episode that is not pending is ignored and logged at
  debug level. It never changes the counts (`tracker.py:WebhookTracker.mark_completed`).
- `is_all_done` is true only when nothing is pending and at least one episode
  has completed (`tracker.py:WebhookTracker.is_all_done`).
- `wait_for_all` returns `True` at once when nothing is pending. Otherwise it
  returns the result of `Event.wait(timeout)`, which is `False` on timeout
  (`tracker.py:WebhookTracker.wait_for_all`).
- Registration and completion meet only when the webhook route runs in the
  CLI's own process, as the thread that `thestill/web/background_server.py:BackgroundWebhookServer`
  starts.
- Known issue: when `transcribe` reuses an already running `thestill server`
  (`ExistingServerInfo`, `thestill/cli.py:1948-1965`), the callback marks
  completion in that server's tracker, not the CLI's. The CLI's pending count
  never drops, and its wait loop (`thestill/cli.py:2372-2378`) runs until
  Ctrl+C.

## Side Effects

- Process-global mutable singleton `tracker.py:_tracker`. Its state is not
  persisted or shared across processes.
- Structured log events from `add_pending` and `mark_completed`.
- `wait_for_all` blocks the calling thread.

## Verification

- Run: `make test`

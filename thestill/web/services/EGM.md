# thestill.web.services

## Overview

Two web-only helpers that keep logic out of the route handlers.
`HealthService` runs the probes behind `GET /health/ready`.
`WebhookTranscriptProcessor` turns an ElevenLabs speech-to-text webhook
payload into a stored raw transcript and updates the episode's database
row. The general business services live in `thestill/services/`, not here.

## Interface

- `__init__.py:__all__` exports `HealthService` and `WebhookTranscriptProcessor`.
- `health_service.py:HealthService(config)` has no HTTP dependencies. It provides:
  - `ping_database()`, which calls `repositories.factory.ping(config)` and raises on failure;
  - `check_worker(task_worker)`, which raises `RuntimeError` when
    `task_worker.is_degraded()` is true.
- `webhook_transcript_processor.py:WebhookTranscriptProcessor(path_manager, repository)`.
  - `process_elevenlabs_webhook(payload) -> Optional[Transcript]`: the entry point.
  - Private helpers `_format_webhook_response`, `_parse_words`, `_build_segments` and
    `_slugify` do the same job as the code in `ElevenLabsTranscriber`.
  - `_save_transcript` and `_cleanup_pending_operation` handle the disk writes.

## Invariants

- `health_service.py:HealthService.check_worker` does nothing when `task_worker` is `None`
  or has no callable `is_degraded`. Failure is signalled only by raising.
  `thestill/web/routes/health.py:readiness_check` turns a raise into a 503 and a
  normal return into a 200.
- `webhook_transcript_processor.py:WebhookTranscriptProcessor.process_elevenlabs_webhook`
  returns `None`, with no side effects, when any of these holds:
  - `webhook_metadata.episode_id` is missing;
  - the transcript text is empty;
  - `repository.get_episode` finds no episode.
- `process_elevenlabs_webhook` reads `payload["transcription"]` when it is a non-empty
  dict. Otherwise it uses the top-level payload (the older format). `transcription_id`
  falls back to `request_id`.
- Missing slugs are built with `_slugify(title)`, which returns `"untitled"` for an empty
  result, so the podcast slug is never empty and the `podcast_slug or "unknown"`
  fallbacks (`webhook_transcript_processor.py:137`, `:141`) are dead code. The
  `raw_transcript_path` stored in the database is relative:
  `<podcast_slug>/<episode_slug>.json`.
- `_parse_words`:
  - skips `spacing` entries;
  - wraps `audio_event` text as `[text]`;
  - maps ElevenLabs speaker ids to `SPEAKER_01`, `SPEAKER_02`, ... in order of first
    appearance;
  - converts a negative logprob with `exp()`.
- `_build_segments` starts a new segment each time the speaker changes.
- `_format_webhook_response` sets `model_used="scribe_v1"`. It enables diarization only
  when a speaker is detected, and the language defaults to `"en"`.

## Side Effects

- `health_service.py:HealthService.ping_database` makes one database round-trip.
- `webhook_transcript_processor.py:WebhookTranscriptProcessor`:
  - `process_elevenlabs_webhook` reads the episode with `repository.get_episode`.
  - `_save_transcript` writes JSON with `indent=2` to
    `path_manager.raw_transcript_file_with_podcast(...)`, creating directories as needed.
  - `process_elevenlabs_webhook` then sets the episode's `raw_transcript_path` with
    `repository.update_episode(..., updates={"raw_transcript_path": ...})`.
  - `_cleanup_pending_operation` deletes
    `pending_operations_dir()/elevenlabs_<transcription_id>.json`.
  - Logging uses f-strings, not structured kwargs, and includes episode titles and output
    paths.

## Verification

- Run: `./venv/bin/pytest tests/unit/web tests/integration/web` (full suite: `make test`)

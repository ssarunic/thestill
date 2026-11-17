# Audio Duration Cap

> **Status:** 📝 Draft (2026-11-17)
> **Created:** 2026-11-17
> **Updated:** 2026-11-17
> **Priority:** High (#2 of the anti-abuse series 118–123). Nothing limits how long an episode is. One import of a 100-hour file is transcribed and run through every LLM stage, and the only ceiling is a 2 GiB download cap that a low-bitrate file never reaches.
> **Related:** [#104 abuse-prevention-and-user-admin](104-abuse-prevention-and-user-admin.md) (runtime settings, quotas, alerts, `QUOTA_ENFORCE` pattern), [#03 error-handling](03-error-handling.md), [#49 queue-auto-healing-and-recovery](49-queue-auto-healing-and-recovery.md) (healer, `error_class`), [#106 episode-reprocess](106-episode-reprocess.md), [#88 import-outcomes-and-arriving-soon](88-import-outcomes-and-arriving-soon.md), [#112 mcp-inbox-import](112-mcp-inbox-import.md) (fixed-wording import errors), [#116 timestamp-drift-alignment](116-timestamp-drift-alignment.md) (the Dalston URL path in production), [#63 universal-follower-gate](63-universal-follower-gate.md). Siblings: [118-per-feed-processing-budget.md](118-per-feed-processing-budget.md), [120-spend-guardrails.md](120-spend-guardrails.md), [121-signup-trust-and-account-farming.md](121-signup-trust-and-account-farming.md), [122-introduced-feed-visibility.md](122-introduced-feed-visibility.md), [123-abuse-triage-sweep.md](123-abuse-triage-sweep.md)

---

## Executive summary

Every cost of processing an episode grows with its length: transcription audio-minutes, GPU time on the Dalston node, and the LLM tokens of clean, summarize and entity extraction. Spec #104 limits how many episodes a user can start (5 imports a day, 10 new feeds a day). It does not limit how long each one is. Today the only bound is `max_audio_bytes` (2 GiB), which at 32 kbps is about 149 hours of audio, and which the production transcription path never applies at all.

This spec adds one runtime setting, `max_episode_minutes` (default 360, 0 = off), and enforces it in four layers, cheapest first:

1. **Import.** Refuse at once when the resolver already knows the duration (YouTube, Apple, Spotify). No quota is charged.
2. **Enqueue.** Don't start the pipeline for an episode whose feed-reported duration is over the cap.
3. **Probe before the paid stage.** Measure the real length before transcription: reuse the ffprobe reading the download stage already takes, and on the Dalston URL path probe the remote file's header. A feed's `itunes:duration` is attacker-controlled, so this is the layer that counts.
4. **Backstop before the first LLM stage.** Clean refuses a transcript longer than the cap, so the dollar spend stops even when the probe could not tell.

An over-long episode ends as a failed episode with a new failure type, `too_long`. The healer never re-drives it, users see "Too long to process" instead of "Unavailable", and an admin can release it with the existing Retry, which runs exempt. Operator and admin paths (reprocess, CLI, admin stage commands) are never capped.

## Current state

| Area | Today | Reference |
|---|---|---|
| Byte cap | `max_audio_bytes`, 2 GiB, env `MAX_AUDIO_BYTES` | [config.py:624](../thestill/utils/config.py#L624), [:1012](../thestill/utils/config.py#L1012) |
| Byte cap enforcement | Checked against `Content-Length` and while streaming, HTTP downloads only | [audio_downloader.py:202](../thestill/core/audio_downloader.py#L202), [:214](../thestill/core/audio_downloader.py#L214) |
| YouTube download | yt-dlp with no `max_filesize` or duration filter; the byte cap does not apply | [youtube_downloader.py:225](../thestill/core/youtube_downloader.py#L225) |
| Bare audio import | Accepts any http(s) URL ending in an audio extension, with no network call and no duration | [import_service.py:198](../thestill/services/import_service.py#L198), [:218](../thestill/services/import_service.py#L218) |
| YouTube import | Duration from yt-dlp metadata | [import_service.py:317](../thestill/services/import_service.py#L317) |
| Apple / Spotify import | Duration from iTunes `trackTimeMillis` / Spotify metadata | [import_service.py:744](../thestill/services/import_service.py#L744), [:820](../thestill/services/import_service.py#L820) |
| Import flow | Resolve, then `reserve_quota("imports_per_day")`, then create and enqueue | [import_service.py:920](../thestill/services/import_service.py#L920), [:935](../thestill/services/import_service.py#L935), [:966](../thestill/services/import_service.py#L966) |
| Feed duration | `itunes:duration` parsed into `episodes.duration` (seconds); often missing, never verified | [feed_manager.py:1283](../thestill/core/feed_manager.py#L1283), [duration.py:179](../thestill/utils/duration.py#L179), [podcast.py:218](../thestill/models/podcast.py#L218) |
| Single enqueue choke point | `enqueue_full_pipeline`, used by refresh, follow seed, publish fan-out, send and import | [queue_manager.py:1113](../thestill/core/queue_manager.py#L1113), [inbox_service.py:71](../thestill/services/inbox_service.py#L71) |
| Entry stage | With Dalston and a fetchable URL, an episode skips download and downsample and starts at transcribe | [queue_manager.py:139](../thestill/core/queue_manager.py#L139), [task_handlers.py:451](../thestill/core/task_handlers.py#L451) |
| Measured duration | Download runs ffprobe and overwrites `episodes.duration`; downsample does the same | [task_handlers.py:300](../thestill/core/task_handlers.py#L300), [:316](../thestill/core/task_handlers.py#L316), [duration.py:247](../thestill/utils/duration.py#L247) |
| Downsample | pydub decodes the whole file into memory | [audio_preprocessor.py:247](../thestill/core/audio_preprocessor.py#L247) |
| Dalston URL submit | The publisher URL goes to Dalston; Thestill never sees the bytes | [dalston_transcriber.py:275](../thestill/core/dalston_transcriber.py#L275) |
| Transcript length | Max segment `end` is already computed after transcription (sync tracks) | [task_handlers.py:636](../thestill/core/task_handlers.py#L636) |
| Fatal path | `FatalError` → task `dead` with `error_class='fatal'`, episode `failure_type='fatal'` | [task_worker.py:825](../thestill/core/task_worker.py#L825), [:1012](../thestill/core/task_worker.py#L1012) |
| Healer | Only requeues `error_class='infra'` rows | [queue_manager.py:2399](../thestill/core/queue_manager.py#L2399), [postgres_queue_manager.py:1518](../thestill/core/postgres_queue_manager.py#L1518) |
| Failure types | `transient`, `fatal`; column is free text with no CHECK | [podcast.py:48](../thestill/models/podcast.py#L48), [:266](../thestill/models/podcast.py#L266) |
| User view of a failure | Non-admins see "Unavailable" | [EpisodeCard.tsx:122](../thestill/web/frontend/src/components/EpisodeCard.tsx#L122) |
| Admin retry | Admin-only, re-queues the failed stage with `initiated_by="episode-retry"` | [api_episodes.py:465](../thestill/web/routes/api_episodes.py#L465), [:518](../thestill/web/routes/api_episodes.py#L518) |
| On-behalf tasks | `refresh`, `refresh-feed`, `import`, `inbox-*`; everything else is an operator run | [task_worker.py:982](../thestill/core/task_worker.py#L982) |
| Spend breaker | Sums feed-reported durations; a missing one counts 0 | [alerts.py:70](../thestill/services/abuse/alerts.py#L70), [:109](../thestill/services/abuse/alerts.py#L109), [queue_manager.py:1790](../thestill/core/queue_manager.py#L1790) |

There is no local database in this checkout (`data/` is absent), so the duration distribution of real shows was not measured here. Rollout Phase 0 measures it on production before any limit is enforced.

## Threat

**Imports.** A signed-in user hosts a single MP3 at 32 kbps mono, 149 hours long, just under 2 GiB, on any web server. They import it five times a day under five URLs (the canonical id is a hash of the normalised URL, so a query string change is a new episode). `BareAudioResolver` makes no network call, so the import succeeds instantly and charges one `imports_per_day` each. In production the episode starts at transcribe, and Dalston fetches the URL itself, so the 2 GiB cap is never checked.

Rough cost per day, per account, using illustrative figures (spec #120 will give real ones):

- Audio: 5 × 149 h ≈ 745 audio hours.
- LLM: speech runs about 9,000 words, or 12,000 tokens, per hour. Clean reads and rewrites the transcript; summarize and entity extraction read it again. That is about 40k input and 15k output tokens per audio hour, or roughly $0.35 per hour at $3 / $15 per million tokens. About $260 a day.
- GPU: 745 hours of audio on the single Dalston node, which transcribes for every user. Every followed show's new episodes wait behind it.

A 16 kbps file doubles all of this. Spec #121's account farming multiplies it by the number of accounts.

**YouTube imports.** A YouTube video of up to 12 hours (YouTube's own upload limit) goes through yt-dlp with no size cap, then downsample decodes it into memory with pydub. Twelve hours of 48 kHz stereo is about 8 GB of PCM, on a 4 GiB host ([66-aws-single-ec2-hosting.md](66-aws-single-ec2-hosting.md)). That is an outage, not just a bill.

**Feeds.** A user introduces a feed they control (10 a day under spec #104). Following it seeds the newest 2 episodes and then every new one. Each episode is 100 hours, with `itunes:duration` either absent or set to `00:45:00`. Spec #118 caps how many episodes per feed; nothing caps how long each one is.

**With this spec** at the 6-hour default, the import case drops to 5 × 6 = 30 audio hours a day, about 25 times less, and the YouTube case never reaches downsample.

## Goals

1. No episode longer than `max_episode_minutes` reaches a paid stage (transcribe, clean, summarize, entities) on a user's behalf, whatever its metadata says.
2. When the length is known up front, the user is told immediately, and no quota is charged.
3. An over-long episode ends in one terminal, non-retryable state that the healer, bulk DLQ retry and refresh never re-drive.
4. An admin can release a legitimate long episode in one action, and exempt a whole show.
5. Real long-form podcasts are not refused by default.
6. The cap can be changed at runtime, and can run in log-only mode first.

## Non-goals

- **How many episodes a feed may process.** That is [118-per-feed-processing-budget.md](118-per-feed-processing-budget.md). This spec bounds each episode; #118 bounds the count. Together they bound the product.
- **Dollar budgets and the spend breaker.** That is [120-spend-guardrails.md](120-spend-guardrails.md). This spec supplies it a better duration (see Interactions).
- **Dalston's own limits.** Dalston's server-side fetch and job limits live outside this repository. Setting one there is a good second layer and is left to its operator.
- **Trimming.** A long episode is not cut to the first N hours. Half a transcript makes a misleading summary.
- **Single-user mode.** The cap is a no-op there, like the quotas in spec #104. The local operator runs everything.

## Design

### 1. The setting and the policy

New runtime setting in [settings_registry.py](../thestill/services/abuse/settings_registry.py):

| Key | Type | Env default | Meaning |
|---|---|---|---|
| `max_episode_minutes` | int ≥ 0 | `MAX_EPISODE_MINUTES=360` | Cap for untrusted feeds and all imports. `0` turns the cap off |
| `max_episode_minutes_trusted` | int ≥ 0 | `MAX_EPISODE_MINUTES_TRUSTED=720` | Cap for feeds whose `FeedTrust.tier()` is `admin` or `chart` (spec #122). `0` = no cap for them |
| `duration_cap_exempt_podcasts` | list of podcast ids | empty | Shows that are never capped (a known 8-hour show) |

Plus env `DURATION_CAP_ENFORCE` (default `false` in Phase 1, then `true`), mirroring `QUOTA_ENFORCE`. With it off, every layer computes its verdict and logs `episode_too_long_would_refuse`, and nothing is refused.

All three keys are set through the existing `thestill settings set` and `/admin/settings`, and audited as `setting.set`.

One small policy object, `DurationCap` in `thestill/services/abuse/duration_cap.py`, built in `build_abuse_services` next to the quota service:

```python
class DurationCap:
    def limit_seconds(self, podcast_id: str | None, *, is_import: bool) -> int | None: ...
    def exempt(self, *, initiated_by: str, metadata: dict, podcast_id: str | None) -> bool: ...
    def check(self, seconds: int | None, *, limit: int | None, source: DurationSource) -> Verdict: ...
```

**Which limit applies.** Imports always use `max_episode_minutes`: the importer chose the URL, so the parent show's trust says nothing about it. Feed episodes use the trusted limit when `FeedTrust.tier(podcast_id)` is `admin` or `chart`, and the base limit for `followed` and `none`. A `followed` feed can be farmed by the accounts spec #121 is about, so it gets no extra room. Until spec #122 ships, every feed uses the base limit.

**Who is exempt.**

- Any task whose `initiated_by` is not on-behalf in the sense of [task_worker.py:982](../thestill/core/task_worker.py#L982). That covers admin Retry (`episode-retry`), reprocess (`reprocess`, [reprocess_metadata.py:27](../thestill/core/reprocess_metadata.py#L27), spec #106), the admin stage commands (`api`), and CLI runs. These paths are all admin-only or operator-only already.
- An import by an admin. `ImportService` passes `duration_cap_exempt: true` into the first task's metadata, so the worker layers skip it too. `enqueue_full_pipeline` gains an optional `extra_metadata` argument for this.
- Episodes of a podcast in `duration_cap_exempt_podcasts`.

**Tolerance.** A measured or transcript length is compared exactly. A probe estimate (§3.2) is refused only when it exceeds the limit by more than 10%, to absorb dynamic ad insertion (spec #116) and estimate error. The later layers still catch a real overrun.

### 2. Durations: where they come from

Spec #120 needs to know which duration it is reading. Add two columns to `episodes` (one Alembic revision, the next free number after 0028, plus `postgres_schema.SCHEMA_SQL` and the SQLite `_run_migrations` step, per spec #104's three-part pattern):

| Column | Type | Notes |
|---|---|---|
| `probed_duration_seconds` | int, null | Set by the remote probe (§3.2) or the import resolver |
| `duration_source` | text, null | `feed`, `resolver`, `probe`, `measured` or `transcript`: where `duration` last came from |

`duration` keeps its meaning (the best known length, shown in the UI). Download and downsample already overwrite it with the ffprobe reading through `mark_episode_downloaded` / `mark_episode_downsampled`; they now also set `duration_source = 'measured'`. No second probe is added after download. Spec #120's precedence (probed > measured > feed > default) reads these columns.

### 3. Enforcement layers

#### 3.1 Import (layer a)

In `ImportService.import_url`, after `_resolve` and before `reserve_quota` ([import_service.py:935](../thestill/services/import_service.py#L935)):

- If `canonical.duration_seconds` is known, the user is not an admin, and it is over the limit, raise `EpisodeTooLongError`. No row is written and no quota is reserved, so there is nothing to release.
- `EpisodeTooLongError` subclasses the module's `ImportError`, so the route ([api_imports.py:59](../thestill/web/routes/api_imports.py#L59)) returns 400 and the MCP `import_episode` tool relays it. Following spec #112, the message is fixed wording plus numbers only: "This episode is 31 h 12 min long. Thestill processes episodes up to 6 hours."
- The refusal writes a `quota_exceeded` usage event with `subject_id='max_episode_minutes'`. Spec #104's `quota_pressure` alert and the daily digest then see a user who keeps trying, with no new alert code.
- A re-import of an existing episode that already failed as `too_long` is free and returns that state (spec #88's existing dedup path), so the import modal shows the reason.

`BareAudioResolver` stays network-free. A bare-audio import has no duration here and is caught by §3.3.

#### 3.2 Enqueue (layer b)

`enqueue_full_pipeline` ([queue_manager.py:1113](../thestill/core/queue_manager.py#L1113)) already reads the episode's pending tasks. It also reads `episodes.duration` and `podcast_id` in the same query. When the duration is over the limit and the call is not exempt, it adds no task, marks the episode failed (§4) at the stage it would have entered, logs `episode_too_long` with `layer='enqueue'`, and returns `False`.

This one change covers refresh (`enqueue_discovered_episodes`), the follow seed, publish fan-out and send (`enqueue_pipeline_for`) and import, because they all go through it. Operator paths call `add_task` directly and are not affected.

A missing feed duration passes this layer. Absence is not evidence, and §3.3 measures it.

#### 3.3 Probe before transcription (layer c)

This is the authoritative layer, because a feed's `itunes:duration` and an attacker's file need not agree. It runs before the first stage that costs money, on each path:

**Download path** (Whisper and other local providers, YouTube everywhere, and any episode without a fetchable URL):

- `handle_download` ([task_handlers.py:270](../thestill/core/task_handlers.py#L270)) first checks the stored duration (it may have changed since enqueue).
- After the file is on disk it already has `duration_seconds` from ffprobe ([:300](../thestill/core/task_handlers.py#L300)). The check runs on that value **before** the upload, the sync track and `mark_episode_downloaded`, so nothing is stored for a refused file. The measured duration is still written, with `duration_source='measured'`, so admins see the real length.
- Refusing here also keeps a 12-hour file away from pydub's in-memory decode in downsample.
- If ffprobe returns `None`, the episode continues and `duration_unmeasured` is logged. Downsample fails such files anyway, and §3.5 is the backstop.

**YouTube** additionally passes yt-dlp a `match_filter` on `duration` (limit + 10%), so an over-long video is refused from its metadata before any bytes download. The filtered result raises `EpisodeTooLongError`, not the generic "YouTube download failed".

**Dalston URL path** (production for RSS and bare-audio episodes). Thestill never holds the file, so `handle_transcribe` probes it before submitting the job ([dalston_transcriber.py:275](../thestill/core/dalston_transcriber.py#L275)):

1. One `guarded_redirect_fetch` GET (the SSRF guard the downloader uses) with `Range: bytes=0-262143`, a 10-second timeout, reading at most 256 KiB.
2. Total size comes from `Content-Range`, or `Content-Length` on a 200.
3. MP3: a Xing/Info or VBRI header gives the frame count and an exact duration. Otherwise the first frame's bitrate, read with the MPEG header parser that spec #116 already has ([sync_track.py:85](../thestill/core/sync_track.py#L85)), gives `size × 8 / bitrate`.
4. Other formats: ffprobe reads the prefix from a temp file. That works for WAV headers and for M4A with the `moov` atom at the front.
5. The result goes to `probed_duration_seconds` with `duration_source='probe'`. When the feed gave no duration it also fills `duration`.
6. If the probe can't tell (an unknown format, `moov` at the end, a server that ignores `Range` and has no length), the job is submitted and `duration_unverified` is logged with the host. §3.5 still stops the LLM spend. The fraction of unverified episodes is a Phase 1 metric (open question 2).

The probe adds one small request per episode on the URL path. It is never a different download of the audio Dalston transcribes, so spec #116's rule about not mixing ad sets is untouched.

#### 3.4 Byte cap scaled to the limit (layer d)

`AudioDownloader` gets `max_bytes = min(max_audio_bytes, limit_seconds × 320 kbps / 8 × 1.1)`, about 950 MB at 6 hours. It stops a 2 GiB high-bitrate download early. It does nothing against a low-bitrate file (6 hours at 16 kbps is 43 MB), so it is a cheap backstop, not a check anyone relies on. Exempt tasks keep the plain `max_audio_bytes`.

#### 3.5 Backstop before the first LLM stage

`handle_clean` ([task_handlers.py:682](../thestill/core/task_handlers.py#L682)) already reads the raw transcript. Before any LLM call it takes the maximum segment `end` (the same value [task_handlers.py:636](../thestill/core/task_handlers.py#L636) computes) and checks it against the limit, exactly, with `duration_source='transcript'`.

This is the one check every path passes: any provider, any probe outcome. It does not save the transcription, but it saves clean, summarize and the entity branch, which is where the per-token money goes. A refusal here logs `episode_too_long` with `layer='transcript'` and `probe_missed=true` when an earlier layer had passed the episode. That pair tells the operator a probe or a feed lied.

### 4. The terminal state

An over-long episode becomes a **failed episode** with a new `FailureType.TOO_LONG = "too_long"`.

Why not a separate "held" state: `EpisodeState` is derived from the episode's columns, and a new state would touch every reader. The failed state already has what "held" needs: the admin Failed page, the admin-only Retry, the `failed_at_stage` to resume from, and a user-facing badge. So "held" is failed with this type, and "release" is Retry.

- **Exception.** `EpisodeTooLongError(FatalError)` carries `error_class='fatal'`, `failure_type='too_long'`, the seconds, the limit and the source. `_mark_episode_failed` ([task_worker.py:1012](../thestill/core/task_worker.py#L1012)) uses `getattr(exc, "failure_type", "fatal")` instead of the constant `"fatal"`.
- **Queue row.** The task goes `dead` with `error_class='fatal'`, so the spec #49 healer, which only takes `infra`, never requeues it. Layer b marks the episode without a task row, so there is nothing for the healer to find at all.
- **Breaker.** A fatal verdict means the dependency answered, so the stage breaker is resolved as for any `FatalError` ([task_worker.py:825](../thestill/core/task_worker.py#L825)).
- **Bulk DLQ retry** ([api_commands.py:1818](../thestill/web/routes/api_commands.py#L1818)) keeps the task's original `initiated_by`, so a retried `too_long` row fails again at the first check, cheaply. Releasing is deliberate, never a side effect of "retry all".
- **Release.** The episode Retry ([api_episodes.py:465](../thestill/web/routes/api_episodes.py#L465)) queues the failed stage with `initiated_by="episode-retry"`, which is exempt. For a layer-b episode the stored `failed_at_stage` is the entry stage, so Retry starts the pipeline. For a §3.5 refusal Retry starts at clean, and the transcript is reused. The release is written to the audit log as `episode.release_too_long` with the duration. In the UI, Retry is labelled "Process anyway (31 h 12 min)" for this failure type, echoing spec #106's cost warning.
- **Exempting a show.** `thestill settings set duration_cap_exempt_podcasts '[...]'`, or the admin settings page. Existing `too_long` episodes of that show still need a Retry each; nothing is released in bulk.
- **Refresh and follow** never touch a failed episode: `enqueue_full_pipeline` is only reached for discovered, unqueued ones.
- **Readers of `failure_type`.** Both repositories log "Unknown failure_type" and drop the value for anything outside the enum. Ship the enum value and the UI before any code writes `too_long` (Phase 1 does both while only logging).

### 5. What users see

- **Import refused up front:** the 400 message from §3.1, in the import modal and in the MCP tool error.
- **Episode card and reader:** non-admins see a "Too long" badge instead of "Unavailable", with the text "Longer than 6 hours. Thestill doesn't process episodes this long." The limit shown is the one that applied. Admins see the badge, the measured length and source, and "Process anyway".
- **Import outcome (spec #88):** `ImportResult` gains `failure_type`, so the import modal can show the same reason when a bare-audio import fails later at §3.3.
- **Inbox:** the entry stays, like any failed episode.

### 6. Logging and alerts

Every verdict logs `episode_too_long` (or `episode_too_long_would_refuse`) with `episode_id`, `podcast_id`, `user_id` for imports, `seconds`, `limit_seconds`, `source` and `layer`. No URLs of private files beyond the host.

The spec #104 daily digest gains one line: episodes refused as too long in 24 hours, by layer, with the top feeds and users. Spec #123's triage sweep can read the same events. No new instant alert: repeated import refusals already feed `quota_pressure` (§3.1), and a feed that keeps publishing over-long episodes is #118's and #123's signal.

### 7. Interactions

- **#118 per-feed budget.** A refused episode wasn't processed, so it does not use the feed's minute budget. #118 counts minutes from the same `duration` / `duration_source` columns.
- **#120 spend guardrails.** The breaker currently counts a missing duration as 0 ([alerts.py:70](../thestill/services/abuse/alerts.py#L70)), so a bare-audio import is invisible to it. The probe and resolver values in §2 give it a real number, and #120's precedence uses them.
- **#122 feed trust.** Only `FeedTrust.tier()` is read. This spec stores nothing about trust.
- **#106 reprocess** is exempt. Its dialog already shows the duration before a re-transcribe.
- **Operator CLI** (`thestill download`, `transcribe`, …) is exempt and logs `duration_cap_bypassed` with `initiated_by` when an exempt run processes an episode over the limit, so it is visible.

## Rollout

1. **Phase 0: measure.** On production, before choosing the final default:

   ```sql
   SELECT width_bucket(duration, 0, 43200, 12) AS bucket_1h, count(*)
   FROM episodes WHERE duration IS NOT NULL GROUP BY 1 ORDER BY 1;
   SELECT p.title, e.title, e.duration / 3600.0 AS hours
   FROM episodes e JOIN podcasts p ON p.id = e.podcast_id
   ORDER BY e.duration DESC NULLS LAST LIMIT 50;
   SELECT count(*) FILTER (WHERE duration IS NULL) * 1.0 / count(*) FROM episodes;
   ```

   Long-form interview and history shows run 3–5 hours, with occasional longer ones. The default of 6 hours (12 for charted shows) is chosen to leave them alone. Any followed show with episodes above it goes into `duration_cap_exempt_podcasts` before Phase 2.
2. **Phase 1: log only** (`DURATION_CAP_ENFORCE=false`). Migration, `DurationCap`, every layer, the `too_long` enum value and the UI badge, and the digest line. Nothing is refused. After a week, read `episode_too_long_would_refuse` by layer and source, and the `duration_unverified` rate.
3. **Phase 2: enforce.** Flip `DURATION_CAP_ENFORCE=true`. Layers a, b, c (download path), the YouTube filter and §3.5 refuse.
4. **Phase 3: the Dalston probe** refuses, once Phase 1 shows its estimates agree with the §3.5 transcript lengths (within 10% on episodes that passed both).

`docs/configuration.md` gains the three settings and `DURATION_CAP_ENFORCE`. `specs/README.md` gains this spec's row in the same PR.

## Testing

- **Policy:** base and trusted limits by `FeedTrust` tier; imports always base; `0` turns each off; exempt podcasts; `initiated_by` exemption for `episode-retry`, `reprocess`, `api` and an empty tag; admin import metadata; 10% slack applies to `probe` only; single-user mode is a no-op.
- **Import:** a YouTube, Apple and Spotify resolver result over the limit returns 400 with the fixed message, writes no episode, reserves nothing, and records one `quota_exceeded` event; the same for MCP; an admin's import goes through; a bare-audio import with no duration goes through.
- **Enqueue:** an over-long feed-reported episode gets no task and is marked `too_long` at its entry stage, through refresh, follow seed, publish fan-out and send; a missing duration enqueues.
- **Download:** a stub ffprobe reading over the limit refuses before upload, sync track and `mark_episode_downloaded`, and still records the measured duration; an `None` reading continues; the scaled byte cap aborts a large stream; the yt-dlp filter refuses from metadata without downloading.
- **Dalston probe:** CBR MP3, Xing VBR MP3, WAV and front-`moov` M4A fixtures give estimates within 2% of ffprobe on the full file; a server that ignores `Range` reads no more than 256 KiB; an unknown format logs `duration_unverified` and submits; the probe refuses an unsafe redirect.
- **Backstop:** a transcript whose last segment ends past the limit fails clean before any LLM call (stub provider asserts zero calls), with `probe_missed=true` when the feed said 45 minutes.
- **State:** the dead row has `error_class='fatal'` and the healer's query ignores it; bulk DLQ retry fails it again without calling the provider; episode Retry runs it to the end and audits the release; Retry after a §3.5 refusal reuses the transcript; the user API returns `failure_type='too_long'` and the frontend shows "Too long" to non-admins.
- **Log-only mode:** every layer logs `episode_too_long_would_refuse` and refuses nothing.

## Open questions

1. **Default.** 6 hours (12 for charted shows) is a guess until Phase 0 runs. 4 hours would cut abuse further and catch more real shows. The exempt list makes a tighter default cheap to live with; Phase 0 decides.
2. **Unverified URL-path episodes.** If Phase 1 shows a large share of `duration_unverified` (mostly M4A with `moov` at the end), the choice is to route those through the local download path (bandwidth and disk on the 4 GiB host) or accept GPU exposure up to the §3.5 backstop. A Dalston-side length limit would settle it more cheaply.
3. **Per-user cumulative minutes.** This caps one episode. A daily audio-minutes quota per user (`import_minutes_per_day`) would close the remaining gap between 5 × 6 hours and what a normal user imports. It fits spec #104's quota table and may belong in #118 or #120.
4. **Probe at import for bare audio.** Moving the §3.3 probe into the import request would tell the user at once, at the cost of a network call in the request path that `BareAudioResolver` deliberately avoids. Revisit if users import long files often.

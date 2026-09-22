# Thestill: System Features and Capabilities Report

**Prepared**: 2026-09-22
**Basis**: repository at commit `016365d` (main, 2026-09-21), the `docs/`
guides, the `specs/` index, and a survey of the Python and TypeScript source.
**Scope**: what the system does today, how it is put together, and what is
planned or in flight. This is a descriptive report, not a spec; where a
capability is behind a flag or only partly shipped, that is called out.

## 1. Executive summary

Thestill is an automated podcast transcription and summarisation pipeline
with a web application, a Model Context Protocol (MCP) server, and a
command-line interface on top. It turns audio episodes from RSS, Apple
Podcasts, Spotify and YouTube into diarised transcripts, cleaned readable
transcripts, structured summaries, a searchable entity-linked corpus, and
per-user morning briefings that can be narrated and emailed.

The system is built as a layered Python application (CLI → services → core
→ repositories → models) with a React single-page frontend. It runs
equally as a single-user local install on SQLite, a Raspberry Pi appliance
in Docker, or a multi-user hosted deployment on AWS with Postgres, S3, SES
and Google sign-in.

Headline capabilities:

- **Six-stage atomic pipeline** (refresh → download → downsample →
  transcribe → clean → summarize) plus an entity continuation
  (extract → resolve → reindex → co-occurrences → related → enrich), driven by a
  durable task queue with per-stage worker pools, retries, a dead-letter
  queue, circuit breakers, watchdogs and auto-healing.
- **Six transcription engines** (Whisper, WhisperX, Parakeet, Google
  Cloud Speech-to-Text, ElevenLabs Scribe, and self-hosted Dalston) and
  **five LLM providers** (OpenAI, Anthropic, Gemini, Mistral, Ollama),
  chosen by configuration.
- **Segment-preserving transcript cleaning** with per-podcast and
  per-episode "facts" (speaker maps, sponsors, mishearings), segment kinds
  (content, filler, ad break, music, intro, outro) and preserved word
  timings.
- **Structured summaries** with clickable timestamp citations that resolve
  to transcript segments.
- **Entity index and hybrid corpus search**: GLiNER extraction, ReFinED
  Wikidata disambiguation, Wikipedia/Wikidata enrichment, sqlite-vec or
  pgvector semantic search, lexical FTS, related-episode rails.
- **Per-user inbox, briefings, narration and email**: write-fan-out
  inbox, lazily or scheduled briefings, single-anchor narrated scripts with
  quote clips, SMTP or SES delivery with one-click unsubscribe.
- **Web app**: dashboard, inbox with reader overlay, podcast and episode
  pages, transcript playback sync and karaoke word highlighting, persistent
  audio and YouTube video player with Now Playing sheet, queue monitor,
  entity pages, search, top-podcast charts, URL import, settings.
- **MCP server**: twenty tools over stdio for Claude Desktop and
  ChatGPT Desktop, and the same surface over Streamable HTTP with per-user
  scoped tokens for claude.ai and Claude mobile connectors.
- **Operations**: opt-in Google OAuth multi-user mode with admin gating,
  default-deny API auth, rate limits, SSRF and XXE guards, structured
  logging for CloudWatch, GCP and Elastic, health and readiness probes,
  Docker images (slim, full, prod), Alembic migrations, and a CI pipeline
  with Postgres, Playwright, gitleaks and image publishing.
- **Quality tooling**: LLM-as-judge eval runs with pinned judges and
  append-only manifests, latency budget tests, and a ~95k-line codebase
  with 250 Python test modules and 77 frontend test files.

## 2. What the system is for

The README states the problem plainly: someone who follows twenty
podcasts faces thirty or more hours of audio a week. Thestill downloads
the episodes, transcribes them with speaker identification, cleans the
speech-to-text output, summarises each episode, and delivers a morning
briefing of what dropped. Beyond reading, the library becomes a queryable
knowledge base: through the MCP server a user can ask Claude to find every
mention of a topic across their podcasts or draft posts from an episode.

Two deployment shapes are first-class:

| Shape | Database | Files | Auth | Typical host |
|---|---|---|---|---|
| Single-user local or appliance | SQLite | local disk | none (auto local user) | laptop, Raspberry Pi 5 in Docker |
| Multi-user hosted | Postgres + pgvector | S3 | Google OAuth, JWT cookie, admin flag | one AWS EC2 instance with Caddy (spec #66) |

## 3. Architecture at a glance

The constitution (spec #00) fixes the dependency direction:
`CLI → Services → Core → Repositories → Models`, with `Utils` (Config,
PathManager, Logger) available everywhere. Key rules:

- Pipeline stages are atomic and idempotent; state lives on the `Episode`
  record and its `EpisodeState` enum (`discovered → downloaded →
  downsampled → transcribed → cleaned → summarized`, or `failed` with the
  failing stage recorded).
- `PathManager` owns every artifact path; the `FileStorage` abstraction
  routes artifacts to local disk or S3.
- Repositories own the database. A factory selects SQLite or Postgres from
  `DATABASE_URL`; every repository has both implementations.
- Pydantic models guard every external boundary (feeds, LLM responses,
  HTTP requests, MCP arguments, webhooks).
- Errors are classified as transient or fatal; the queue retries the
  former and dead-letters the latter.

Package layout:

```text
thestill/
├── cli.py            # Click CLI (37 top-level commands, 8 groups, 18 subcommands)
├── core/             # Atomic processors, providers, queue engine
├── services/         # Business logic (briefings, inbox, imports, auth, …)
├── repositories/     # SQLite + Postgres persistence, one pair per aggregate
├── models/           # Pydantic models
├── search/           # sqlite-vec / pgvector clients, related-episode builders
├── mcp/              # MCP server, tools, scopes, identity, middleware
├── web/              # FastAPI app, routes, middleware, React frontend
└── utils/            # Config, PathManager, logging
```

## 4. Content sources and ingestion

### 4.1 Following podcasts

`thestill add <url>` and `POST /api/commands/add` accept:

- RSS feed URLs (parsed with feedparser through defusedxml to block XXE).
- Apple Podcasts show links, resolved via the iTunes lookup API to the
  show's RSS feed, with deep-history import past Apple's 200-episode
  lookup cap (spec #65).
- Spotify show links, resolved by metadata matching against the Apple
  directory to the show's public RSS feed (spec #79). Spotify exclusives
  without an RSS feed are refused with a clear error.
- YouTube channels and playlists, via yt-dlp with the `MediaSource`
  strategy pattern (`RSSMediaSource`, `YouTubeMediaSource`,
  `MediaSourceFactory`).

The Add Podcast modal also offers search over regional top-500 charts
(spec #27), with EEA-wide region expansion drafted (spec #57).

### 4.2 Importing single episodes

Any signed-in user can paste a URL into the inbox Import dialog or call
`POST /api/imports` (spec #31). Supported kinds: YouTube videos (watch,
short, `youtu.be`), Apple Podcasts episode share links, Spotify episode
links and URIs, and direct audio file URLs (`.mp3`, `.m4a`, `.opus`,
`.ogg`, `.wav`). The episode lands in the inbox immediately and the row
updates in place as each stage completes. Imports are deduplicated by a
canonical id and do not require following the parent show; a synthetic
`audio-imports` parent absorbs bare audio URLs. A Substack resolver
(spec #37) and a mobile "Share → Thestill" share target (spec #80) are
drafted.

### 4.3 Feed refresh

- `thestill refresh` and the web refresh command discover new episodes,
  with `--podcast-id`, `--max-episodes` and `--dry-run` options.
- Parallel feed fetching with per-host concurrency caps and conditional
  GET (spec #19), and phase-timing profiling documented in
  `docs/profiling-refresh.md`.
- A queued `REFRESH_FEED` stage with per-feed adaptive (AIMD) intervals
  and a background scheduler (spec #48), both off by default.
- Refresh-on-open enqueues one throttled refresh when a podcast page is
  viewed (spec #74), so lazily imported shows stay fresh.
- The "processed = followed" gate (spec #63) means only followed podcasts
  are polled and processed.
- Network-failure classification (spec #60) distinguishes host-side
  outages from feed-side failures, quarantines dead feeds and re-probes
  them on an interval.
- `MAX_EPISODES_PER_PODCAST` bounds tracked episodes without ever dropping
  processed ones.

## 5. The processing pipeline

### 5.1 Stages

| Stage | Command | What it does |
|---|---|---|
| Download | `thestill download` | Fetches audio with retry and exponential backoff; `MAX_AUDIO_BYTES` cap; SSRF URL guard with allowlist |
| Downsample | `thestill downsample` | Converts to 16 kHz mono 16-bit WAV for the transcribers; optional deletion of originals |
| Transcribe | `thestill transcribe` | Produces JSON transcripts with timestamps, speakers and (where supported) word timings; chunked transcription for long audio |
| Clean | `thestill clean-transcript` | Two-pass facts-based, segment-preserving LLM cleanup (see 5.4) |
| Summarize | `thestill summarize` | Structured analysis with timestamp citations (see 5.5) |
| Entity continuation | queue stages `EXTRACT_ENTITIES`, `RESOLVE_ENTITIES`, `REINDEX`, `REBUILD_COOCCURRENCES`, `COMPUTE_RELATED`, `ENRICH_ENTITIES` | Entity index, search index, co-occurrence graph, related-episode rail, Wikidata enrichment (see 6) |

All stages support `--dry-run` and `--max-episodes`; most support
`--podcast-id`. Stages are idempotent and resume from existing artifacts.

### 5.2 Transcription engines

| Provider | Runs | Diarisation | Word timestamps | Notes |
|---|---|---|---|---|
| Whisper | local | no | segment-level only | free, private, hallucination filtering |
| WhisperX | local | yes (pyannote.audio, min/max speakers) | yes (forced alignment) | the one engine with live progress callbacks (spec #12) |
| Parakeet (NVIDIA NeMo TDT v3) | local | no | yes (native) | about 25 European languages, auto-detected |
| Google Cloud Speech-to-Text (Chirp 3 BatchRecognize) | cloud | built-in | yes | 12-minute chunks with 1-minute overlap transcribed in parallel, up to 8 hours; language from podcast metadata |
| ElevenLabs Scribe | cloud | built-in, up to 32 speakers | yes | files to 2 GB, language auto-detect, sync or async via HMAC-verified webhook |
| Dalston (self-hosted, ElevenLabs-compatible) | your hardware | yes | yes | audio never leaves your infrastructure; default in Docker images |

All engines are built behind one `Transcriber` base class and a factory
that validates the provider's optional dependencies and required settings
at startup. Long-running cloud jobs (Google, ElevenLabs, Dalston) persist
pending operations so they can be resumed or cancelled. Downloaded files
pass a magic-byte integrity check before reaching ffmpeg. Local engines are
an optional `local-transcription` extra so cloud-only deployments stay
small. Publisher transcripts advertised through Podcasting 2.0
`<podcast:transcript>` tags (SRT, VTT, JSON, HTML, text) can be downloaded
for comparison and evaluation.

### 5.3 LLM providers

`LLM_PROVIDER` selects one of OpenAI (with reasoning-effort control),
Anthropic, Google Gemini (with thinking level), Mistral, or Ollama for
local models. All share one `LLMProvider` base with Pydantic structured
output (falling back to JSON mode where a model lacks native support),
streaming, continuation of truncated generations, a per-model limits
table, prompt-cache awareness that drives batch sizing (Anthropic gets
explicit cache-control markers), and per-provider retry loops that honour
`retry-after` headers. Ollama is health-checked at construction. Untrusted
transcript text is wrapped with a prompt-injection preamble before it
reaches any prompt. There is no automatic cross-provider fallback and no
external tracing integration today: a per-batch pass-through for Gemini
`PROHIBITED_CONTENT` responses is in development (spec #41), and an
opt-in call-level trace sink is drafted (spec #75). The eval judge can be
pinned to a different provider and model than the pipeline.

### 5.4 Transcript cleaning

The cleaning stage (`TranscriptCleaningProcessor`) is deliberately
conservative: output stays at least 95% identical to input.

- **Pass 1, facts extraction**: speaker mapping (`SPEAKER_00` → name),
  guests, keywords and mishearings, sponsors. Stored as editable Markdown
  in `data/podcast_facts/` and `data/episode_facts/`; the `thestill facts`
  group lists, shows, edits and re-extracts them. Host and guest roles can
  also be set explicitly (`podcast set-hosts`, `podcast detect-hosts`,
  `episode set-guests`, `backfill-roles`). Metadata speaker priors from
  `<podcast:person>` tags are drafted (spec #67).
- **Pass 2, segmented cleaning** (spec #18): a deterministic segmenter
  builds an `AnnotatedTranscript` grid with stable ids and word timings;
  the LLM patches batches of segments with surrounding context, returning
  corrected text, a segment kind and optional sponsor. Kinds are
  `content`, `filler`, `ad_break`, `music`, `intro`, `outro`, so the UI
  can hide filler and toggle intros and ads without losing text.
- Degenerate transcripts fail loudly rather than being silently rewritten.
- A legacy inline cleaning path during transcription remains behind
  `ENABLE_TRANSCRIPT_CLEANING`.

### 5.5 Summaries

The summariser (`TranscriptSummarizer`) produces a fixed nine-section
Markdown artefact: The Gist, Timeline, Key Takeaways, The Drama, Best
Quotes, Blog Ideas, Social Snippets, Resource List, and a "BS Test"
critical check. Every claim must carry a timestamp; long transcripts are
chunked by estimated tokens and the system prompt is language-aware.
Timestamp citations such as `[49:30]` are resolved deterministically to
transcript segments and persisted in a content-addressed sidecar
(spec #54) so the reader can click into the transcript. A summary manifest
records provenance, and a `SummaryTranslator` produces faithful
translations for the original-language work drafted in spec #58.
Summaries are also the material for briefing narration.

### 5.6 Task queue and workers

The web server runs an in-process worker over a SQLite or Postgres task
queue (`SKIP LOCKED` on Postgres):

- Task states `pending`, `processing`, `completed`, `retry_scheduled`,
  `failed`, `dead` and `superseded` (a later stage made the row moot);
  a linear stage-successor graph chains the next stage for full-pipeline
  runs, and the six entity stages fail in isolation without failing the
  episode.
- Per-stage worker pools (`*_PARALLEL_JOBS`) so a slow transcribe does not
  block a fast clean (spec #20); per-episode mutex.
- Exponential backoff (about 5 s, 30 s, 3 min with jitter), three retries,
  then `failed` (transient) or `dead` (fatal) with a dead-letter queue
  offering retry, skip and retry-all. Interrupted and stale tasks are
  recovered on restart and by a periodic sweep.
- Auto-healing of infrastructure-class failures, per-stage circuit
  breakers, handler watchdogs, stale-row sweeps, and a degraded-mode
  self-restart when abandoned threads exceed a budget (spec #49).
- Real-time progress via Server-Sent Events per task, bump-to-front and
  cancel per task, and a queue viewer page in the web UI (specs #10, #11).
- Two background schedulers run inside the web server when enabled: the
  refresh scheduler (spec #48) enqueues due feeds each tick with a
  coalescing guard and weekly quarantine re-probes, and the briefing
  scheduler (spec #50) claims a schedule before generating so it is safe
  across restarts and multiple instances, then chains narration and the
  email delivery pass.
- A briefing readiness gate that waits for in-flight episodes before
  cutting a briefing is drafted (spec #55); a grace window already exists.

## 6. Entities, corpus and search (spec #28 and follow-ons)

- **Extraction**: GLiNER zero-shot NER over cleaned transcripts, with
  coreference handling and type rules for person, company, topic and
  product.
- **Resolution**: ReFinED disambiguation against Wikidata, alias merging
  with rapidfuzz, an alias allowlist, a resolution blacklist, a review
  queue with corrections, and admin CLI surgery (`entity merge`,
  `mention drop`, `mention repoint`, `entity-alias-add`, `clean-aliases`,
  `repair-entity-types`, `rebuild-entities`).
- **Enrichment** (specs #45, #47): Wikipedia and Wikidata lookups give
  photos, logos, bios and vital stats to resolved entities, run
  automatically as a pipeline stage with rate limiting and re-enrichment
  age.
- **Corpus**: a regenerable Markdown projection under `data/corpus/` with
  entity wikilinks and segment maps for persons, companies and topics;
  `rebuild-entity-pages` regenerates it.
- **Search**: transcript chunks embedded with a configurable
  sentence-transformers model (multilingual by default, English swap
  available), stored in sqlite-vec or pgvector, combined with lexical
  full-text search into `lexical`, `semantic` and `hybrid` modes. A quick
  search groups podcasts and episodes. Related-episode rails blend vector
  and TF-IDF signals and have been scaled from all-pairs to incremental
  constant-time updates (specs #46, #56).
- **Anchor queries**: `find-mentions`, `quotes-by`, `search`, `entity get`
  on the CLI, mirrored as MCP tools and web routes; a per-tool latency
  budget is enforced in CI.

## 7. Inbox, briefings, narration and email

- **Inbox** (spec #29): per-user deliveries with write-fan-out on publish,
  seeding of recent episodes on follow, backfill for existing followers,
  automatic pipeline enqueueing for delivered-but-unprocessed episodes, and
  `unread`, `read`, `saved`, `dismissed` states with view-driven read
  tracking. A reader overlay opens episodes above the inbox without a
  route change (spec #52), and the reader refreshes live as the pipeline
  completes (spec #68).
- **Briefings** (specs #36, #50): each briefing covers the user's inbox
  since the previous one. Generated lazily on inbox open or by a per-user
  schedule (daily or weekly, chosen hour and IANA timezone) when
  `BRIEFING_SCHEDULER_ENABLED` is on. The link-index script is written
  synchronously without an LLM; briefing history, an episode index grouped
  by show, cover mosaic and mark-listened are in the UI.
- **Narration** (specs #33, #77): a single-anchor news-style or
  conversational readout with deterministic quote selection, LLM theme
  clustering and script generation, a validation contract with one
  regeneration and a guaranteed fallback. Time is budgeted in seconds
  (short, medium, long presets) with a quote-share cap. Available from the
  briefing page and `thestill narrate`. Rendering to MP3 with TTS and
  spliced quote clips plus a private personal podcast feed is drafted
  (spec #34).
- **Email delivery** (spec #51): opt-in per user, SMTP or AWS SES behind
  an `EmailSender` abstraction, send-once semantics anchored on a unique
  constraint, bounded retries with doubling backoff tracked in a
  deliveries table, inline-styled HTML plus plaintext with absolute links
  back to the app, and RFC 8058 one-click unsubscribe with signed tokens
  that survive JWT secret rotation.

## 8. Web application

### 8.1 Backend (FastAPI)

- Routers for health, status, dashboard, podcasts, episodes, transcript
  words, entities, search, inbox, briefings, narrations, imports,
  commands (pipeline, queue, DLQ), top podcasts, auth, MCP tokens,
  ElevenLabs webhooks and unsubscribe. The API reference in
  `specs/02-api-reference.md` and `docs/web-server.md` list every route.
- Uniform response envelope with pagination; OpenAPI docs only when
  enabled.
- `GET /health` liveness and `GET /health/ready` readiness with one DB
  round-trip; readiness fails when the worker is degraded.
- Middleware stack: gzip, request logging with correlation ids and
  capability-URL redaction, security headers (Content Security Policy that
  allows only the YouTube player frame, `X-Frame-Options: DENY`, HSTS in
  production), body-size limits, CORS from an explicit origin list (a
  wildcard is refused at startup), and in-memory sliding-window rate
  limiting on auth, webhooks and MCP.
- Server-Sent Events for task progress; no websockets. Everything else
  in the UI is opt-in React Query polling.
- `POST /api/podcasts/resolve` lazily imports a show from a URL, so a
  chart row can be opened before anyone follows it.
- Serves the built SPA from `thestill/web/static/` with long-lived
  immutable caching for hashed assets and a no-cache catch-all.

### 8.2 Authentication and authorisation

- Single-user mode (default): no login; a local user owns the data.
- Multi-user mode: Google OAuth 2.0 only (no passwords or magic links),
  JWT in an httpOnly `SameSite=Strict` cookie (30-day default) or a
  Bearer header for API clients, token revocation on logout,
  `PUBLIC_BASE_URL` required so callbacks never derive from the Host
  header, forwarded headers honoured only from trusted proxies, and
  `COOKIE_SECURE` enforced in production.
- Default-deny: every `/api` router carries `require_auth`; operator
  surfaces (manual pipeline triggers, queue and DLQ, bulk retries, entity
  corrections, system status and dashboards, webhook payloads) require
  `require_admin`. An integration test audits the route table so an
  unauthenticated endpoint cannot ship unnoticed.
- Legacy account claim (spec #64) transfers or discards the single-user
  account's data when moving to multi-user.

### 8.3 Frontend (React 19, TypeScript, Vite, Tailwind 4, React Query, React Router 7)

Pages: Dashboard, Inbox, Podcasts, Podcast detail, Episodes, Episode
detail, Briefings, Briefing detail, Entities, Search results, Top
podcasts, Queue viewer, Failed tasks, Settings, Login.

Notable features:

- Dashboard stats, activity feed and narration tile; admin-only routes
  and buttons mirror the server gates.
- Episode reader with summary, transcript, entity list, and a
  hierarchy tuned against Apple Podcasts on a phone (spec #76).
- Transcript playback sync (spec #23): active-segment highlight,
  follow-playback toggle, click-to-seek, deep-linked timestamps, filler
  reveal, in-transcript search with next and previous.
- Karaoke word highlighting during playback with graceful fallback
  (spec #38), also in the Now Playing sheet.
- Persistent floating media player across routes (spec #22), a unified
  audio and video playback session with a stable global video node
  (spec #61), YouTube video renditions from `<podcast:alternateEnclosure>`
  (spec #62), a floating video tile, a player shell layer above overlays
  (spec #71), and a tap-to-expand Now Playing sheet with scrubber and
  speed control (spec #72).
- Inbox with import modal, bulk actions, "currently processing" badges
  on episode cards (spec #21), and mobile row density work (spec #73).
- Command bar quick search, corpus search results, entity pages with
  enrichment, related-episode rails, top-podcast charts with follow
  buttons.
- Briefing schedule settings, email opt-in, MCP connector token
  management (create, rotate, revoke with masked prefix, scopes, expiry
  and last-used).
- Media Session API metadata so lock screens and headset buttons work,
  persisted playback rate, Web Share API share button, deep links with
  `?t=` seek, scroll restoration and filters kept in the URL.
- Mobile header and navigation drawer with a three-breakpoint layout,
  error boundaries with stale-chunk reload recovery after deploys, all
  pages lazy-loaded, and a test suite of 77 component and page test files
  plus seven Playwright end-to-end suites in CI.
- Not yet present: dark mode (design tokens exist but no dark theme), a
  PWA manifest or service worker, and the mobile share target (spec #80).

## 9. MCP server

`thestill-mcp` speaks MCP over stdio for Claude Desktop, ChatGPT Desktop
and any MCP client, exposing five `thestill://` resources (podcast,
episode, transcript, audio reference, summary) plus twenty tools:

| Group | Tools |
|---|---|
| Library | `add_podcast`, `remove_podcast`, `list_podcasts`, `list_episodes`, `get_status`, `get_transcript`, `get_summary` |
| Pipeline | `refresh_feeds`, `download_episodes`, `downsample_audio`, `transcribe_episodes`, `clean_transcripts`, `summarize_episodes`, `process_episode` |
| Entities and search | `find_mentions`, `list_quotes_by`, `get_episode_clip`, `get_entity`, `list_episodes_by_entity`, `search_corpus` |

Remote access (spec #78) mounts the same surface on the web server at
`/mcp/{token}` over Streamable HTTP for claude.ai custom connectors and
Claude mobile. Each user mints a personal token from Settings with
scopes `read` (implicit), `follows` and, for admins, `pipeline`; the
server stores only a hash, tokens expire (90 days by default) and can be
rotated or revoked, per-token and per-IP rate limits apply, and the token
is redacted from all logs. Unknown or expired tokens get an empty 404 so
the endpoint's existence leaks nothing; the admin flag is re-read on every
request so demotion takes effect immediately. Tool arguments are validated
with JSON Schema, tool failures return sanitised error results with a
reference id for log correlation, and a unit test asserts every registered
tool has a scope entry. An OAuth variant for organisation-wide connectors
is the planned Phase 3.

## 10. Command-line interface

The CLI (`thestill/cli.py`) has 37 top-level commands and 8 groups with
18 subcommands, all built on a typed `CLIContext` dependency container
with global `--config` and `--quiet` options. Beyond the podcast-management
and pipeline commands already listed:

- `narrate`, `resolve-summary-citations`, `backfill-inbox`,
  `claim-local-user`, `activity` (recent processing log), `cleanup`
  (age-based audio deletion that keeps transcripts), `repair-images`
  (re-sync stale artwork URLs).
- `transcribe` and `summarize` also run standalone on a single audio or
  transcript file; `transcribe --cancel-pending` reconciles async
  ElevenLabs jobs; `refresh --queue` enqueues per-feed worker tasks.
- `facts list|show|edit|extract`; `podcast set-hosts|set-recurring|
  detect-hosts`; `episode set-guests`; `backfill-roles`.
- Entity index: `resolve-entities`, `rebuild-cooccurrences`,
  `backfill-entity-types`, `enrich-entities`, `merge-aliases`,
  `clean-aliases`, `repair-entity-types`, `rebuild-entities`,
  `entity get|merge`, `mention drop|repoint`, `resolution-blacklist`,
  `entity-alias-add`, `rebuild-entity-pages`.
- Search: `find-mentions`, `quotes-by`, `search`, `chunks backfill`,
  `reindex`, `related build`.
- Evals: `eval run|list|show|compare`, `evaluate-raw-transcript`,
  `evaluate-clean-transcript`, `harness-eval`.
- `server` (host, port, reload, workers) and `status`.

Every command gets a `command_id` in structured logs. Most commands
share the vocabulary `--dry-run`, `--podcast-id`, `--episode-id`,
`--max-episodes`, `--force`, `--since` and `--json`; the destructive
`clean-aliases` and `resolve-summary-citations` default to preview and
need `--apply` or `--write`. The MCP server is a separate console script,
`thestill-mcp`, with no flags.

## 11. Storage, data model and persistence

- **Databases**: SQLite by default; Postgres (uuid, timestamptz, jsonb,
  pgvector with HNSW indexes, `pg_trgm` title search) when `DATABASE_URL`
  is set (spec #44). The Postgres schema has 30 tables and is the single
  source of truth shared by the bootstrap path and ten Alembic revisions,
  which can run at startup under an advisory lock. Every repository has
  both implementations behind one factory, verified by dual-backend
  contract tests that CI runs against a real pgvector container.
- **Aggregates**: podcasts, episodes, users, podcast followers, inbox
  entries, briefings, briefing schedules, briefing deliveries, MCP
  tokens, pending transcription operations, legacy claims, entities,
  mentions, aliases, co-occurrences, chunks (with vector and FTS
  indexes), category cache.
- **File artifacts**: original audio, downsampled audio, raw JSON
  transcripts, clean transcripts, summaries, facts, corpus pages,
  briefings, narrations, evaluations. Stored via `FileStorage` on local
  disk or S3 (SSE-S3 or SSE-KMS, S3-compatible endpoints supported,
  spec #35), with ephemeral-versus-persistent routing settled in
  spec #40.
- **Identifiers**: internal UUIDs plus external feed GUIDs and slugs;
  path traversal guards in `PathManager` and `LocalFileStorage`.

## 12. Deployment and operations

- **Docker**: multi-stage `:slim` (cloud transcription only), `:full`
  (adds ffmpeg for Google and ElevenLabs) and `:prod` (Postgres, S3, SES,
  search, entities extras) targets; digest-pinned base images; compose
  file for Raspberry Pi 5; launchd scripts for macOS.
- **AWS** (spec #66): one `t4g` EC2 instance running the prod image,
  Postgres and Caddy (automatic TLS) via `deploy/aws-ec2/`. A single-file
  boto3 tool provisions S3, IAM, security groups and Route53, stores
  secrets in SSM Parameter Store, launches and reconciles the instance,
  and offers SSM-based logs and shell; nightly backups dump Postgres and
  sync artifacts to S3. The Deploy workflow authenticates with OIDC,
  requires the commit to be on main, waits for the published image,
  reconciles the instance and publishes a GitHub release. Entity
  extraction runs on the resized instance as of the latest commit.
- **Configuration**: everything is environment-driven (`.env`), grouped
  by storage, transcription, LLM, episode management, concurrency, refresh
  scheduler, queue healing, cleaning, evals, web, auth, rate limits,
  briefings, email, search, enrichment, logging and MCP. Startup guards
  refuse unsafe combinations (insecure cookies in production, multi-user
  without a public base URL, misconfigured email provider, wildcard CORS).
  Several capabilities ship dark and must be switched on per deployment:
  queued refresh, the refresh scheduler, the briefing scheduler,
  narration and startup migrations. Auto-heal, circuit breakers,
  degraded-exit and the remote MCP mount default to on.
- **Logging**: structlog with console, JSON, ECS, GCP and CloudWatch
  formats, correlation ids (`request_id`, `command_id`, `task_id`,
  `worker_id`, `episode_id`), and query cookbooks for CloudWatch, GCP and
  Elastic.
- **Security** (spec #25, all 27 findings closed): XXE, SSRF, webhook
  HMAC and metadata checks, JWT hardening, CORS and trusted proxies,
  body-size caps, rate limits, race-condition fixes, hash-pinned `uv.lock`,
  committed `package-lock.json`, weekly Dependabot, gitleaks in
  pre-commit and CI, and a 48-hour fast-patch policy for critical CVEs
  such as in yt-dlp. A pre-deploy security checklist is planned
  (spec #26).

## 13. Quality, testing and evaluation

- **Tests**: 250 Python test modules across unit, integration, docs, perf
  and e2e directories; 77 frontend test files with Vitest and Playwright.
  A doc-drift suite mechanically cross-checks the docs against the code
  for API routes, log events, environment variables, CLI commands, MCP
  tool names and links. CI runs Python tests against a real pgvector Postgres service, a
  latency-budget job for search tools, frontend lint, tests, build and
  e2e, a gitleaks secret scan, a slim Docker build and an arm64 prod
  image publish.
- **Static checks**: black, isort, ruff (bans `print()` and tz-naive
  datetimes), pylint and mypy through `make check`.
- **Evals** (spec #53): `thestill eval run` judges raw transcripts,
  clean transcripts and summaries with an LLM judge pinned independently
  of the pipeline, deterministic checks for summaries, per-episode
  sampling for variance, append-only run manifests with prompt hashes and
  git commits (a unit test fails if a rubric prompt changes without a
  version bump), and `eval compare` that flags confounded comparisons
  where both judge and pipeline changed. Narration A/B scripts and
  cleaning comparison prototypes live in `scripts/`.
- **Performance**: a full-stack performance review shipped eight phases of
  hardening (spec #69) with a recorded medium-priority backlog (spec #70).

## 14. What is in flight or planned

From the specs index, the most significant open items:

- Briefing audio rendering with TTS and a private personal podcast feed
  (spec #34) and the briefing readiness gate (spec #55).
- Substack import (spec #37) and the mobile share target (spec #80).
- Episodes as first-class with many-to-many collections (spec #32).
- Original-language summaries (spec #58) and reader feedback loops for
  transcript and summary corrections (spec #59).
- Metadata speaker priors (spec #67), LLM call tracing (spec #75),
  Gemini prohibited-content fallback (spec #41).
- EEA top-podcast regions (spec #57), robustness hardening from the
  refresh outage post-mortem (spec #42), pre-deploy security checklist
  (spec #26).
- The specs index is behind the code in places. Rows still marked
  "implemented on a feature branch, pending merge" whose code is already
  present on main include Apple deep-history import (#65), performance
  hardening (#69), the player shell layer (#71), the Now Playing sheet
  (#72), refresh-on-open (#74), conversational narration (#77), remote MCP
  access (#78) and constant-time related episodes (#56). Treat those as
  shipped and update the index when convenient.

## 15. Known limitations

- Local transcription engines need the heavy `local-transcription`
  extra (torch); Docker images ship cloud providers only.
- Entity resolution needs several gigabytes of ReFinED Wikidata index and
  4 to 6 GB of RAM; the `entities` extra is required for the MCP server.
- Parakeet has no diarisation and no language selection; it transcribes
  whatever language it hears.
- Spotify-exclusive shows cannot be imported; Pocket Casts links are not
  supported.
- Narration is off by default while fallback rates are measured. It
  produces text and a TTS-ready JSON script only: there is no TTS
  provider, audio rendering or clip splicing yet (spec #34 is a draft).
- There is no cross-provider LLM fallback and no tracing integration; only
  WhisperX reports live transcription progress.
- Remote MCP connectors require a publicly reachable HTTPS endpoint and
  individually managed Claude accounts until OAuth lands.
- Rate limiting is in-memory per process.
- The web UI has no dark mode, PWA manifest or service worker yet.

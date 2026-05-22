# Thestill Specs

Technical specifications for thestill. Plans are gated and versioned inline;
references are evergreen.

This index is the canonical entry point. When adding, completing, or deprecating
a spec, update the relevant table below.

Each spec carries a stable numeric ID, which is also the filename prefix. IDs
are assigned on creation and never reused — moving a spec between tables
(e.g., Active → Completed) does **not** renumber it and does **not** rename
the file. When adding a new spec, assign the next available ID
(`max(current IDs) + 1`) and use it as the filename prefix.

## Reference

Evergreen documents describing how the system works today. Read these first
when getting oriented.

| ID | Spec | Summary |
|---:|---|---|
| 01 | [architecture](01-architecture.md) | Layered architecture, dependency flow, design patterns |
| 02 | [api-reference](02-api-reference.md) | REST API endpoints, response envelope, pagination format |
| 03 | [error-handling](03-error-handling.md) | Exception hierarchy, fail-fast patterns, error classification |
| 04 | [testing](04-testing.md) | Coverage targets, test types, type-checking standards |

## Active plans

Planned or in-progress work. Each spec carries its own `Status` header
describing current phase and gates.

| ID | Spec | Status | Summary |
|---:|---|---|---|
| 05 | [docker-deployment](05-docker-deployment.md) | 📋 Planned (2026-04-13) | Slim cloud-only Docker image for RPi5, multi-stage `:slim`/`:full` targets, Dalston-only default |
| 06 | [authentication](06-authentication.md) | 📝 Draft | Opt-in auth system: zero-friction self-hosted, multi-tenant-ready for hosted |
| 07 | [multi-user-web-app](07-multi-user-web-app.md) | 📝 Draft | Multi-user podcast tracking web app, "process once, deliver to many" |
| 08 | [multi-phase-transcript-cleaning](08-multi-phase-transcript-cleaning.md) | 🗄 Archived | Superseded by #18 |
| 09 | [single-user-web-ui](09-single-user-web-ui.md) | 🚧 Active development | Single-user web UI: dashboard, podcast management, pipeline visibility |
| 10 | [queue-viewer](10-queue-viewer.md) | 🚧 Active development | Task queue viewer page: pending/processing/retry tasks with bump-to-front |
| 11 | [task-queue-monitor](11-task-queue-monitor.md) | 🚧 Active development | Unified task queue monitor replacing the Failed Tasks page |
| 12 | [whisperx-chunk-progress-tracking](12-whisperx-chunk-progress-tracking.md) | 🚧 Active development | Capture WhisperX stdout progress output and convert to real-time callbacks |
| 18 | [segment-preserving-transcript-cleaning](18-segment-preserving-transcript-cleaning.md) | 🚧 Active development | Per-segment cleanup with word timestamps preserved; prerequisite for richer media player |
| 19 | [refresh-performance](19-refresh-performance.md) | 🚧 Active development | Profile `thestill refresh`, parallelize feed fetching, add conditional GET, design automated scheduler |
| 20 | [parallel-task-queues](20-parallel-task-queues.md) | 🚧 Active development | Per-stage worker pools so a slow transcribe task no longer blocks a fast clean task; stage-swimlane UI |
| 21 | [episode-processing-indicator](21-episode-processing-indicator.md) | 🚧 Active development | Show "currently processing" badge on episode cards in lists; frontend-only via queue-tasks hook |
| 22 | [floating-media-player](22-floating-media-player.md) | 🚧 Active development | Persistent audio playback across route changes via `PlayerContext` + mini player in `Layout`; seam for future custom player |
| 24 | [word-level-transcript-highlighting](24-word-level-transcript-highlighting.md) | 🗄 Archived | Superseded by #38 |
| 26 | [pre-deploy-security-checklist](26-pre-deploy-security-checklist.md) | 📋 Planned (2026-04-23) | LLM-runnable + runtime smoke-test checklist that must return GO before any deploy; regression harness for spec #25 |
| 28 | [corpus-search-and-entities](28-corpus-search-and-entities.md) | 🚧 Phases 0–4 complete; Phase 5 (entity pages + augmented reader) in progress | Native person/company/topic entity index over real podcast corpus; sqlite-vec hybrid search (Phase 2.10), DLQ-separated entity branch with CI latency budgets (Phase 3), and `⌘K` command bar + `/search` slug routes (Phase 4) all shipped. Phase 5: 7 of 20 reader affordances landed, entity-page polish + remaining affordances + Playwright suite still owed |
| 29 | [per-user-inbox-fanout](29-per-user-inbox-fanout.md) | 📝 Draft | Per-user Inbox replacing shared "recent episodes" list: write-fan-out on publish, seed-on-follow, per-user read/save/dismiss state; sets the steady-state delivery model for full automation |
| 30 | [mcp-anchors-and-entity-discovery](30-mcp-anchors-and-entity-discovery.md) | 📝 Draft | Seven thin MCP tools (anchor queries + entity discovery) so the LLM-harness surface catches up to the web entity surface introduced by spec #28 PRs #60/#61 |
| 31 | [import-arbitrary-episodes](31-import-arbitrary-episodes.md) | 📝 Draft | Paste any URL (YouTube / RSS episode / audio file) → episode lands in user's inbox immediately and runs through the existing pipeline; synthetic-podcast parents in v1, no follow side-effect |
| 32 | [episodes-as-first-class](32-episodes-as-first-class.md) | 📝 Draft | Lift `episodes.podcast_id NOT NULL` and add a many-to-many `collection_memberships` table; same content shared across cross-posts, user playlists, entity-pinned feeds; additive migration with a dual-write window |
| 33 | [narrated-digest](33-narrated-digest.md) | 📝 Draft | Single-anchor news-style readout replacing the concatenated digest; theme-grouped segments with verbatim quote clips, capped by user-chosen spoken duration; markdown + TTS-ready JSON script |
| 34 | [briefing-audio-and-feeds](34-briefing-audio-and-feeds.md) | 📝 Draft | Render #33's script to MP3 with TTS anchor + spliced original-audio quote clips; deliver via private token-protected personal podcast RSS feed (Apple / Overcast / Pocket Casts), in-app player, and direct download |
| 36 | [per-user-digest-from-inbox](36-per-user-digest-from-inbox.md) | 📝 Draft | Wire the morning briefing to select from each user's inbox since their last briefing; replaces the global recent-episode window post-#29; unblocks per-user audio in #34 |
| 37 | [substack-import-resolver](37-substack-import-resolver.md) | 📝 Draft | Add `SubstackResolver` so pasted Substack post URLs (open.substack.com, `*.substack.com`, custom domains) resolve to embedded podcast audio; reuses #31's canonical-id + auto-add-parent path |
| 38 | [karaoke-word-highlighting](38-karaoke-word-highlighting.md) | 📝 Draft | Karaoke-style smooth-wipe word highlighting during playback; CSS gradient + rAF driver, opt-in toggle, graceful fallback when words missing; supersedes #24 |
| 39 | [video-alternate-enclosure-player](39-video-alternate-enclosure-player.md) | 📝 Draft | Render `<podcast:alternateEnclosure>` video variants (YouTube embeds first, room for mp4/HLS later) on the Episode Detail page; audio pipeline unchanged |
| 41 | [llm-prohibited-content-fallback](41-llm-prohibited-content-fallback.md) | 🚧 Active development | Per-batch pass-through when Gemini returns `PROHIBITED_CONTENT`; structured-output path now surfaces real `finish_reason`. Option A (per-batch model fallback to Claude / Mistral) still owed |
| 42 | [robustness-and-failure-mode-hardening](42-robustness-and-failure-mode-hardening.md) | 📝 Draft (2026-05-21) | Post-mortem of the silent tz-naive/aware refresh outage → six named failure modes (errors-as-empty-results, checkpoint-before-durability, mixed-tz, silent fleet degradation, consistent-mock tests, parallel-path drift), enforcement plan, and PR review checklist. Phase 1 = gate ETag on success + narrow excepts + tz-aware Pydantic boundary |
| 43 | [aws-hosting](43-aws-hosting.md) | 📝 Draft (2026-05-22) | Phase-1 hosted beta on AWS (~1000 podcasts / ~4000 episodes/mo): app on a single EC2 `t4g.xlarge` + in-process worker, Dalston co-located on a `g4dn.xlarge` GPU (Spot-friendly), RDS Postgres + pgvector, S3 artifacts with a 14-day audio-cache lifecycle, S3 Gateway endpoint (no NAT). Gated on #44; HA later = Multi-AZ flip. ~$265–540/mo |
| 44 | [postgres-migration](44-postgres-migration.md) | 📝 Draft (2026-05-22) | SQLite→Postgres port done now (near-empty DB) so #43 runs managed from day one and HA is a checkbox. Audit: repo seam exists for 6/8 repos; missing all PG impls, driver, DSN/pool, factory, interfaces for entity + pending_ops, and direct ports of the queue (`FOR UPDATE SKIP LOCKED`) + vector search (sqlite-vec→pgvector). Additive, not a rewrite |
| 45 | [entity-page-enrichment](45-entity-page-enrichment.md) | 📝 Draft (2026-05-22) | Make person/company entity pages informative & fun. Three tiers: T0 = free structured facts/visuals from the Wikidata QID we already hold (photo/logo, vital stats, founder↔company cross-links) + Wikipedia lead + internal relationship graph; T1 = cached LLM narrative (why-they-matter, fun facts, founding story, quote synthesis); T2 = external/paid (financials, news↔spike correlation, predictions, audio supercut). Additive `entity_enrichment` table + one nullable API field; gated on QID (= notability); per-#42 failure model (transient ≠ "no data"). Sentiment explicitly cut |

## Completed

Shipped work. Kept for historical context and rollback reference.

| ID | Spec | Completed | Summary |
|---:|---|---|---|
| 35 | [pluggable-file-storage](35-pluggable-file-storage.md) | 2026-05-13 | `FileStorage` abstraction + `S3FileStorage` backend (PR #93); per-artifact migrations: digests/corpus/external transcripts (#93), `podcast_service` reads (#95), transcribers + audio pipeline (#96). Phase 4 (presigned URLs) deferred to spec #34; Phase 5 (Terraform/CDK) lives outside the code path |
| 40 | [storage-routing-ephemeral-vs-persistent](40-storage-routing-ephemeral-vs-persistent.md) | 2026-05-13 | Settles #35's per-artifact-routing question with two carve-outs: pending transcription ops move from JSON files to SQLite (PR #94); debug feeds keep direct `Path` I/O. Downsampled WAV stays in main backend |
| 13 | [multi-user-shared-podcasts](13-multi-user-shared-podcasts.md) | 2026-01-21 | Phase 1 (follow/unfollow) shipped: shared processing across users |
| 25 | [security-audit-and-hardening](25-security-audit-and-hardening.md) | 2026-04-27 | All 27 findings closed across phases 1–5 (XXE, SSRF, webhook auth, JWT, CORS, supply chain, race conditions, etc.) |
| 27 | [add-podcast-search-discoverability](27-add-podcast-search-discoverability.md) | 2026-04-27 | Search-or-paste Add Podcast modal filtering regional top-500 live; data path pre-shapes a future free-tier gate |
| 14 | [dry-refactoring-plan](14-dry-refactoring-plan.md) | 2026-01-13 | Eliminated ~210 lines of duplication across 6 refactoring phases |
| 15 | [mistral-llm-provider](15-mistral-llm-provider.md) | 2026-01-12 | Added Mistral AI as fifth LLM provider with full feature parity |
| 16 | [full-pipeline-and-failure-handling](16-full-pipeline-and-failure-handling.md) | 2026-01-07 | Full-pipeline execution + transient/fatal error split + DLQ |
| 17 | [pylint-fixes](17-pylint-fixes.md) | 2026-01-15 | Pylint score lifted to 9.19/10, zero E-level errors remaining |
| 23 | [transcript-playback-sync](23-transcript-playback-sync.md) | 2026-04-20 | Active-segment highlight, follow-playback toggle, click-to-seek, deep-linked timestamps, filler reveal, in-transcript search with collapse/next-prev |

## Status legend

| Marker | Meaning |
|---|---|
| 📖 Reference | Evergreen doc describing current system |
| 💡 Proposal | Problem framed, solution space being explored |
| 📝 Draft | Solution drafted, not yet scheduled |
| 📋 Planned | Scheduled, ready to execute, not started |
| 🚧 Active development | In progress, phases partially complete |
| ✅ Complete | Shipped and verified |
| 🗄 Archived | Superseded or abandoned (kept for history) |

## Conventions for new specs

- **ID**: assign the next available ID (highest current ID + 1). IDs are
  zero-padded to two digits (`01`, `02`, …) and remain stable for the life of
  the spec. Never reuse an ID, even for an archived/abandoned spec. Switch to
  three digits when crossing 99.
- **Filename**: `NN-kebab-case-descriptive.md`, where `NN` is the spec's ID.
  Example: `05-docker-deployment.md`. No date prefix. The ID prefix is part of
  the filename and is never changed once assigned — not even when status
  changes or the spec is archived.
- **Status header**: every spec starts with a table or bullet list containing
  `Status`, `Created`, `Updated`, and (for plans) `Priority`. See
  [05-docker-deployment.md](05-docker-deployment.md) for the reference format.
- **Linking**: use relative paths. File references inside a spec should use
  `../` to reach repo-root files — e.g., `[cli.py](../thestill/cli.py)`.
  Spec-to-spec references use the full prefixed filename — e.g.,
  `[06-authentication.md](06-authentication.md)`.
- **Index entry**: add a row to this file in the appropriate table as part of
  the same PR that creates the spec. Move the row between tables as status
  changes; the ID and filename both travel with the row unchanged.
- **Code citations**: reference file:line when pointing at specific code, for
  example `[cli.py:1458](../thestill/cli.py#L1458)`.
- **Referring to a spec**: use `spec #NN` in PRs, commits, and conversation —
  short and unambiguous, and easy to search for. The full filename is fine
  too, but the ID is the canonical short handle.

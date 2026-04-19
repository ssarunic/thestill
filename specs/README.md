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
| 20 | [floating-media-player](20-floating-media-player.md) | 📋 Planned (2026-04-19) | Persistent audio playback across route changes via `PlayerContext` + mini player in `Layout`; seam for future custom player |

## Completed

Shipped work. Kept for historical context and rollback reference.

| ID | Spec | Completed | Summary |
|---:|---|---|---|
| 13 | [multi-user-shared-podcasts](13-multi-user-shared-podcasts.md) | 2026-01-21 | Phase 1 (follow/unfollow) shipped: shared processing across users |
| 14 | [dry-refactoring-plan](14-dry-refactoring-plan.md) | 2026-01-13 | Eliminated ~210 lines of duplication across 6 refactoring phases |
| 15 | [mistral-llm-provider](15-mistral-llm-provider.md) | 2026-01-12 | Added Mistral AI as fifth LLM provider with full feature parity |
| 16 | [full-pipeline-and-failure-handling](16-full-pipeline-and-failure-handling.md) | 2026-01-07 | Full-pipeline execution + transient/fatal error split + DLQ |
| 17 | [pylint-fixes](17-pylint-fixes.md) | 2026-01-15 | Pylint score lifted to 9.19/10, zero E-level errors remaining |

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

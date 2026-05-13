# Storage Routing — Ephemeral vs Persistent Artefacts

> **Status:** ✅ Shipped in [#94](https://github.com/ssarunic/thestill/pull/94) (pending ops → SQLite) and [#96](https://github.com/ssarunic/thestill/pull/96) (downsampled WAV confirmed in main backend; corpus already routed via #93)
> **Created:** 2026-05-13
> **Updated:** 2026-05-13
> **Author:** Engineering
> **Related:** [#35 pluggable-file-storage](35-pluggable-file-storage.md), [#16 full-pipeline-and-failure-handling](16-full-pipeline-and-failure-handling.md)

---

## Provenance

Spec #35 left one open question unresolved: **per-artifact backend routing**. The
recommendation there was "start global, add overrides later when there's demand."
Demand arrived during the Phase 2 migration: routing every artifact family
through `STORAGE_BACKEND=s3` means routing ephemeral data (intermediate WAVs,
in-flight job state, debug-only RSS dumps) to S3 alongside the persistent
artefacts that actually need durability. That trades S3 PUT/GET fees + NAT
egress for no value.

This spec settles the routing question with **two narrow carve-outs**, not a
per-artifact override matrix:

1. **Pending transcription operations** move out of the file domain entirely
   and into SQLite, where they always belonged.
2. **Debug RSS feed dumps** keep doing direct `Path.write_bytes` — they bypass
   `FileStorage` because they're dev-only ephemera that overwrites itself on
   every refresh.

Everything else continues to use the global `STORAGE_BACKEND` (per #35).
Downsampled WAV stays on the main backend because Dalston and Google STT can
both stream audio from S3 directly via presigned URLs — making cloud storage
a feature for that artefact family, not a cost.

This spec also retires the "corpus stays local for Obsidian editing" footnote
from spec #35. Obsidian was an idea, not a requirement; corpus pages route
through `STORAGE_BACKEND` like every other persistent artefact.

---

## Table of contents

1. [Decision matrix](#decision-matrix)
2. [Pending operations → SQLite](#pending-operations--sqlite)
3. [Debug feeds → direct Path I/O](#debug-feeds--direct-path-io)
4. [Downsampled WAV — confirming "main backend"](#downsampled-wav--confirming-main-backend)
5. [Corpus simplification](#corpus-simplification)
6. [Migration phases](#migration-phases)
7. [Tests](#tests)
8. [Non-goals](#non-goals)

---

## Decision matrix

| Artefact family | Routing | Why |
|---|---|---|
| `original_audio/` | `STORAGE_BACKEND` (default) | Persistent, large (50–200 MB), durably needed for re-processing |
| `downsampled_audio/` | `STORAGE_BACKEND` (default) | Ephemeral by lifetime but cheaper to keep in S3 for STT providers that stream from S3 directly (Dalston, Google) — lifecycle rule expires it at 30d |
| `raw_transcripts/`, `clean_transcripts/`, `clean_transcripts/*.json` | `STORAGE_BACKEND` (default) | Persistent, small |
| `summaries/`, `briefings/`, `podcast_facts/`, `episode_facts/`, `narrations/` | `STORAGE_BACKEND` (default) | Persistent |
| `external_transcripts/` | `STORAGE_BACKEND` (default) | Persistent |
| `corpus/` | `STORAGE_BACKEND` (default) | Persistent (spec #28 corpus pages); Obsidian-local stance from #35 retired |
| `evaluations/` | `STORAGE_BACKEND` (default) | Persistent (test artefacts) |
| `briefings/` | `STORAGE_BACKEND` (default) | Persistent |
| `pending_operations/` | **SQLite (new table)** | DB-shaped: UUID PK, query by status, lifecycle measured in minutes |
| `debug_feeds/` | **Local `Path` I/O, bypass `FileStorage`** | Dev/debug-only XML, overwrites itself, no durability requirement |
| `feeds.json` | `STORAGE_BACKEND` (default) | Persistent feed metadata cache (already routed) |
| `chunks/` | N/A — sqlite-vec table, not a file family | |

Two carve-outs total. No per-artifact env vars. No `scratch_storage` field on `Config`.

---

## Pending operations → SQLite

### Current state

[`PathManager.pending_operation_file(operation_id)`](../thestill/utils/path_manager.py) returns
`data/pending_operations/{operation_id}.json`. Two transcribers persist state to
those files when starting long-running async jobs:

- [`thestill/core/elevenlabs_transcriber.py`](../thestill/core/elevenlabs_transcriber.py)
  — written on async-upload kickoff at line ~949; read on resume; deleted on
  completion at line ~956; listed via `pending_dir.glob("elevenlabs_*.json")`
  at lines ~980, 1040.
- [`thestill/core/google_transcriber.py`](../thestill/core/google_transcriber.py)
  — same pattern at lines ~1635–1697.

Lifecycle: written, read once on app restart to resume, deleted on completion.
Typical residence on disk is minutes to hours.

### Why move it

These files are **DB-shaped data wearing a JSON disguise**:

- Each has a UUID-like primary key.
- They're queried by status ("list pending") and by id ("get this operation").
- Their lifecycle is measured in minutes, not days — they're not artefacts to
  archive.
- Future scale-out (running Thestill on multi-host AWS deployments) needs them
  shared across hosts, which means either S3 (wrong for ephemeral state) or
  the DB (right for in-flight state).

Putting them in SQLite alongside the rest of the application state is the
correct shape from the start; storage backend routing then doesn't have to
think about them at all.

### Table schema

```sql
CREATE TABLE pending_transcription_operations (
    operation_id    TEXT PRIMARY KEY,
    provider        TEXT NOT NULL CHECK (provider IN ('google', 'elevenlabs')),
    episode_id      TEXT NOT NULL,
    payload_json    TEXT NOT NULL,
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX idx_pending_ops_provider ON pending_transcription_operations(provider);
CREATE INDEX idx_pending_ops_episode ON pending_transcription_operations(episode_id);
```

**Notes:**

- `operation_id` is the provider-supplied operation handle (Google's
  long-running operation ID; ElevenLabs' transcription job id). Both
  providers issue unique IDs, so no synthetic UUID needed.
- `episode_id` is `NOT NULL` because both transcribers already guard
  persistence on `episode_id` being present (ElevenLabs at line ~322,
  Google at line 1439). The skip-when-missing behaviour stays at the
  call site; the table enforces the invariant.
- `payload_json` carries the entire provider-shaped state — ElevenLabs'
  9 fields or Google's 18 `TranscriptionOperation` fields. Keeping it as
  one opaque blob avoids dragging provider-specific columns into a shared
  table and makes backfill a one-liner (store the JSON verbatim).
- No dedicated `audio_path` / `output_path` columns: they're per-provider
  shaped (`audio_path` for ElevenLabs, `audio_gcs_uri` for Google) and
  already carried inside `payload_json`. Promoting them to columns would
  force a normalisation pass that buys nothing — nobody queries by audio
  path.
- ISO-8601 timestamps with UTC suffix — matches the rest of the repo
  (per `feedback_sqlite_timestamp_format`).
- No FK constraint on `episode_id`: matches the existing JSON-file
  semantics where the file lifecycle is independent of the episodes
  table. A `DELETE FROM episodes` shouldn't cascade into in-flight
  transcription state.

### Repository surface

New module: `thestill/repositories/sqlite_pending_operations_repository.py`.

```python
class SqlitePendingOperationsRepository:
    def __init__(self, db_path: str): ...

    def create(
        self,
        operation_id: str,
        provider: str,           # "google" | "elevenlabs"
        episode_id: str,
        payload: dict,
    ) -> None: ...

    def get(self, operation_id: str) -> Optional[PendingOperation]: ...

    def list_by_provider(self, provider: str) -> list[PendingOperation]: ...

    def update_payload(self, operation_id: str, payload: dict) -> None: ...

    def delete(self, operation_id: str) -> None: ...   # idempotent
```

`PendingOperation` is a small `@dataclass` mirroring the table columns
(`payload_json` decoded into a `dict`).

### Migration shape

The existing migration block in [`thestill/repositories/sqlite_podcast_repository.py`](../thestill/repositories/sqlite_podcast_repository.py)
already handles incremental schema bumps. Add a new migration step that:

1. Creates the table + indexes.
2. Walks any existing `data/pending_operations/*.json` files and inserts them
   into the new table. For each file:
   - Parse the JSON.
   - Pick `provider` from the filename prefix (`elevenlabs_` → `elevenlabs`,
     otherwise `google`) or from the payload itself when it has a provider hint.
   - `operation_id` = filename stem (sans prefix and `.json` suffix).
   - `audio_path` = whatever the payload calls it (varies per provider; map
     in the migration).
   - `payload_json` = the full original JSON, preserved verbatim. Forward
     compatibility: no field deletion at backfill time.
3. Move the JSON file to `data/pending_operations/.migrated/` rather than
   deleting it — recoverable belt-and-braces against an in-flight job whose
   backfill mapping was wrong.

Backfill is intentionally idempotent: if the table already has the
`operation_id`, skip (don't overwrite).

After this migration runs successfully on a deployment, the
`data/pending_operations/` directory becomes a vestigial-but-harmless
backup. A future spec can remove the directory entirely once enough time has
passed.

### Transcriber call-site migration

Per-file changes:

**`thestill/core/elevenlabs_transcriber.py`** — replace four sites:

- `_save_pending_operation` (line ~935-953) → `repo.create(...)`
- `_load_pending_operation` (~line 944) → `repo.get(...)`
- `_clear_pending_operation` (~line 956) → `repo.delete(...)`
- `list_pending_operations` (~line 980 + 1040) → `repo.list_by_provider("elevenlabs")`

**`thestill/core/google_transcriber.py`** — replace four equivalent sites:
~lines 1635, 1653, 1673, 1690.

The transcribers take `pending_ops_repository: SqlitePendingOperationsRepository`
as a new constructor argument. Wire it up at the three startup seams
(CLI, web, MCP) the same way `file_storage` was threaded.

`PathManager.pending_operations_dir()` and `pending_operation_file()` stay
in place for the duration of the backfill window — once we're confident no
new code reads from disk, both methods are removed in a follow-up spec.

---

## Debug feeds → direct Path I/O

`debug_feeds/<podcast_slug>.xml` overwrites itself on every refresh. It exists
to debug RSS parsing issues; nothing reads it except a developer eyeballing
the file. Routing it through `FileStorage` would mean S3 round-trips for
something nobody else needs to see, and it'd interfere with the obvious
debug workflow of `ls data/debug_feeds/` + opening the file in a text editor.

**Decision:** debug-feed writes keep using direct `Path.write_bytes`. No
migration needed; the call site in [`thestill/core/feed_manager.py`](../thestill/core/feed_manager.py)
already does this and just doesn't need to change.

Documented here so a future "everything must go through FileStorage" cleanup
PR doesn't sweep this away by accident.

---

## Downsampled WAV — confirming "main backend"

Initial instinct was to treat downsampled audio as ephemeral and keep it
local — they're cheap to re-derive from the original audio. The reason to
**not** carve them out:

- **Dalston and Google STT can stream audio from S3.** When `STORAGE_BACKEND=s3`,
  the transcribe step can hand the STT provider a presigned URL instead of
  bytes-through-the-app. That's a real architectural win for cloud deployments
  and depends on the WAV being in S3 in the first place.
- **The S3 cost is bounded by lifecycle policy.** Spec #35 already prescribes
  expiring `downsampled_audio/` after 30 days. Storage cost is therefore a
  rolling 30-day window of files that are individually small (~10× smaller
  than the source MP3 because 16kHz mono).
- **Local backend behaviour is unchanged.** For `STORAGE_BACKEND=local`
  deployments (Docker/RPi5), downsampled WAVs continue to live on local disk.
  No regression.

**Decision:** downsampled WAV stays in the main `file_storage` (no carve-out).
Document the rationale here so the question doesn't re-litigate itself when
audio migration (#35 Phase 2.6) lands.

---

## Corpus simplification

Spec #35 carried a footnote that `corpus/` *might* want to stay local for
Obsidian browsability. That was speculation, not a requirement. Drop it.

**Decision:** corpus pages route through `STORAGE_BACKEND` like everything
else. The existing [`EntityPageWriter`](../thestill/core/entity_page_writer.py)
migration in #35 Phase 2.2 already uses `file_storage`; no code change needed.
Just delete the Obsidian footnote from #35 and `docs/storage-backends.md`
to stop anchoring the design on a stale idea.

---

## Migration phases

All phases shipped together in [#94](https://github.com/ssarunic/thestill/pull/94); doc updates in [#96](https://github.com/ssarunic/thestill/pull/96).

### Phase 1 — table + repository ✅ Shipped (#94)

- Migration block in `sqlite_podcast_repository.py` creates `pending_transcription_operations` + indexes.
- New `SqlitePendingOperationsRepository` module + tests.
- Repo constructed at the three DI seams (CLI, web, MCP) alongside the other repositories.

### Phase 2 — transcriber call-site migration ✅ Shipped (#94)

- Both ElevenLabs and Google migrated in the same PR (the shape was symmetric enough).
- Each transcriber's `_save_pending_operation` / `_load_pending_operation` / `_clear_pending_operation` / `list_pending_operations` now route through the repository instead of `path_manager.pending_operations_dir()`.

### Phase 3 — backfill ✅ Shipped (#94)

- One-shot migration that walks `data/pending_operations/*.json`, inserts into the table, moves the file to `.migrated/`.
- Runs from inside the table-creation migration block (so it's gated by the table-existence check and effectively idempotent).

### Phase 4 — docs ✅ Shipped (#96)

- Spec #35 Open Question #1 (per-artifact routing) marked resolved with a link to this spec; the Obsidian footnote in the Motivation section was reworded.
- Downsampled WAV stays in the main backend (this spec's Section 4) — confirmed by #96's `handle_downsample` migration.

---

## Tests

- **Repository tests** (`tests/unit/repositories/test_sqlite_pending_operations_repository.py`):
  CRUD round-trip, `list_by_provider` filtering, idempotent delete,
  `update_payload` overwrite semantics, payload JSON round-trip preserving
  nested structures.
- **Migration backfill test**: seed a `tmp_path/data/pending_operations/`
  with synthetic ElevenLabs + Google JSON files, run the migration on a
  fresh DB, assert the table rows are populated correctly and the source
  files moved to `.migrated/`.
- **Transcriber state tests**: existing tests for elevenlabs and google
  transcribers' pending-op persistence updated to use the repository
  instead of `tmp_path/pending_operations/*.json`. Behavioural assertions
  unchanged.
- **Integration**: a smoke test that starts a fake ElevenLabs job (mocked
  HTTP), kills the process before completion, restarts with a fresh
  process, and asserts the resume path picks up the row from the DB.

Aim: every existing transcriber resume scenario keeps working, plus the
backfill is regression-tested for both providers.

---

## Non-goals

- **Removing `PathManager.pending_operations_dir()` / `pending_operation_file()`
  in this spec.** They stay until we're confident the backfill has drained
  every deployment's `data/pending_operations/` directory. Follow-up spec
  removes them and the directory itself.
- **Reworking the rest of the spec #35 migration order.** Phase 2.4
  (transcribers) and Phase 2.6 (audio) still need to happen for the storage
  abstraction itself; this spec is orthogonal — it just removes pending ops
  from #35's scope.
- **Generalising the carve-out mechanism.** Two carve-outs justify themselves
  by their domain shape; adding a `STORAGE_BACKEND_AUDIO` / `STORAGE_BACKEND_CORPUS`
  matrix would over-design for no current need.
- **Multi-host pending-op coordination.** The DB move makes this *possible*,
  but cross-host locking semantics for "who owns this pending op" is a
  future-deployment concern, not in scope here.

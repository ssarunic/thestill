# Pluggable File Storage Backends

> **Status:** 📝 Draft
> **Created:** 2026-05-08
> **Updated:** 2026-05-08
> **Author:** Engineering
> **Related:** [#05 docker-deployment](05-docker-deployment.md), [#25 security-audit-and-hardening](25-security-audit-and-hardening.md)

---

## Provenance

This spec was extracted from a stale Claude-authored branch (`claude/abstract-file-storage-HGknL`, 4 commits, January 2026) before that branch was pruned. The branch never opened a PR and was 4 months out of date by the time it was reviewed. The design was solid; the diff was unmergeable. This document preserves the design so it can be re-implemented against current `main` when cloud deployment becomes a real requirement.

The original branch's commits (for archival reference, all by `Claude <noreply@anthropic.com>`):

- `d284043 feat: add FileStorage abstraction for pluggable storage backends`
- `b2038a9 feat: implement S3 and GCS storage backends`
- `d2547eb feat: add boto3 as optional dependency for S3 storage`
- `fc7805c refactor: cloud-first FileStorage API design`

---

## Motivation

The pipeline writes ~6 file artifact families per episode (original audio, downsampled WAV, raw transcript JSON, cleaned Markdown + JSON sidecar, summary, facts) plus corpus pages and digests. Today every byte lives on local disk under `data/`. Three forces push toward a storage abstraction:

1. **Docker / RPi5 deployment ([#05](05-docker-deployment.md)).** SD cards are slow and small; offloading audio + transcripts to S3 / GCS keeps the appliance lean. Spec #05 explicitly defers this and ships the slim image with local persistence — this spec is the natural follow-up.
2. **Multi-host hosting.** Any future managed offering needs shared, durable, ACL-protected storage that isn't tied to one container's filesystem.
3. **Pre-signed URLs for the web player.** Streaming audio to the browser today goes through FastAPI, which serves the file from local disk. With cloud storage, a presigned URL hands streaming directly to S3/GCS — cheaper, faster, and survives server restarts.

The point isn't to migrate everything off disk. The point is to make the storage layer *swappable* so each artifact family can live wherever makes sense (audio in S3, SQLite stays local, corpus pages stay local for Obsidian editing).

---

## Table of Contents

1. [Current state](#current-state)
2. [Proposed abstraction](#proposed-abstraction)
3. [Backends](#backends)
4. [Integration with PathManager](#integration-with-pathmanager)
5. [Configuration](#configuration)
6. [Migration phases](#migration-phases)
7. [Cross-cutting concerns](#cross-cutting-concerns)
8. [Open questions](#open-questions)
9. [Non-goals](#non-goals)

---

## Current state

**Path resolution** is centralized in [`PathManager`](../thestill/utils/path_manager.py) (660 lines, ~27 call sites across `core/`, `services/`, `web/`, `mcp/`). Spec #25 item 3.3 added `_assert_inside_root` plus slug-shape validation (`_SLUG_RE`, `_validate_episode_id`) so every external string is sanitised before it touches the filesystem. **Any storage abstraction must preserve these guards** — they are load-bearing security controls, not cosmetic checks.

**File I/O is scattered.** Direct `Path.read_*`, `Path.write_*`, and `open()` calls live in:

- [`core/audio_downloader.py`](../thestill/core/audio_downloader.py) — streams downloaded audio to disk with `open(local_path, "wb")`
- [`core/audio_preprocessor.py`](../thestill/core/audio_preprocessor.py) — pydub + ffmpeg need real filesystem paths
- [`core/dalston_transcriber.py`](../thestill/core/dalston_transcriber.py), [`core/elevenlabs_transcriber.py`](../thestill/core/elevenlabs_transcriber.py), [`core/google_transcriber.py`](../thestill/core/google_transcriber.py) — open audio for upload, write transcripts and pending-operation state
- [`core/entity_page_writer.py`](../thestill/core/entity_page_writer.py) — `path.write_bytes` for corpus Markdown pages
- [`core/external_transcript_downloader.py`](../thestill/core/external_transcript_downloader.py) — RSS-supplied transcript downloads
- [`services/digest_generator.py`](../thestill/services/digest_generator.py), [`services/podcast_service.py`](../thestill/services/podcast_service.py) — read transcripts, write digests

There is **no single chokepoint** today. Adding a backend means either threading a storage object through every call site or doing a sweep that replaces direct I/O with abstraction calls. The branch chose the second path; this spec keeps that choice.

**SQLite persistence (`data/podcasts.db`) is out of scope.** That's database state, not file state. Postgres migration is its own decision, deferred per [#05](05-docker-deployment.md).

---

## Proposed abstraction

Keep the abstraction minimal and *cloud-shaped* — operations the local FS gets for free are designed around what S3/GCS naturally support.

### `FileMetadata` dataclass

```python
@dataclass
class FileMetadata:
    path: str
    size: int
    modified_time: datetime
    content_type: Optional[str] = None
    etag: Optional[str] = None

    @property
    def modified_timestamp(self) -> float:
        return self.modified_time.timestamp()
```

### `FileStorage` ABC — required surface

| Method | Returns | Notes |
|---|---|---|
| `read_text(path, encoding="utf-8")` | `str` | `FileNotFoundError` if missing |
| `write_text(path, content, encoding="utf-8")` | `None` | Creates parents |
| `read_bytes(path)` | `bytes` | |
| `write_bytes(path, content)` | `None` | |
| `exists(path)` | `bool` | Discouraged — prefer catching `FileNotFoundError` to save an API call |
| `delete(path)` | `None` | **Idempotent** — no error if missing |
| `delete_batch(paths)` | `int` | S3 supports up to 1000 keys per `DeleteObjects` request |
| `get_metadata(path)` | `FileMetadata` | Single API call for size/mtime/type/etag |
| `list_files(prefix="", pattern=None)` | `Iterator[FileMetadata]` | Listing already returns metadata — never N+1 |

### Default-implemented helpers

- `get_size(path) -> int` — delegates to `get_metadata`
- `get_modified_time(path) -> float` — delegates to `get_metadata`
- `get_public_url(path, expires_in=3600) -> Optional[str]` — `None` for local; presigned/signed URL for cloud
- `get_local_path(path) -> Path` — local backend returns the real path; cloud backends download to `tempfile.NamedTemporaryFile` and return that. **This is the seam for tools that require a filesystem path** (pydub, ffmpeg subprocess, whisper). Caller is responsible for cleanup of temp files (or use a `with` adapter — see [Open questions](#open-questions)).
- `ensure_directory(path)` — no-op on cloud; mkdir on local

### Design principles

- **Cloud-first semantics, local adapts.** `delete` is idempotent because S3 is. `list_files` yields metadata because S3 listings include it. `LocalFileStorage` mimics these contracts even where the local FS would behave differently.
- **No N+1 metadata calls.** `get_size` is a property of `FileMetadata`, not a separate API call.
- **Forward-slash paths everywhere.** Object stores use `/`; local backend normalises Windows backslashes on input.

---

## Backends

### `LocalFileStorage`

- `base_path: str` — anchored at the resolved data root.
- `_resolve_path` validates that the resolved final path stays under `base_path` (raises `ValueError` on escape).
- `delete` uses `unlink(missing_ok=True)`; `delete_batch` iterates.
- `list_files` uses `Path.rglob`; pattern uses `glob` against the prefix.
- `get_local_path` returns the actual filesystem path — no temp file shenanigans.

⚠️ **Overlap with `PathManager._assert_inside_root`.** Both perform escape-resolution checks. The implementation must pick one of: (a) `LocalFileStorage` trusts `PathManager`-shaped inputs and skips its own check, or (b) the two checks coexist as defence-in-depth. Recommend (b) — cheap, and protects against someone constructing a `LocalFileStorage` directly without going through `PathManager`.

### `S3FileStorage`

- Constructor: `bucket`, `region="us-east-1"`, `prefix=""`, `endpoint_url=None`, `access_key_id`/`secret_access_key` optional (falls back to AWS chain — env, IAM role, profile).
- `endpoint_url` enables LocalStack / MinIO for tests and self-hosted S3-compatible deployments.
- Lazy-imports `boto3` so the dep is optional. Surfaces a clear `ImportError` with install hint.
- `read_bytes` → `get_object`; maps `NoSuchKey` to `FileNotFoundError`.
- `delete` → `delete_object` (already idempotent).
- `delete_batch` → `delete_objects` chunked at 1000.
- `get_metadata` → `head_object`.
- `list_files` → `list_objects_v2` paginator; pattern filtering uses `fnmatch` on the relative key (and on the basename, to support both `**/*.json` and `*.json`).
- `get_public_url` → `generate_presigned_url("get_object", ExpiresIn=...)`.
- `get_local_path` → `tempfile.NamedTemporaryFile(delete=False, suffix=Path(path).suffix, prefix="thestill_s3_")`.
- Extra (not in ABC): `upload_file(local, remote)` and `download_file(remote, local)` use boto3's high-level transfer manager → automatic multipart for files > 8MB. Important for original audio (often 50–200 MB).

### `GCSFileStorage`

- Constructor: `bucket`, `project=None`, `prefix=""`, `credentials_path=None` (falls back to ADC — env var, gcloud auth, GCE metadata).
- Lazy-imports `google.cloud.storage`. Already a transitive dep of Google Cloud Speech-to-Text in current `pyproject.toml`, so no new top-level dep needed for GCS support.
- API surface mirrors S3 backend point-for-point.
- `get_public_url` → `blob.generate_signed_url(expiration=timedelta(seconds=expires_in), method="GET")`.

---

## Integration with PathManager

**Keep `PathManager`. Don't replace it.** It does two jobs:

1. **Encodes the on-disk *layout*** — `original_audio/`, `clean_transcripts/{podcast_slug}/`, `corpus/persons/{slug}.md`, etc. This is intentional, well-named, audited code. None of it is going anywhere.
2. **Validates external strings** — slug regex + `_assert_inside_root` security guards from spec #25.

The split:

- `PathManager` produces the **relative path** (no `storage_path` prefix). Today it produces absolute `Path` rooted at `storage_path`; that becomes the local-only convenience method.
- `FileStorage` takes the relative path and does I/O.

Sketch:

```python
# Today
path = path_manager.original_audio_file("ep_abc.mp3")  # data/original_audio/ep_abc.mp3
path.write_bytes(audio_bytes)

# After
relative = path_manager.relative.original_audio_file("ep_abc.mp3")  # original_audio/ep_abc.mp3
storage.write_bytes(relative, audio_bytes)
```

`PathManager` grows a `.relative` accessor (or a `relative_to_root: bool = False` flag) that returns the path without the `storage_path` prefix. The existing absolute-path methods stay for callers that need `get_local_path` semantics directly.

**Do not duplicate slug validation** in `FileStorage`. Slug validation belongs in `PathManager` (input sanitisation at the boundary). `FileStorage` only sees already-validated relative paths.

---

## Configuration

Driven by env vars; surfaces in [`utils/config.py`](../thestill/utils/config.py).

```bash
# Backend selection
STORAGE_BACKEND=local     # local | s3 | gcs (default: local)

# Local (existing)
STORAGE_PATH=./data       # already exists

# S3
S3_BUCKET=thestill-data
S3_REGION=eu-west-1
S3_PREFIX=prod/           # optional, lets one bucket host multiple deployments
S3_ENDPOINT_URL=          # optional — set for LocalStack / MinIO / DigitalOcean Spaces
# AWS credentials follow standard boto3 chain; explicit
# AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY also honoured.

# GCS
GCS_BUCKET=thestill-data
GCS_PROJECT=my-project
GCS_PREFIX=prod/
GOOGLE_APPLICATION_CREDENTIALS=/path/to/sa.json   # standard ADC
```

Backend selection happens once at startup via a factory:

```python
def make_storage(config: Config) -> FileStorage:
    backend = config.storage_backend
    if backend == "local":
        return LocalFileStorage(base_path=config.storage_path)
    if backend == "s3":
        return S3FileStorage(bucket=config.s3_bucket, region=config.s3_region, prefix=config.s3_prefix, endpoint_url=config.s3_endpoint_url)
    if backend == "gcs":
        return GCSFileStorage(bucket=config.gcs_bucket, project=config.gcs_project, prefix=config.gcs_prefix)
    raise ValueError(f"unknown STORAGE_BACKEND={backend!r}")
```

Validates required keys at construction; **fails fast** on missing config (no silent fallback to local).

### Optional dependencies

```toml
[project.optional-dependencies]
s3 = ["boto3>=1.34", "mypy-boto3-s3>=1.34"]   # mypy-boto3-s3 is types-only
# gcs already covered by google-cloud-storage as a top-level dep
```

Install with `pip install thestill[s3]`.

---

## Migration phases

The diff is large but mechanically rote. Phase it so each PR keeps the tree green.

### Phase 0 — abstraction lands, nothing uses it

- Add `thestill/utils/file_storage.py` with `FileStorage`, `FileMetadata`, `LocalFileStorage` only.
- Add unit tests against `LocalFileStorage` covering: read/write text + bytes, idempotent delete, `delete_batch`, `get_metadata`, `list_files` with prefix and pattern, `get_local_path` round-trip, escape-attempt rejection.
- No production caller wired up yet. Pure addition; cannot break anything.

### Phase 1 — wire factory into config + DI

- Add `make_storage` factory and `STORAGE_BACKEND=local` default.
- Inject `FileStorage` instance into existing service-layer constructors. Default to `LocalFileStorage` so behaviour is unchanged.
- Add `.relative` accessor (or equivalent) to `PathManager`.

### Phase 2 — migrate file I/O call sites family-by-family

One PR per artifact family so each is reviewable and revertable:

1. **Digests** — write-only, single producer (`services/digest_generator.py`), small files. Lowest risk.
2. **Corpus pages** — write-only via `core/entity_page_writer.py`.
3. **Transcripts (raw + clean + sidecars)** — written by transcribers, read by cleaning + summary + MCP layers. Many call sites.
4. **Summaries + facts.**
5. **External transcripts.**
6. **Audio (original + downsampled).** Highest risk: pydub/ffmpeg/whisper need filesystem paths → forces every audio caller through `get_local_path`. Save for last.

Each PR: replace `Path.read_*`/`Path.write_*`/`open()` with `storage.read_*`/`storage.write_*`. For tools that need a real path, wrap with `get_local_path`. Add an integration test.

### Phase 3 — implement cloud backends

- `S3FileStorage` + `GCSFileStorage` modules.
- Tests: contract-equivalence suite (same tests run against `LocalFileStorage`, `S3FileStorage` against LocalStack, `GCSFileStorage` against fake-gcs-server).
- Optional dep groups in `pyproject.toml`.

### Phase 4 — presigned URLs for streaming

- `web/routes/api_episodes.py` audio endpoint returns `307` to `storage.get_public_url(...)` when backend supports it (cloud); falls back to `FileResponse` from `get_local_path` for local backend.
- Frontend audio player needs no changes.

### Phase 5 — opt-in cloud deployment in `:slim` Docker target

- Document `STORAGE_BACKEND=s3` configuration in the Docker spec.
- Add a smoke-test compose file with LocalStack so contributors can validate cloud paths without an AWS account.

---

## Cross-cutting concerns

### Spec #25 security guards must not regress

- `_assert_inside_root` and `_SLUG_RE` validation must still run on every external string. The migration moves I/O but **must not bypass `PathManager`** — the path-construction methods stay, only the `read`/`write` step is abstracted.
- The spec #25 audit predates this work; an item should be added to spec #26 (pre-deploy checklist) that re-verifies traversal resistance against each backend.

### `get_local_path` lifecycle

For S3/GCS, `get_local_path` writes to a `NamedTemporaryFile(delete=False, ...)` and returns its path. **Caller must clean up.** Forgetting is a slow disk leak. Two options:

- **Context-manager wrapper** — `with storage.local_copy(path) as p: ...` that auto-deletes on exit. Preferred. Add to ABC as a default-implemented helper using `contextlib`.
- **Caller responsibility** — current branch design. Workable but error-prone given how many transcribers / preprocessors handle audio.

Recommend the context-manager wrapper.

### Transactional writes

Several pipeline stages do "write the artifact, then update the DB." Today the FS write is fast and effectively atomic. With cloud backends:

- `write_bytes` is not atomic across multipart uploads.
- Network failures mid-write leave partial objects (S3) or no object (GCS).

For cloud backends, prefer `upload_file` (single API call for small, multipart for large) over chunked manual writes, and always update the DB *after* the upload returns. The pipeline already has DLQ + retry semantics ([#16](16-full-pipeline-and-failure-handling.md)) that handle this correctly if the upload raises.

### Cost shape

Every `exists()` call is an S3 `HeadObject` request. The codebase has a habit of "check exists, then read" patterns — these become 2× API calls under cloud backends. Phase 2 PRs should grep for `if path.exists():` before reads and rewrite to `try: storage.read_bytes(path) except FileNotFoundError:`.

---

## Open questions

1. **Per-artifact backend routing.** Should `STORAGE_BACKEND` be global, or per-artifact (e.g., audio in S3, corpus on local for Obsidian editing)? The latter is more flexible; the former is simpler. Recommendation: start global, add per-artifact override env vars (`STORAGE_BACKEND_AUDIO`, `STORAGE_BACKEND_CORPUS`) only when there's demand.
2. **Where do MCP and web servers cache cloud-pulled audio?** A long-running web server hitting `get_local_path` on every audio stream is wasteful. Recommendation: not the storage layer's problem — add an `LRUDiskCache` decorator if it bites in production.
3. **Migration of existing local data.** When a deployment flips `STORAGE_BACKEND=local` → `s3`, existing files don't auto-upload. Provide a one-shot `thestill migrate-storage --from local --to s3 --dry-run` CLI? Probably yes, but it's a follow-up spec.
4. **DB backups.** SQLite backups also benefit from S3. But that's `db_path`, not `FileStorage`. Out of scope here — flag for a separate spec.
5. **Cost telemetry.** Cloud requests cost money. Should we instrument `S3FileStorage` with request counters (via `structlog`)? Recommendation: yes, low-cost, helps spot N+1 regressions.

---

## Non-goals

- Postgres migration. Database state is not file state. ([#05](05-docker-deployment.md) defers this; this spec inherits the deferral.)
- Replacing `PathManager`. The path-shape and security-guard logic stay exactly where they are.
- Background sync between backends. No bidirectional replication, no "use S3 but mirror to local."
- Generic key-value store abstraction. `FileStorage` is for files; redis/memcached/etc. are not in scope.
- Per-tenant bucket isolation. Multi-user hosting is its own design problem ([#07](07-multi-user-web-app.md)).
- CDN integration. Presigned URLs are sufficient for v1; CloudFront / Cloud CDN can layer on top later without code changes.

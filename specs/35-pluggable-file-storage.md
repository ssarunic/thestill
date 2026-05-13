# Pluggable File Storage Backends

> **Status:** 📝 Draft — S3-on-AWS targeted for v1
> **Created:** 2026-05-08
> **Updated:** 2026-05-13
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

**2026-05-13 revision.** The likely production hosting environment is AWS, so this spec now commits to **S3 as the v1 cloud backend**. The `FileStorage` abstraction remains backend-agnostic — GCS is preserved as a design-equivalent future backend, but is explicitly deferred and not part of the initial cloud rollout. Sections below have been re-shaped to lead with S3 and AWS-specific operational concerns (IAM roles, KMS, lifecycle policies, VPC endpoints, region pinning).

---

## Motivation

The pipeline writes ~6 file artifact families per episode (original audio, downsampled WAV, raw transcript JSON, cleaned Markdown + JSON sidecar, summary, facts) plus corpus pages and digests. Today every byte lives on local disk under `data/`. Three forces push toward a storage abstraction, all of them now sharpened by an assumed **AWS production deployment target**:

1. **AWS production hosting.** When Thestill is deployed to AWS (EC2 / ECS / App Runner — to be decided in [#05](05-docker-deployment.md) follow-up), local-disk persistence is a liability: instances are ephemeral, EBS volumes are single-AZ and don't share across tasks, and ECS task storage caps make audio-heavy workloads impractical. S3 is the natural primary store — durable, regionally-redundant, IAM-gated, and decouples storage lifetime from compute.
2. **Docker / RPi5 deployment ([#05](05-docker-deployment.md)).** SD cards are slow and small; offloading audio + transcripts to S3 keeps the appliance lean. Spec #05 explicitly defers this and ships the slim image with local persistence — this spec is the natural follow-up. The same abstraction serves both the cloud deployment and the slim Docker target.
3. **Pre-signed URLs for the web player.** Streaming audio to the browser today goes through FastAPI, which serves the file from local disk. With S3, a presigned URL hands streaming directly to S3 (and optionally CloudFront later) — cheaper, faster, and survives server restarts. No NAT bandwidth on the egress path.

The point isn't to migrate everything off disk. The point is to make the storage layer *swappable* so each artifact family can live wherever makes sense (audio + transcripts in S3, SQLite stays local on EBS or migrates to RDS later, corpus pages stay local for Obsidian editing).

---

## Table of Contents

1. [Current state](#current-state)
2. [Proposed abstraction](#proposed-abstraction)
3. [Backends](#backends)
4. [Integration with PathManager](#integration-with-pathmanager)
5. [Configuration](#configuration)
6. [Migration phases](#migration-phases)
7. [AWS deployment concerns](#aws-deployment-concerns)
8. [Cross-cutting concerns](#cross-cutting-concerns)
9. [Open questions](#open-questions)
10. [Non-goals](#non-goals)

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

Two backends ship in v1: `LocalFileStorage` (existing behaviour) and `S3FileStorage` (new, AWS production target). `GCSFileStorage` is deferred — design preserved below so the abstraction stays cloud-neutral, but it is **not** part of the initial cloud rollout.

### `LocalFileStorage`

- `base_path: str` — anchored at the resolved data root.
- `_resolve_path` validates that the resolved final path stays under `base_path` (raises `ValueError` on escape).
- `delete` uses `unlink(missing_ok=True)`; `delete_batch` iterates.
- `list_files` uses `Path.rglob`; pattern uses `glob` against the prefix.
- `get_local_path` returns the actual filesystem path — no temp file shenanigans.

⚠️ **Overlap with `PathManager._assert_inside_root`.** Both perform escape-resolution checks. The implementation must pick one of: (a) `LocalFileStorage` trusts `PathManager`-shaped inputs and skips its own check, or (b) the two checks coexist as defence-in-depth. Recommend (b) — cheap, and protects against someone constructing a `LocalFileStorage` directly without going through `PathManager`.

### `S3FileStorage` (primary cloud backend)

- Constructor: `bucket`, `region="us-east-1"`, `prefix=""`, `endpoint_url=None`, `access_key_id`/`secret_access_key` optional (falls back to AWS chain — env, IAM role, profile). **In production on AWS, explicit keys should never be set** — rely on the EC2 instance profile or ECS task role. See [AWS deployment concerns](#aws-deployment-concerns).
- `endpoint_url` enables LocalStack / MinIO for tests and self-hosted S3-compatible deployments.
- Lazy-imports `boto3` so the dep is optional. Surfaces a clear `ImportError` with install hint.
- `read_bytes` → `get_object`; maps `NoSuchKey` to `FileNotFoundError`.
- `delete` → `delete_object` (already idempotent).
- `delete_batch` → `delete_objects` chunked at 1000.
- `get_metadata` → `head_object`.
- `list_files` → `list_objects_v2` paginator; pattern filtering uses `fnmatch` on the relative key (and on the basename, to support both `**/*.json` and `*.json`).
- `get_public_url` → `generate_presigned_url("get_object", ExpiresIn=...)`. Used by the web player audio endpoint.
- `get_local_path` → `tempfile.NamedTemporaryFile(delete=False, suffix=Path(path).suffix, prefix="thestill_s3_")`.
- Extra (not in ABC): `upload_file(local, remote)` and `download_file(remote, local)` use boto3's high-level transfer manager → automatic multipart for files > 8MB. Important for original audio (often 50–200 MB).
- **Server-side encryption.** `put_object` and `upload_file` calls pass `ServerSideEncryption="AES256"` by default; if `S3_KMS_KEY_ID` is set, switch to `"aws:kms"` with that key. Bucket default encryption belt-and-braces this at the AWS side.

### `GCSFileStorage` (deferred)

> Not shipping in v1. Notes preserved so the abstraction stays portable. Re-engage if a non-AWS hosting need surfaces.

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
STORAGE_BACKEND=local     # local | s3 (default: local). "gcs" reserved for future.

# Local (existing)
STORAGE_PATH=./data       # already exists

# S3 — primary cloud backend, used on AWS deployments
S3_BUCKET=thestill-data
S3_REGION=eu-west-1               # MUST match the EC2/ECS region to avoid cross-region transfer charges
S3_PREFIX=prod/                   # optional, lets one bucket host multiple deployments (prod/, staging/, dev-$user/)
S3_ENDPOINT_URL=                  # optional — set for LocalStack / MinIO in tests; LEAVE EMPTY in AWS
S3_KMS_KEY_ID=                    # optional — set to switch SSE from AES256 to aws:kms with this CMK
# AWS credentials: in production rely on the EC2 instance profile / ECS task role.
# AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY are honoured for local dev only.
```

Backend selection happens once at startup via a factory:

```python
def make_storage(config: Config) -> FileStorage:
    backend = config.storage_backend
    if backend == "local":
        return LocalFileStorage(base_path=config.storage_path)
    if backend == "s3":
        return S3FileStorage(
            bucket=config.s3_bucket,
            region=config.s3_region,
            prefix=config.s3_prefix,
            endpoint_url=config.s3_endpoint_url,
            kms_key_id=config.s3_kms_key_id,
        )
    raise ValueError(f"unknown STORAGE_BACKEND={backend!r}")
```

Validates required keys at construction; **fails fast** on missing config (no silent fallback to local).

### Future: GCS configuration (deferred)

Reserved for the deferred GCS backend. Not parsed by the v1 factory.

```bash
# GCS_BUCKET=thestill-data
# GCS_PROJECT=my-project
# GCS_PREFIX=prod/
# GOOGLE_APPLICATION_CREDENTIALS=/path/to/sa.json   # standard ADC
```

### Optional dependencies

```toml
[project.optional-dependencies]
s3 = ["boto3>=1.34", "mypy-boto3-s3>=1.34"]   # mypy-boto3-s3 is types-only
# gcs would reuse google-cloud-storage (already a transitive dep) when re-engaged
```

Install with `pip install thestill[s3]`. AWS deployment images should bake this in.

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

### Phase 3 — implement S3 backend

- `S3FileStorage` module with the surface described in [Backends](#backends).
- Contract-equivalence test suite: the same suite that already runs against `LocalFileStorage` (Phase 0) runs against `S3FileStorage` pointed at LocalStack in CI. This guarantees behavioural parity across backends.
- Optional dep group `thestill[s3]` in `pyproject.toml`.
- Document AWS IAM policy template (see [AWS deployment concerns](#aws-deployment-concerns)) alongside the backend.
- **GCS backend is deferred to a follow-up spec** — re-engage if a non-AWS hosting need surfaces. The abstraction stays GCS-shaped (idempotent delete, metadata in listings) so re-engaging is mechanical.

### Phase 4 — presigned URLs for streaming

- `web/routes/api_episodes.py` audio endpoint returns `307` to `storage.get_public_url(...)` when backend supports it (S3); falls back to `FileResponse` from `get_local_path` for local backend.
- Frontend audio player needs no changes.
- Optional follow-up: front S3 with CloudFront for cached / lower-latency delivery. Not required for v1 — presigned S3 URLs alone are fine for the expected request volume.

### Phase 5 — AWS production deployment

- Document `STORAGE_BACKEND=s3` configuration in the Docker spec ([#05](05-docker-deployment.md)), including the AWS-side resources (bucket, IAM policy, instance profile / task role, optional KMS key, lifecycle rules, VPC gateway endpoint).
- Provide a Terraform / CDK module fragment (or at least a reference set of `aws` CLI commands) that provisions the bucket + IAM + endpoint for a Thestill deployment.
- Add a smoke-test compose file with LocalStack so contributors can validate cloud paths without an AWS account.
- Update [#05](05-docker-deployment.md) to flag S3 as the recommended storage configuration for any non-RPi5 deployment.

---

## AWS deployment concerns

These items are specific to running Thestill on AWS with `STORAGE_BACKEND=s3`. They don't change the abstraction's shape, but they're load-bearing for the production rollout and need to be designed alongside the code.

### IAM: instance profile / task role, not access keys

- Production deployments **must** authenticate via the EC2 instance profile (or ECS task role / EKS service account / App Runner instance role). Never bake `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` into env files or container images.
- The `S3FileStorage` constructor already defers to the boto3 credential chain; the AWS deployment just needs the right IAM role attached to compute. Document the minimum policy:

  ```json
  {
    "Version": "2012-10-17",
    "Statement": [
      {
        "Sid": "ThestillObjectIO",
        "Effect": "Allow",
        "Action": [
          "s3:GetObject",
          "s3:PutObject",
          "s3:DeleteObject"
        ],
        "Resource": "arn:aws:s3:::thestill-data/prod/*"
      },
      {
        "Sid": "ThestillListing",
        "Effect": "Allow",
        "Action": "s3:ListBucket",
        "Resource": "arn:aws:s3:::thestill-data",
        "Condition": { "StringLike": { "s3:prefix": ["prod/*"] } }
      }
    ]
  }
  ```

- If `S3_KMS_KEY_ID` is set, add `kms:Encrypt`, `kms:Decrypt`, `kms:GenerateDataKey` on the specific CMK ARN.
- Local dev: developers use a named profile (`AWS_PROFILE=thestill-dev`) with a separate prefix (`STORAGE_PREFIX=dev-$USER/`) on the same bucket, or a separate dev bucket entirely.

### Bucket configuration

- **Block Public Access:** enabled on the bucket. Presigned URLs work regardless — they sign the request, not the object.
- **Default server-side encryption:** SSE-S3 (AES256) on the bucket, with `S3FileStorage` also passing SSE on writes as belt-and-braces. Switch to SSE-KMS only if compliance requires customer-managed keys.
- **Versioning:** off by default. Thestill artifacts are deterministically re-derivable from upstream feeds, so versioning is wasted storage. Reconsider only for `corpus/` if Obsidian editing moves to S3.
- **Bucket policy:** denies non-TLS access (`aws:SecureTransport`); denies any principal that isn't the Thestill role.

### Lifecycle policies

Audio files are large and rarely re-read after summarisation. Configure S3 lifecycle rules per artifact family — these run at the AWS side and require no application changes.

| Prefix | Suggested transition |
|---|---|
| `prod/original_audio/` | `STANDARD` → `STANDARD_IA` after 30 days → `GLACIER_IR` after 90 days |
| `prod/downsampled_audio/` | Expire after 30 days (cheap to re-derive from the original) |
| `prod/raw_transcripts/`, `prod/clean_transcripts/`, `prod/summaries/`, `prod/facts/`, `prod/digests/`, `prod/corpus/` | Stay in `STANDARD` (small, frequently read) |

Re-reading audio from Glacier IR for an unusual re-process is cheap and bounded; default storage costs without a lifecycle rule are not.

### VPC gateway endpoint for S3

- Provision a **VPC gateway endpoint** for S3 in the deployment's VPC. Gateway endpoints are free and route S3 traffic over the AWS backbone instead of through a NAT gateway.
- Without this, every `GetObject` / `PutObject` from a private-subnet instance bills NAT egress at $0.045/GB. For original audio (50–200 MB per episode, hundreds of episodes/week), this is the largest avoidable AWS cost.
- The endpoint is region-scoped; it works automatically once attached to the VPC route tables.

### Region pinning

- Set `S3_REGION` to match the compute region. Cross-region transfer is $0.02/GB and adds latency.
- For multi-region resilience: out of scope for v1. If it becomes a requirement, use S3 Cross-Region Replication at the bucket level — no application changes.

### Key layout and request rate

- Current `PathManager` layout already disperses object keys (episode IDs are GUID-like, podcast slugs vary), so we get S3 partition spread for free at expected scale.
- No need for the legacy hex-prefix sharding trick — S3 auto-partitions and the read/write rates are far below the 3500 PUT / 5500 GET per prefix-per-second ceiling.

### Cost telemetry

- Tag the bucket with `Project=thestill`, `Environment=prod`, etc. Cost Explorer + Cost Allocation Tags surface monthly spend per artifact family if prefixes are stable.
- Pair with the request-counter instrumentation in [Open questions](#open-questions) item 5 so application-side and AWS-side numbers can be reconciled.

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

- **GCS backend in v1.** The abstraction stays cloud-neutral, but the initial cloud rollout targets AWS / S3 only. Re-engage GCS only if a concrete non-AWS hosting need surfaces.
- Postgres migration / RDS. Database state is not file state. ([#05](05-docker-deployment.md) defers this; this spec inherits the deferral.)
- Replacing `PathManager`. The path-shape and security-guard logic stay exactly where they are.
- Background sync between backends. No bidirectional replication, no "use S3 but mirror to local."
- Generic key-value store abstraction. `FileStorage` is for files; redis/memcached/etc. are not in scope.
- Per-tenant bucket isolation. Multi-user hosting is its own design problem ([#07](07-multi-user-web-app.md)).
- CloudFront / CDN integration in v1. Presigned S3 URLs are sufficient for the expected request volume; CloudFront can layer on top later without code changes (Phase 4 note).
- Multi-region replication. Single-region S3 with versioning-off is the v1 footprint; CRR is an AWS-side bucket setting if it ever becomes a requirement.

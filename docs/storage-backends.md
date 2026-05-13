# Storage Backends

Thestill stores pipeline artefacts (audio, transcripts, summaries, corpus
pages, digests) through a pluggable backend abstraction defined in spec
[#35](../specs/35-pluggable-file-storage.md). Two backends ship today:

- **`local`** (default) — on-disk under `STORAGE_PATH`, matching the
  historical layout. Ideal for development, the slim Docker target, and
  RPi5 appliance deployments.
- **`s3`** — AWS S3 (or any S3-compatible store via `S3_ENDPOINT_URL`).
  The v1 cloud production target.

SQLite (`podcasts.db`) is always on local disk regardless of backend.
Database state is not file state.

> **What this page does NOT cover:** GCS is deferred per spec #35.
> Multi-region replication is not in v1 — use S3 Cross-Region Replication
> at the bucket level if you need it.

## Quick reference

```bash
# Local (default) — nothing to set
STORAGE_BACKEND=local
STORAGE_PATH=./data

# AWS S3 production
STORAGE_BACKEND=s3
S3_BUCKET=thestill-data
S3_REGION=eu-west-1            # match your compute region
S3_PREFIX=prod/                # optional, multi-deployment one-bucket pattern
S3_ENDPOINT_URL=               # leave empty for AWS
S3_KMS_KEY_ID=                 # empty = SSE-S3 (AES256), default
```

## Local backend

Zero configuration beyond `STORAGE_PATH`. Files land under the data root
in the same layout as before: `original_audio/`, `clean_transcripts/`,
`summaries/`, etc.

The backend defends against path traversal via the same resolution guard
`PathManager` uses (spec #25), so directly-constructed `LocalFileStorage`
instances stay safe even when the caller doesn't go through `PathManager`.

## S3 backend — AWS production

### 1. Install the optional extra

```bash
pip install -e ".[s3]"
```

This pulls `boto3` only when needed. The base install stays lean.

### 2. Provision AWS resources

Minimum set:

- **An S3 bucket** in the same region as your compute. Same-region
  traffic is free; cross-region is $0.02/GB and slower.
- **An IAM policy** attached to the role your compute runs as (EC2
  instance profile, ECS task role, EKS service account). Minimum policy:

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

- **A VPC gateway endpoint for S3** in your VPC. Gateway endpoints are
  free and keep S3 traffic on the AWS backbone. Without one, every
  `GetObject` / `PutObject` from a private-subnet instance bills NAT egress
  at $0.045/GB — typically the largest avoidable cost.

Recommended bucket configuration:

- **Block Public Access** enabled (presigned URLs work regardless — they
  sign the request, not the object).
- **Default server-side encryption** = SSE-S3 (AES256). Switch to SSE-KMS
  only if compliance demands customer-managed keys.
- **Versioning** off. Thestill artefacts are deterministically
  re-derivable from upstream feeds; versioning is wasted storage.
- **Bucket policy** denies non-TLS access (`aws:SecureTransport: false`).

### 3. Configure lifecycle rules

Audio files dominate storage cost and are rarely re-read after
summarisation. Rule of thumb:

| Prefix | Suggested transition |
|--------|----------------------|
| `prod/original_audio/` | `STANDARD` → `STANDARD_IA` after 30 days → `GLACIER_IR` after 90 days |
| `prod/downsampled_audio/` | Expire after 30 days (cheap to re-derive from the original) |
| `prod/raw_transcripts/`, `prod/clean_transcripts/`, `prod/summaries/`, `prod/facts/`, `prod/digests/`, `prod/corpus/` | Stay in `STANDARD` |

These run at the AWS side and need no application changes.

### 4. Set environment variables

In your deployment's `.env` or task-definition env block:

```bash
STORAGE_BACKEND=s3
S3_BUCKET=thestill-data
S3_REGION=eu-west-1
S3_PREFIX=prod/
```

**Never set** `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` in production.
The boto3 credential chain picks up the attached IAM role automatically.

If you need customer-managed KMS encryption:

```bash
S3_KMS_KEY_ID=arn:aws:kms:eu-west-1:111122223333:key/abcd-efgh
```

…and add `kms:Encrypt`, `kms:Decrypt`, `kms:GenerateDataKey` permissions
on the CMK to your IAM policy.

### 5. Verify

Start Thestill and check the structured log line:

```
file_storage_backend backend=s3 bucket=thestill-data region=eu-west-1 ...
```

A failed `S3_BUCKET` check or missing IAM permission fails fast at
startup, not on the first write.

## Local development against S3

Two options for testing the S3 backend without a real AWS account:

### LocalStack (recommended for CI / smoke tests)

[LocalStack](https://localstack.cloud/) emulates the S3 API in a
container. Point Thestill at it via `S3_ENDPOINT_URL`:

```bash
docker run -d -p 4566:4566 localstack/localstack
aws --endpoint-url http://localhost:4566 s3 mb s3://thestill-dev

STORAGE_BACKEND=s3
S3_BUCKET=thestill-dev
S3_ENDPOINT_URL=http://localhost:4566
S3_REGION=us-east-1
```

### MinIO (recommended for self-hosted production / on-prem)

[MinIO](https://min.io/) is a production-grade S3-compatible store. The
same `S3_ENDPOINT_URL` knob points Thestill at it — useful when you want
S3 semantics without AWS (e.g. RPi5 + MinIO on a NAS).

```bash
STORAGE_BACKEND=s3
S3_BUCKET=thestill
S3_ENDPOINT_URL=http://minio.local:9000
S3_REGION=us-east-1  # MinIO ignores this but boto3 requires it
```

## Choosing between local and S3

| Use case | Backend |
|----------|---------|
| Single-machine development | `local` |
| RPi5 appliance / small home server | `local` (or `s3` via MinIO if running across multiple hosts) |
| Docker compose on a VPS | `local` (cheaper, less moving parts) |
| AWS EC2 / ECS / Fargate / App Runner | `s3` |
| Anywhere with ephemeral compute (Lambda, autoscaling) | `s3` |
| Multi-host hosted service | `s3` |

## Operational considerations

### Cost shape

Every `exists()` call on S3 is a `HeadObject` request. The historical
"check exists, then read" pattern doubles request count under S3 —
prefer `try: storage.read_*() except FileNotFoundError:` where the
caller's flow allows. The pipeline's existing skip-if-already-done
short-circuits are fine to keep on `exists()`.

### Tagging for cost allocation

Tag the bucket with `Project=thestill`, `Environment=prod`,
`Owner=<team>`. Cost Explorer + Cost Allocation Tags surface monthly
spend per environment.

### Key layout & request rate

Current `PathManager` paths disperse keys naturally (UUID-like episode
IDs, varying podcast slugs), so S3 partitioning is automatic. No need
for the legacy hex-prefix sharding trick — Thestill's expected request
rate is well under the 3500 PUT / 5500 GET per-prefix-per-second ceiling.

### Audio streaming via presigned URLs

When `STORAGE_BACKEND=s3`, the web layer can serve audio via S3
presigned URLs (no proxying through FastAPI). This work is part of
spec #35 Phase 4 — not yet wired up. Today, the local backend's
`get_public_url` returns `None` and the audio endpoint streams the file
through the application.

## Migrating from local to S3

When flipping an existing deployment, existing local files don't
auto-upload. A `thestill migrate-storage` CLI is on the spec roadmap
(open question #3). Until then, sync manually:

```bash
aws s3 sync ./data/ s3://thestill-data/prod/ \
  --exclude "*.db" \
  --exclude "*.db-wal" \
  --exclude "*.db-shm" \
  --storage-class STANDARD
```

The `--exclude` flags skip the SQLite database files — those stay on
local disk regardless of backend.

## Troubleshooting

**`STORAGE_BACKEND=s3 requires S3_BUCKET to be set`** — set `S3_BUCKET`
in your env, or revert to `STORAGE_BACKEND=local`.

**`ImportError: S3FileStorage requires boto3`** — install the optional
extra: `pip install -e ".[s3]"`.

**`AccessDenied` on first write** — the attached IAM role lacks
`s3:PutObject` for the resource prefix, or the bucket policy denies
non-TLS access and your client isn't using HTTPS. Check CloudTrail to
identify which condition failed.

**Latency spikes for audio reads** — confirm the bucket region matches
the compute region (`S3_REGION` env var and the bucket's actual region
both). Cross-region reads add ~50-200 ms per request.

**High NAT gateway costs** — provision the VPC S3 gateway endpoint (free,
sees S3 traffic over the AWS backbone instead of through NAT).

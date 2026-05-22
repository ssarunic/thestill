# AWS Hosting (Phase 1 — beta)

> **Status:** 📝 Draft (2026-05-22)
> **Created:** 2026-05-22
> **Updated:** 2026-05-22
> **Author:** Engineering
> **Priority:** High — unblocks hosted beta
> **Related:** [05-docker-deployment.md](05-docker-deployment.md) (slim/full image), [35-pluggable-file-storage.md](35-pluggable-file-storage.md) (S3 backend), [40-storage-routing-ephemeral-vs-persistent.md](40-storage-routing-ephemeral-vs-persistent.md), [25-security-audit-and-hardening.md](25-security-audit-and-hardening.md), [26-pre-deploy-security-checklist.md](26-pre-deploy-security-checklist.md), [44-postgres-migration.md](44-postgres-migration.md) (DB prerequisite), [42-robustness-and-failure-mode-hardening.md](42-robustness-and-failure-mode-hardening.md), [19-refresh-performance.md](19-refresh-performance.md)

---

## Executive Summary

Host thestill on AWS for an initial "me + a few beta users" deployment,
sized for **~1000 followed podcasts / ~4000 new episodes per month**, with a
3–6 month horizon before re-evaluating. New requirement: **cache original
audio in S3 for 1–2 weeks** so that re-processing yields ad-stable timestamps
(today we only hand Dalston the live feed URL and never retain audio).

Two findings from the code drive the whole design:

1. **thestill is a single stateful node.** The task worker runs *in-process*
   inside the FastAPI server ([core/task_worker.py](../thestill/core/task_worker.py)),
   backed by a SQL queue in the same database with WAL pragmas
   ([core/queue_manager.py](../thestill/core/queue_manager.py)). That state
   lives on a real local disk with reliable file locking — which **rules out
   the usual "Fargate + EFS" default** (SQLite/NFS file-locking is a corruption
   foot-gun). Once the DB moves to Postgres (spec
   [#44](44-postgres-migration.md)) the app becomes stateless and Fargate
   becomes the natural target; until then it's "one EC2 box + EBS."

2. **Only Dalston needs a GPU.** thestill's runtime image ships no torch on
   purpose ([Dockerfile](../Dockerfile)); transcription is delegated. We are
   **co-locating Dalston on AWS in the same VPC**, which also makes the
   ~300 GB/mo of cached-audio traffic free (same-region S3↔EC2) instead of
   ~$27/mo of egress if Dalston pulled from outside AWS.

This spec is **gated on [#44](44-postgres-migration.md)**: we deploy with
Postgres (RDS) from day one, not SQLite, so there is no second DB migration
when we later go HA.

---

## Table of Contents

1. [Goals & Non-Goals](#goals--non-goals)
2. [Workload Assumptions](#workload-assumptions)
3. [Target Architecture](#target-architecture)
4. [Component Sizing](#component-sizing)
5. [Audio Caching Design](#audio-caching-design)
6. [Networking & Security](#networking--security)
7. [Secrets & Configuration](#secrets--configuration)
8. [Cost Estimate](#cost-estimate)
9. [Deployment Mechanics](#deployment-mechanics)
10. [Phase Plan & HA Trigger](#phase-plan--ha-trigger)
11. [Open Items & Risks](#open-items--risks)
12. [Cross-References](#cross-references)

---

## Goals & Non-Goals

### Goals

- A durable, low-ops hosted deployment good for 3–6 months at the stated scale.
- Keep all artifacts in S3 (reuse the [#35](35-pluggable-file-storage.md)
  backend, already production-ready).
- Cache original audio 1–2 weeks for timestamp stability, then auto-purge.
- Co-locate Dalston (GPU transcription) in-region to eliminate audio egress.
- Run the entity + semantic-search stack (`[entities]` extra) in the cloud.
- Make the eventual HA / multi-instance move a config change, not a migration.

### Non-Goals

- Multi-AZ / HA in Phase 1 (single node is acceptable for beta; see
  [Phase Plan](#phase-plan--ha-trigger)).
- Auto-scaling the app tier (the in-process worker is single-node until #44 +
  a separate worker service).
- Moving Dalston's own architecture into scope — it's deployed as its own
  image; we only specify *where* it runs and how thestill reaches it.
- LLM provider cost optimization (out of scope; noted as a TCO line item).

---

## Workload Assumptions

| Dimension | Assumption |
|---|---|
| Followed podcasts | ~1,000 feeds (refresh hot path — see [#19](19-refresh-performance.md)) |
| New episodes | ~4,000 / month (~133 / day) |
| Users | Operator + a handful of beta users |
| Avg episode audio | ~50–90 min, ~50–90 MB original |
| Audio retention | 14 days in S3, then lifecycle-expired |
| Transcription | Delegated to Dalston (GPU), async/polling |
| Clean + summarize | LLM API calls (network-bound, no local model) |
| Entities + search | GLiNER + ReFinED + sentence-transformers + pgvector |
| Region | `us-east-1` (cheapest; S3 region must match compute) |

GPU work is the dominant variable: at ~50 min/episode and ~10–15× realtime on
a T4, transcription is roughly **300–400 GPU-hours/month without diarization**.
Enabling diarization (pyannote) roughly doubles that and may exceed a single
T4's monthly capacity — see [Open Items](#open-items--risks).

---

## Target Architecture

```
                        Internet
                           │  443 (Caddy / Let's Encrypt TLS)
                  ┌────────▼─────────────────────────────────┐
                  │  VPC  (single AZ — beta)                  │
                  │                                           │
   you + beta ───▶│  ┌─────────────────────┐                 │
   users         │  │ app node             │   private IP    │
                  │  │ t4g.xlarge (ARM)     │────────────────▶┌──────────────────┐
                  │  │ 16 GiB, EBS 100GB    │   :8080         │ Dalston node     │
                  │  │ thestill[s3,web,     │                 │ g4dn.xlarge (T4) │
                  │  │   entities]          │                 │ GPU transcription│
                  │  │ FastAPI + in-proc    │                 │ Spot-friendly    │
                  │  │ worker               │                 │ EBS 100GB        │
                  │  └─────┬───────────┬────┘                 └────────┬─────────┘
                  │        │ 5432      │                               │
                  │  ┌─────▼───────┐   │  ┌──────────────────────────┐ │
                  │  │ RDS Postgres│   └─▶│ S3 Gateway VPC Endpoint   │◀┘
                  │  │ db.t4g.small│      │ (free, keeps S3 in-AWS)   │
                  │  │ + pgvector  │      └────────────┬─────────────┘
                  │  │ (private)   │                   │
                  │  └─────────────┘                   ▼
                  └────────────────────┐  ┌───────────────────────────────┐
                                       │  │ S3 bucket (STORAGE_BACKEND=s3) │
   Outbound (no NAT — public subnet):  │  │ original_audio/  ⟵ 14-day      │
   RSS + audio downloads, LLM APIs,    │  │ downsampled_audio/  lifecycle  │
   HuggingFace model pulls.            │  │ raw/clean transcripts,         │
                                       │  │ summaries, digests, corpus     │
                                       │  └───────────────────────────────┘
                                       └── (presigned audio URL → Dalston)
```

### What runs where

| Component | Choice | Rationale |
|---|---|---|
| App node | EC2 `t4g.xlarge` (Graviton, 16 GiB) | FastAPI + in-process worker + entity stack. ReFinED loads 4–6 GB resident; 16 GiB gives headroom. ARM saves ~25% vs x86. **Fallback:** `m7i.xlarge` (x86) if any ML wheel misbehaves on aarch64. |
| Dalston node | EC2 `g4dn.xlarge` (T4, x86) | Cheapest current-gen GPU; sufficient at this volume w/o diarization. x86 (T4 is Intel) — a separate image/arch from the ARM app, which is fine since they're separate services. |
| Database | RDS PostgreSQL `db.t4g.small` + pgvector | See [#44](44-postgres-migration.md). HA later = Multi-AZ checkbox, no migration. Vector index RAM scales independently of the app box. |
| Object storage | S3, `STORAGE_BACKEND=s3` | Reuse [#35](35-pluggable-file-storage.md). |
| S3 access path | S3 Gateway VPC Endpoint (free) | Both nodes (and Dalston's presigned-URL fetch) reach S3 privately — no NAT needed. |
| Web/TLS | Caddy on the app box (auto Let's Encrypt) | Zero AWS cost; the SPA is served by FastAPI itself (single origin). CloudFront/ALB are clean upgrades, not needed for beta. |
| Shell access | SSM Session Manager | No SSH, no bastion, no open :22. |
| Scheduling | systemd timer → `thestill digest` | Matches the batch "morning briefing" model. |

---

## Component Sizing

**App node EBS (100 GB gp3).** Must persistently hold, beyond the OS:

- Model caches that otherwise re-download every restart: `~/.cache/refined`
  (~6 GB), `~/.cache/huggingface` (embedding + GLiNER models, ~2–3 GB).
- Audio scratch for in-flight downloads before S3 upload.
- (Postgres is on RDS, not this volume.)

**Dalston node EBS (100 GB gp3).** Transcription models + temp audio. Use a
GPU AMI (AWS Deep Learning Base GPU, or Ubuntu + NVIDIA driver +
nvidia-container-toolkit), run the Dalston container with `--gpus all`.

**RDS.** Start `db.t4g.small` (2 GiB), 50 GB gp3, single-AZ. Expect to bump to
`t4g.medium`/`large` as the pgvector HNSW index grows — a low-downtime RDS
modify, not a migration. The `chunks`/embeddings table is the real growth
driver (~hundreds of vectors/episode × 4000/mo).

---

## Audio Caching Design

The 1–2 week caching requirement is mostly **one S3 lifecycle rule** plus a
small code seam — no new cron job:

1. **Lifecycle rule:** expire objects under `original_audio/` (and
   `downsampled_audio/`) after **14 days**. Server-side, automatic, free.
   Keep `DELETE_AUDIO_AFTER_PROCESSING=false` so audio survives the window.
2. **Feed Dalston the cached copy, not the live URL.** The plumbing exists:
   `S3FileStorage.get_public_url()` mints a presigned URL
   ([utils/file_storage/s3.py:309](../thestill/utils/file_storage/s3.py#L309)),
   and `DalstonTranscriber` already has a `use_url` branch that skips local
   download when handed an `audio_url`
   ([core/dalston_transcriber.py](../thestill/core/dalston_transcriber.py)).
   New flow: **download → S3 → presign → pass to Dalston**, which fetches via
   the S3 Gateway Endpoint (in-region, free). This is what guarantees
   ad-stable timestamps on re-processing.

This is the one behavioral change vs today's "send the live feed URL" path,
and it is small. It can ship independently of the rest of this spec.

---

## Networking & Security

Applies the [#25](25-security-audit-and-hardening.md) /
[#26](26-pre-deploy-security-checklist.md) posture, right-sized for beta:

- **VPC**, single AZ. Public subnet for the two EC2 instances; private subnet
  for RDS (no public IP). Skip NAT gateway in Phase 1 (~$46/mo saved) — public
  instances reach the internet directly; tight security groups + SSM (no SSH)
  keep the surface small.
- **Security groups:** ALB/Caddy port 443 from `0.0.0.0/0`; Dalston `:8080`
  from the app SG only; RDS `:5432` from the app SG only.
- **S3:** Block Public Access on; SSE-S3 by default (`S3_KMS_KEY_ID` switches
  to SSE-KMS if compliance later demands). Bucket reached via the Gateway
  Endpoint.
- **IAM:** each instance carries an instance-profile role — S3 read/write on
  the bucket, SSM, CloudWatch Logs. **No baked keys** — the
  [#35](35-pluggable-file-storage.md) backend already relies on the boto3
  credential chain.
- **TLS:** Caddy terminates with auto Let's Encrypt. `COOKIE_SECURE=true`,
  `ENVIRONMENT=production`, `ENABLE_DOCS=false`.

Upgrade path (Phase 2+): move instances to private subnets + NAT, put an ALB
(or CloudFront) in front, add WAF.

---

## Secrets & Configuration

- **Secrets** → SSM Parameter Store (SecureString), free for a beta's ~8–10
  keys: LLM provider keys, `HUGGINGFACE_TOKEN`, `DALSTON_API_KEY`,
  `JWT_SECRET_KEY`, Google OAuth (if `MULTI_USER=true`), RDS credentials.
  Move to Secrets Manager when rotation is wanted.
- **Key deploy env:** `STORAGE_BACKEND=s3`, `S3_BUCKET`, `S3_REGION=us-east-1`,
  `DATABASE_URL=postgresql://…` (per [#44](44-postgres-migration.md), replacing
  `DATABASE_PATH`), `DALSTON_BASE_URL=http://<dalston-private-ip>:8080`,
  `LOG_FORMAT=cloudwatch`.
- **Logging:** the app already supports `LOG_FORMAT=cloudwatch`
  ([.env.example](../.env.example)); ship container stdout to CloudWatch Logs.

---

## Cost Estimate

`us-east-1`, ~730 hr/mo. Instance prices verified against the AWS Price List
API (2026-05): `g4dn.xlarge` **$0.526/hr**, `t4g.xlarge` **$0.1344/hr**. Other
lines are well-known list prices, approximate.

| Item | Simplest (24/7 on-demand) | Cost-optimized |
|---|---:|---:|
| Dalston `g4dn.xlarge` | ~$384 | **Spot ~$140**, or scheduled window ~$60–210 |
| App `t4g.xlarge` | ~$98 | Compute Savings Plan (1yr) ~$69 |
| RDS `db.t4g.small` + 50 GB | ~$30 | ~$30 |
| EBS app 100 GB gp3 | ~$8 | ~$8 |
| EBS Dalston 100 GB gp3 | ~$8 | ~$8 |
| S3 (~180 GB audio + artifacts) | ~$7 | ~$7 |
| S3 Gateway Endpoint / NAT / ALB | $0 | $0 |
| CloudWatch logs + backups | ~$4 | ~$4 |
| **AWS total** | **~$540/mo** | **~$265–335/mo** |

**The GPU is ~70% of the bill and the only real lever.** The biggest single
saving is running Dalston on **Spot** (~$140 vs $384): thestill's SQL retry
queue plus the [#42](42-robustness-and-failure-mode-hardening.md) hardening
make a mid-job reclaim safe — it just re-queues. Recommended approach: run
on-demand for ~2 weeks to *measure* real GPU-hours and throughput, then flip to
Spot once a Dalston outage is confirmed to re-queue (not dead-letter).

**Not AWS, but the real recurring cost:** LLM API calls for clean + summarize
across 4000 episodes/mo — roughly **$40–200/mo** depending on model. Budget
separately.

---

## Deployment Mechanics

- **IaC:** CDK (TypeScript) — VPC, two EC2 instances + instance profiles, RDS,
  S3 bucket + lifecycle rule, S3 Gateway Endpoint, security groups, SSM, Param
  Store. Run `checkov`/`cdk-nag` before deploy per
  [#26](26-pre-deploy-security-checklist.md).
- **New image build target.** The current [Dockerfile](../Dockerfile) builds
  `slim`/`full` *without* the entity stack. Add a `cloud`/`entities` target
  that installs `.[s3,web,entities]` (pulls torch on ARM, gliner, refined,
  sentence-transformers, sqlite-vec→**replaced by pgvector**, see #44). Build
  `linux/arm64`.
- **Mount model caches** (`~/.cache/refined`, `~/.cache/huggingface`) on the
  EBS volume so they survive restarts.
- **Backups:** RDS automated backups + PITR (free with RDS) replace the
  SQLite `.backup` cron entirely. EBS snapshots (DLM) for the app box's caches
  are optional (caches are reproducible).
- **Scheduling:** systemd timer on the app box runs `thestill digest` on the
  desired cadence; alternatively EventBridge Scheduler hits the API.

---

## Phase Plan & HA Trigger

- **Phase 1 (this spec):** single app EC2 + Dalston GPU EC2 + RDS (single-AZ) +
  S3. No HA. Recovery = relaunch from AMI + RDS is already durable/PITR.
- **Phase 2 (HA, when triggered):** flip RDS to **Multi-AZ** (checkbox, no
  migration — the whole point of doing [#44](44-postgres-migration.md) now).
  Move instances to private subnets + NAT + ALB.
- **Phase 3 (scale-out, if needed):** with Postgres + `FOR UPDATE SKIP LOCKED`
  the worker can run as a *separate* service; the now-stateless app tier can
  move to Fargate behind the ALB.

**Triggers to advance:** sustained queue backlog the single worker can't drain,
the need for zero-downtime deploys, or a second region.

---

## Open Items & Risks

- **Diarization is the GPU swing factor.** If `ENABLE_DIARIZATION=true`,
  re-measure — a single T4 may not keep up with 4000 episodes/mo; consider
  `g5.xlarge` (A10G) or a second Dalston instance.
- **1000-feed refresh** at scale is exactly what [#19](19-refresh-performance.md)
  and [#42](42-robustness-and-failure-mode-hardening.md) address — keep that
  work landed before relying on unattended scheduling.
- **Single-AZ, single-node = no HA in Phase 1.** Acceptable for beta; RDS PITR
  - a documented relaunch are the recovery story.
- **ARM wheel risk** for the entity stack — validate `refined`/torch on
  Graviton before committing; `m7i.xlarge` is the x86 fallback.
- **Spot on Dalston** depends on clean re-queue on interruption — verify
  against [#42](42-robustness-and-failure-mode-hardening.md) before relying on
  it.

---

## Cross-References

- [05-docker-deployment.md](05-docker-deployment.md) — slim/full image this
  extends with an `entities` target.
- [35-pluggable-file-storage.md](35-pluggable-file-storage.md) /
  [40-storage-routing-ephemeral-vs-persistent.md](40-storage-routing-ephemeral-vs-persistent.md)
  — the S3 backend and what it routes.
- [44-postgres-migration.md](44-postgres-migration.md) — **prerequisite**;
  this spec assumes Postgres from day one.
- [25-security-audit-and-hardening.md](25-security-audit-and-hardening.md) /
  [26-pre-deploy-security-checklist.md](26-pre-deploy-security-checklist.md)
  — security posture + pre-deploy gate.
- [42-robustness-and-failure-mode-hardening.md](42-robustness-and-failure-mode-hardening.md)
  — failure modes that make Spot transcription safe.

# AWS Hosting on a Single EC2 Node (operator-scale)

> **Status:** ✅ Deployed and cut over (2026-08-06). Live at `https://thestill.me` on `i-098addb829485abf5` (t4g.medium, eu-west-2), Elastic IP `35.177.165.137`, Let's Encrypt TLS via Caddy. Full corpus migrated (5,479 episodes / 411,541 chunks / 793k entity mentions), Dalston reached over the private VPC path, refresh scheduler running the queued path. Local instance retired.
> **Created:** 2026-07-29
> **Updated:** 2026-08-06
> **Author:** Engineering
> **Priority:** High — moves the production instance off the dev machine
> **Related:** [43-aws-hosting.md](43-aws-hosting.md) (beta-scale plan; this spec supersedes its Phase-1 sizing), [05-docker-deployment.md](05-docker-deployment.md) (slim/full image), [44-postgres-migration.md](44-postgres-migration.md) (✅ done — unblocks everything here), [35-pluggable-file-storage.md](35-pluggable-file-storage.md) / [40-storage-routing-ephemeral-vs-persistent.md](40-storage-routing-ephemeral-vs-persistent.md) (S3 backend, optional here), [25-security-audit-and-hardening.md](25-security-audit-and-hardening.md), [26-pre-deploy-security-checklist.md](26-pre-deploy-security-checklist.md), [51-briefing-email-delivery.md](51-briefing-email-delivery.md) (SES phase)

---

## Executive Summary

Move the production thestill instance from the operator's dev machine to a
**single EC2 instance** that lifts the existing `docker-compose.yml` almost
verbatim: the `thestill:full` container, a Postgres container (pgvector), and
Caddy for TLS. Total AWS cost ≈ **$36/month** (eu-west-2, on-demand,
2026-07 prices).

Context that changed since [#43](43-aws-hosting.md) was drafted:

1. **[#44](44-postgres-migration.md) shipped** — production runs on Postgres
   behind `DATABASE_URL`. The "SQLite pins us to one box" constraint is gone.
2. **Dalston (GPU transcription) is already deployed on AWS** — the GPU node
   that dominated #43's cost model is a sunk/separate concern. thestill only
   needs network reachability to it.
3. **Scale is operator-scale, not beta-scale.** #43 sized for ~1,000 feeds /
   ~4,000 new episodes per month; the live system is an order of magnitude
   below that. Sizing here targets the real workload, with #43's phases kept
   as the growth path.

Why single EC2 rather than ECS Fargate: the app is a **mandatory singleton**
today — SSE progress is an in-memory store
([core/progress_store.py](../thestill/core/progress_store.py)) and the
refresh/briefing schedulers have no leader election — so Fargate's replica
management buys nothing, while its ephemeral filesystem would force the S3
storage migration on day one and Tailscale-based admin access is awkward
without a host. The container is identical in both worlds; Fargate remains
the Phase-3 target (see [Upgrade Path](#upgrade-path)).

---

## Goals & Non-Goals

### Goals

- Production instance on AWS, reachable over HTTPS on the operator's domain,
  surviving dev-machine reboots/travel.
- Reuse the existing `full` Docker image and compose topology unchanged.
- Reach the existing in-VPC Dalston deployment over **private VPC networking**
  (security-group to security-group), removing the overlay-network hop from
  the transcription data path. The overlay network (e.g. Tailscale) remains
  the *admin* plane only.
- Durable backups (nightly `pg_dump` + artifact sync to S3) with a documented
  restore path.
- Keep every step reversible and vendor-light: no step here blocks the #43
  Phase-2/3 growth path (RDS, S3 artifacts, ALB, Fargate).

### Non-Goals

- HA / Multi-AZ / autoscaling (see [#43 Phase Plan](43-aws-hosting.md#phase-plan--ha-trigger)).
- Moving artifact storage to S3 (`STORAGE_BACKEND=s3`) on day one — EBS +
  bind mount works verbatim; S3 is an independent follow-up via
  [#35](35-pluggable-file-storage.md).
- Audio caching / presigned-URL feeding of Dalston
  ([#43 Audio Caching Design](43-aws-hosting.md#audio-caching-design)) —
  still valid, still independent, not required to host.
- Dalston's own deployment (pre-existing, out of scope).
- IaC (CDK/Terraform) — optional at this scale; a documented manual build +
  the compose file is acceptable for one box. Revisit with Phase 2.

---

## Workload Assumptions

| Dimension | Assumption |
|---|---|
| Users | Operator (single-user mode or small allowlist) |
| Followed podcasts | ~100 feeds |
| Episode corpus | Low thousands, growing by tens/week |
| Database | Postgres, single-digit GB incl. pgvector HNSW indexes |
| Artifacts on disk | ~5–10 GB (transcripts, summaries, facts, briefings) |
| Audio | Deleted after processing (`DELETE_AUDIO_AFTER_PROCESSING=true`); playback streams from original enclosure URLs — no egress path through the app |
| Transcription | Dalston (already on AWS), async polling, server-side URL fetch |
| Region | Same region as the existing Dalston node (eu-west-2 in the reference deployment) — co-location is mandatory for free/fast private traffic |

---

## Target Architecture

```
                    Internet
                       │ 80/443
              ┌────────▼──────────────────────────────────────┐
              │ VPC (same VPC — or peered — as Dalston)        │
              │                                                │
              │  ┌──────────────────────────────┐              │
              │  │ EC2 t4g.medium (Graviton)     │  private IP  │
              │  │ 30 GB gp3, public subnet      │─────────────▶ Dalston node
              │  │                               │  (SG → SG)   │ (pre-existing GPU
              │  │  docker compose:              │              │  instance)
              │  │   • caddy      (TLS, 80/443)  │              │
              │  │   • thestill:full (:8000)     │              │
              │  │   • postgres   (pgvector)     │              │
              │  │                               │              │
              │  │  /data on EBS (bind mount)    │              │
              │  └───────────────┬───────────────┘              │
              │                  │ nightly cron                 │
              └──────────────────┼──────────────────────────────┘
                                 ▼
                    S3 bucket (versioned, lifecycle)
                    pg_dump.gz + artifact sync  ← backup/DR only
```

### Components

| Component | Choice | Rationale |
|---|---|---|
| Instance | **t4g.medium** (2 vCPU / 4 GiB, ARM) | Floor, not luxury: the app process holds sentence-transformers + torch (~1–1.5 GB RSS) and Postgres wants real memory for HNSW. `t4g.large` (8 GiB) if headroom is preferred over tuning. Graviton matches Apple-Silicon dev builds — images are arm64 natively, no cross-build. |
| Disk | 30 GB gp3 | OS + `/data` artifacts + Postgres volume + HF model cache. |
| App | `thestill:full` image | ffmpeg needed by the downsample stage; `slim` won't do. |
| Database | **Postgres container** (`pgvector/pgvector:pg17`) | Cheapest correct option at this scale. RDS `db.t4g.small` (+~$28/mo) is a drop-in upgrade when managed backups/patching are worth it — `DATABASE_URL` is the only change. |
| TLS / web | Caddy container (auto Let's Encrypt) | Zero AWS cost, single origin (SPA is served by FastAPI). ALB/CloudFront are Phase-2 upgrades. |
| Admin access | SSM Session Manager and/or the operator's overlay network | Port 22 is **not** exposed to the internet. |
| Backups | Nightly cron → `pg_dump \| gzip` + `aws s3 sync` of `/data` | Versioned bucket + lifecycle rule; see [Backups & Recovery](#backups--recovery). |
| Boot resilience | `restart: unless-stopped` (already in compose) + EC2 auto-recovery alarm (free) | Failed-host migration without human involvement. |

---

## Networking & Security

Applies the [#25](25-security-audit-and-hardening.md) /
[#26](26-pre-deploy-security-checklist.md) posture:

- **Placement:** public subnet, public IPv4 (Caddy terminates TLS). No NAT
  gateway (~$40/mo avoided) — the box reaches RSS/LLM APIs directly; tight
  security groups keep the surface small.
- **Security group (app):** inbound 80/443 from `0.0.0.0/0` only. No :22, no
  :8000, no :5432 exposure (Postgres and the app talk over the compose
  network only).
- **Security group (Dalston):** inbound on its service port **from the app
  SG only** (replaces the previous overlay-network reachability for the data
  path). If Dalston lives in a different VPC, use same-region VPC peering.
- **`DALSTON_BASE_URL`** points at Dalston's private DNS name (see the
  addressing note below). The SSRF URL guard needs an allowlist entry for
  this private URL — same mechanism as the localhost case in
  [docs/configuration.md](../docs/configuration.md).
- **IAM:** instance profile with least privilege — S3 read/write on the
  backup bucket, SSM core, CloudWatch Logs. No static AWS keys on the box.
- **S3:** Block Public Access on; SSE-S3; versioning on (ransomware/fat-finger
  protection for backups).

### Correction (2026-08-04): Dalston does not listen on its VPC interface

This spec assumed the app could reach Dalston over private VPC networking as
soon as an SG-to-SG rule existed. **That premise was wrong.** Dalston pins its
gateway to loopback (`127.0.0.1:8000:8000` in its `docker-compose.aws.yml`)
and publishes itself via `tailscale serve`; its M93 hardening milestone states
the intent plainly — *"AWS SGs: zero ingress rules … Host ports bind
tailscale0 / localhost only."* With the SG rule correctly in place the app
still got `Connection refused` (not a timeout — the packet arrived and nothing
was listening).

Two options were weighed:

| | Tailscale on the app box | VPC-private (chosen) |
|---|---|---|
| Change required | none to Dalston | one line: bind the VPC address |
| Blast radius if the app is compromised | every device the tailnet ACL permits — and the tailnet is currently default-allow | one TCP port on one host |
| Addressing | stable MagicDNS name | needs explicit work (below) |
| Encryption in transit | WireGuard | none (same-VPC) |

**Decision: VPC-private.** The app box is the only internet-facing host in
the estate (80/443 open to the world); granting it membership of the most
trusted network inverts the blast radius. An SG reference is a smaller trust
relationship — one source identity, one destination, one port — and it is
least-privilege by construction rather than by remembering to write an ACL.
Tailscale remains the **administrative** overlay for Dalston; the app box
never joins the tailnet at all, because its admin path is SSM Session Manager
(no daemon, no keys, IAM-controlled, CloudTrail-audited).

Bind to Dalston's **VPC address specifically**, not `0.0.0.0` — everything
else on that host stays loopback-only.

### Stable addressing for `DALSTON_BASE_URL`

`ip-172-31-29-119.eu-west-2.compute.internal` is derived from the private IP,
so it changes whenever Dalston is rebuilt. In increasing order of robustness:

1. **Private Route53 record** (`dalston.int.thestill.me`, zone associated with
   the VPC) — implemented 2026-08-04. This is indirection, *not* stability:
   nothing updates it automatically yet. `thestill-aws` should upsert it on
   every launch/reconcile.
2. **Persistent ENI** with a fixed private IP, reattached on rebuild — free,
   and sufficient while Dalston is a single instance.
3. **Internal ALB** — stable DNS, health checks, target replacement, and a
   natural path to multiple targets. Roughly $16–18/mo before LCUs, so worth
   buying when Dalston needs more than one target rather than pre-paying.

Note a private hosted zone answers only after propagation; a name queried
before the zone is live can be negatively cached for the parent zone's SOA
minimum (86400s here). Query a fresh name to distinguish propagation lag from
misconfiguration.

### What the cutover actually taught us (2026-08-04 → 08-06)

Five things went wrong that the plan did not anticipate. All are fixed; they
are recorded because each is a class, not an incident.

1. **Config drift, silent.** `ENABLE_DIARIZATION` never reached SSM, so every
   transcript on the new host came back speakerless — valid output, no error.
   Root cause was hand-picking which variables to migrate (28 were missed).
   Fixed by `thestill-aws secrets diff`; catalogued as
   [#42 FM-10](42-robustness-and-failure-mode-hardening.md).
2. **Collation-dependent CHECK.** `entity_cooccurrences` has
   `CHECK (a < b)`, which means different things under macOS libc and Debian
   glibc. One row of 1,089,070 flipped ordering and `pg_restore` discarded the
   entire table. Catalogued as [#42 FM-9](42-robustness-and-failure-mode-hardening.md);
   recovered by re-deriving from `entity_mentions`.
3. **A mid-chain stage that raises severs the pipeline.** The image omits the
   `entities` extra by design, so `extract-entities` raised — and because the
   chain is linear, `reindex` never ran and 105 episodes were readable but
   unsearchable. Entity stages now skip with `skipped_unavailable` and let the
   chain continue.
4. **Stale paths outlive their files.** Restored rows carried
   `downsampled_audio_path` values whose WAVs were long deleted, and the
   transcribe handler dead-lettered on the missing file instead of falling
   back to Dalston's URL fetch.
5. **Dalston does not listen on its VPC interface** (above). Cost a day of
   assuming the security group was at fault.

Sizing note: embedding work scales with **episodes, not users** — thestill
processes once and fans out (spec #29). At ~200 episodes/week and ~203 chunks
each, steady state is minutes of daily CPU, so `t4g.medium` is not the
constraint at 10-20 users; memory and the singleton architecture are. Bulk
work (a re-embed, an entity backfill) is better served by temporarily
resizing than by permanently buying a larger box — which is why the Elastic
IP matters: it makes stop/resize/start a no-op for DNS.

### Before Dalston is ever public

Not required for this deployment — the SG admits exactly one source SG on one
port — but a prerequisite for any public listener: public ALB with ACM TLS and
WAF, SG allowing Dalston only from the ALB SG, per-tenant API keys and quotas,
and admin/`/metrics`/key-administration surfaces kept off the public route.
Dalston's own review additionally flags unauthenticated `/metrics`, wide-open
CORS, no application-level upload ceiling, redirect-following after a single
SSRF hostname check, and multi-GB in-memory accumulation on URL ingestion.

---

## Configuration Deltas

Compose file changes vs the repo's dev `docker-compose.yml`:

1. Drop the dev-only `extra_hosts` overlay-IP mapping; set `DALSTON_BASE_URL`
   to the VPC-private address.
2. Add `postgres` (pgvector image) and `caddy` services; app gets
   `DATABASE_URL=postgresql://…@postgres:5432/thestill`.
3. Build target `full`, platform `linux/arm64`.
4. Persist the HuggingFace cache (`HF_HOME`) on the EBS bind mount — or bake
   the embedding model into the image — so restarts don't re-download from
   HuggingFace before search works.

Environment flips for production:

| Variable | Value |
|---|---|
| `PUBLIC_BASE_URL` | `https://<operator-domain>` (also add this redirect URI in the Google OAuth console) |
| `ENVIRONMENT` | `production` |
| `COOKIE_SECURE` | `true` |
| `ALLOWED_ORIGINS` | explicit origin list (the app rejects `*` at startup) |
| `LOG_FORMAT` | `json` (stdout → CloudWatch agent or `docker logs`) — no `LOG_FILE` |
| `STORAGE_PATH` / `DATABASE_PATH` | container defaults (`/data`) — unchanged |

Secrets (LLM keys, `JWT_SECRET_KEY`, OAuth client secret, `DALSTON_API_KEY`)
live in SSM Parameter Store (SecureString, free) and are materialized into the
compose `.env` at deploy time by a small fetch script — not committed, not
baked into images. A plain `.env` on the encrypted EBS volume is the
acceptable minimum for self-hosters.

---

## Migration (one-time)

1. Provision instance + SGs + IAM role; install Docker, compose plugin, and
   (optionally) the overlay-network agent for admin access.
2. `CREATE EXTENSION vector;` then restore a `pg_dump` of the current
   production Postgres into the container.
3. `rsync` artifacts into `/data`: `raw_transcripts/`, `clean_transcripts/`,
   `summaries/`, `briefings/`, `narrations/`, `episode_facts/`,
   `podcast_facts/`, `corpus/`. Skip `logs/`, `debug_feeds/`, survey/report
   scratch files.
4. Start compose with schedulers **disabled**, verify health/auth/search/
   playback, then enable `REFRESH_SCHEDULER_ENABLED` /
   `BRIEFING_SCHEDULER_ENABLED` and stop the dev-machine instance (two
   schedulers must never run against the same DB).
5. Cut DNS to the new box; confirm OAuth login on the new callback URL.

---

## Backups & Recovery

- **Nightly cron:** `pg_dump | gzip` to `s3://<backup-bucket>/pg/` and
  `aws s3 sync /data s3://<backup-bucket>/data/` (excluding audio scratch).
  Lifecycle: expire noncurrent versions after ~30 days. Cost: well under
  $1/mo.
- **Restore runbook:** new instance from the same bootstrap → restore latest
  dump → sync `/data` down → start compose → repoint DNS. Target: under an
  hour, data loss bounded by the nightly cadence.
- **Upgrade lever:** moving to RDS makes backups/PITR managed and continuous;
  that is the first thing to buy if the nightly window ever feels risky.

---

## Cost Estimate

eu-west-2, on-demand, ~730 hr/mo. Instance/RDS/EBS rates verified against the
AWS Price List API (2026-07-29): `t4g.medium` **$0.0376/hr**, `t4g.large`
**$0.0752/hr**, RDS `db.t4g.small` **$0.036/hr**, gp3 **$0.0928/GB-mo**.

| Item | Base | Notes |
|---|---:|---|
| EC2 `t4g.medium` | ~$27.50 | `t4g.large` ≈ $55 if headroom preferred |
| EBS 30 GB gp3 | ~$2.80 | |
| Public IPv4 | ~$3.65 | $0.005/hr |
| S3 backups | <$1 | ~10 GB, versioned |
| Route53 hosted zone | $0.50 | |
| **Total** | **≈ $36/mo** | |
| *Optional: RDS `db.t4g.small` + 20 GB* | *+~$28/mo* | *replaces the Postgres container* |

Not AWS, unchanged by this spec: LLM API usage (clean/summarize/briefings)
and Dalston's own instance cost. A 1-year Compute Savings Plan trims the EC2
line ~30% once the shape is proven.

For comparison, the evaluated ECS Fargate topology (1 vCPU/4 GB ARM task +
ALB + RDS + IPv4 charges) came to ≈ $95–105/mo and requires the singleton
fixes below before it can run more than one replica — deferred, not rejected.

---

## Upgrade Path

Ordered levers, each independent, none blocked by this spec:

1. **RDS Postgres** — config-only swap of `DATABASE_URL`; buys managed
   backups/PITR and Multi-AZ-by-checkbox later.
2. **`STORAGE_BACKEND=s3`** ([#35](35-pluggable-file-storage.md)) — removes
   the artifact tie to the box; prerequisite for any stateless compute.
3. **Audio caching + presigned Dalston URLs** —
   [#43's design](43-aws-hosting.md#audio-caching-design) applies unchanged.
4. **Singleton fixes** — externalize the SSE progress store (Postgres
   LISTEN/NOTIFY or Redis) and add scheduler leader election (Postgres
   advisory locks). These are the only two things pinning the app to one
   node.
5. **Fargate + ALB** — after 1–4, the #43 Phase-3 shape becomes a
   config/infra change, not a migration.

---

## Open Items & Risks

- **Briefing email delivery** ([#51](51-briefing-email-delivery.md)): the
  configured email provider's credentials must move with the deployment; SES
  (Phase 4 of #51) becomes natural once on AWS — including its sandbox-exit
  and bounce-handling work.
- **arm64 wheel validation:** the `full` image is torch-free, but the
  in-process embedding model (sentence-transformers) must be verified on
  Graviton before cutover; fall back to a `t3.medium`-class x86 instance if
  any wheel misbehaves.
- **Postgres-in-container ops:** major-version upgrades and vacuum/memory
  tuning are on the operator until the RDS lever is pulled.
- **Single node = no HA** — accepted; recovery is the documented restore
  runbook plus EC2 auto-recovery.
- **Dalston coupling:** if the Dalston instance is stopped/rebuilt, its
  private IP may change — prefer a stable private DNS name or an ENI with a
  fixed IP for `DALSTON_BASE_URL`.

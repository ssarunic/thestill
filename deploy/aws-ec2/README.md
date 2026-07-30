# AWS single-EC2 deployment kit (spec #66)

Operational assets for running thestill on one EC2 instance:

- `docker-compose.prod.yml` — app (GHCR `prod` image) + Postgres (pgvector) + Caddy (auto-TLS)
- `Caddyfile` — HTTPS termination, SSE-safe proxying
- `fetch-secrets.sh` — materializes `.env` from SSM Parameter Store
- `backup.sh` — nightly `pg_dump` + artifact sync to S3 (run from cron)

The step-by-step runbook — provisioning, first deploy, data migration,
backup/restore drill, upgrades — lives in
[docs/aws-deployment.md](../../docs/aws-deployment.md). Design rationale and
cost model: [specs/66-aws-single-ec2-hosting.md](../../specs/66-aws-single-ec2-hosting.md).

A future Fargate/ALB topology (spec #43 Phase 3) would live in a sibling
`deploy/fargate/` directory; nothing here assumes it.

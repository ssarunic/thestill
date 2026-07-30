# AWS Deployment Runbook (single EC2)

Operator runbook for the spec #66 topology: one EC2 instance running the
`prod` Docker image, a pgvector Postgres container, and Caddy for TLS. The
*why* (sizing, cost model, upgrade path) lives in
[specs/66-aws-single-ec2-hosting.md](../specs/66-aws-single-ec2-hosting.md);
this document is the *how*. The compose file and scripts referenced here are
in [deploy/aws-ec2/](../deploy/aws-ec2/).

## Quick path: `thestill-aws`

[`deploy/aws-ec2/thestill-aws`](../deploy/aws-ec2/thestill-aws) automates
everything in sections 1–3 and 5 below. Same shape as dalston's
`dalston-aws`: a single-file boto3 CLI, split into a cheap idempotent
`setup` and a billable `launch`.

```bash
alias thestill-aws='./venv/bin/python deploy/aws-ec2/thestill-aws'

# 1. Shared infra: S3 bucket, IAM role/profile, security groups, derived
#    secrets. No billable resources. Safe to re-run at any time.
#    --alias is repeatable; each alias 301s to the canonical --domain.
thestill-aws setup --domain thestill.ai --alias thestill.me --dalston-sg sg-0abc123

# 2. Your own secrets (API keys, Dalston credentials) from a local file
thestill-aws secrets put --from-env-file prod-secrets.env

# 3. The instance + full cloud-init bootstrap (docker, compose, deploy kit,
#    secrets fetch, containers, nightly backup timer)
thestill-aws launch

# 4. Point the domain at it (Route53)
thestill-aws dns

# 5. Watch it come up
thestill-aws status
```

**Every command is idempotent.** `setup` is get-or-create throughout;
`launch` no-ops when an instance is already running and starts it when
stopped; `dns` skips a record that already matches; `reconcile` replays the
same converging bootstrap over SSM. After any failure, re-run the command
rather than unpicking partial state.

Day-2 operations:

| Command | Purpose |
|---|---|
| `thestill-aws status` | instance state, bootstrap progress, containers, `/health/ready` |
| `thestill-aws logs --service thestill --lines 100` | container logs via SSM |
| `thestill-aws ssh` | shell via SSM Session Manager (no key pair, no port 22) |
| `thestill-aws reconcile --image-tag prod-<sha>` | upgrade or re-apply on-box config in place |
| `thestill-aws secrets list` | parameter names under `/thestill/prod/` (never values) |
| `thestill-aws teardown` | delete instance/SG/IAM; keeps S3, secrets and DNS |

What the script deliberately does **not** do: the data migration (section 4
— restoring your dump is a decision, not a step), the Google OAuth redirect
URI (no API exists), and the cutover scheduler flip (section 6). Those stay
manual and deliberate.

### Multiple domains

One domain is canonical; every other domain permanently redirects to it:

```bash
thestill-aws setup --domain thestill.ai \
  --alias thestill.me --alias www.thestill.ai --alias thestill.dev
thestill-aws reconcile     # regenerate the redirects on a running box
thestill-aws dns           # A records for the canonical domain AND every alias
```

Caddy serves only the canonical domain and 301s the aliases to it, so
sessions, the OAuth callback, briefing-email links and `ALLOWED_ORIGINS`
all stay single-origin — you register **one** redirect URI in the Google
console and users keep one session no matter which domain they typed.

This is deliberate rather than incidental. Serving two domains as co-equal
origins is not a config change: `thestill.me` and `thestill.ai` are
different registrable domains with separate cookie jars, so a user would
have to log in once per domain, and briefing emails — which have no request
context — would still have to pick one domain for their links.

Each domain needs its own A record. `dns` resolves the hosted zone per
domain, so aliases in a different Route53 zone work automatically; any
alias hosted outside Route53 is reported with the IP to point it at, and
does not block the others. Caddy issues a certificate per domain on first
request, so DNS must resolve before the redirect works.

The rest of this document is the manual equivalent — useful for
understanding what the script does, for a non-Route53 DNS provider, or for
recovering when something needs doing by hand.

## 1. Provision

1. **Instance**: `t4g.medium` (Graviton/arm64), 30 GB gp3, Amazon Linux 2023
   arm64 AMI, public subnet with a public IPv4. Same VPC (or a peered VPC in
   the same region) as the Dalston node.
2. **Security group (app)**: inbound 80/443 from `0.0.0.0/0`. Nothing else —
   no 22 (use SSM Session Manager), no 8000, no 5432.
3. **Security group (Dalston)**: allow inbound on its service port from the
   app security group only.
4. **IAM instance profile** (least privilege):
   - `ssm:GetParametersByPath` (+ `kms:Decrypt` on the SSM default key) for
     `/thestill/prod/*`
   - `s3:PutObject`, `s3:GetObject`, `s3:ListBucket` on the backup bucket
   - `AmazonSSMManagedInstanceCore` for Session Manager
5. **Backup bucket**: Block Public Access on, SSE-S3, versioning on,
   lifecycle rule expiring noncurrent versions after 30 days.
6. **EC2 auto-recovery alarm** (free): recover the instance on failed system
   status checks.

## 2. Install

```bash
sudo dnf install -y docker jq
sudo systemctl enable --now docker
sudo usermod -aG docker ec2-user
# compose v2 plugin
DOCKER_CONFIG=${DOCKER_CONFIG:-$HOME/.docker}
mkdir -p "$DOCKER_CONFIG/cli-plugins"
curl -SL "https://github.com/docker/compose/releases/latest/download/docker-compose-linux-aarch64" \
  -o "$DOCKER_CONFIG/cli-plugins/docker-compose"
chmod +x "$DOCKER_CONFIG/cli-plugins/docker-compose"
```

Fetch the deploy kit (no full checkout needed — the image comes from GHCR):

```bash
sudo mkdir -p /srv/thestill && sudo chown ec2-user /srv/thestill
cd /srv/thestill
curl -fsSLO https://raw.githubusercontent.com/ssarunic/thestill/main/deploy/aws-ec2/docker-compose.prod.yml
curl -fsSLO https://raw.githubusercontent.com/ssarunic/thestill/main/deploy/aws-ec2/Caddyfile
curl -fsSLO https://raw.githubusercontent.com/ssarunic/thestill/main/deploy/aws-ec2/fetch-secrets.sh
curl -fsSLO https://raw.githubusercontent.com/ssarunic/thestill/main/deploy/aws-ec2/backup.sh
chmod +x fetch-secrets.sh backup.sh
mkdir -p data
```

## 3. Secrets

Create one SSM parameter per env var under `/thestill/prod/` (SecureString
for secrets), then materialize `.env`:

```bash
# POSTGRES_PASSWORD is interpolated into DATABASE_URL unescaped — generate
# it hex-only (base64's '/', '+', '=' would corrupt the connection URI).
aws ssm put-parameter --type SecureString --name /thestill/prod/POSTGRES_PASSWORD --value "$(openssl rand -hex 32)"
aws ssm put-parameter --type SecureString --name /thestill/prod/JWT_SECRET_KEY --value '...'
# ... GEMINI_API_KEY, GOOGLE_CLIENT_ID/SECRET, DALSTON_API_KEY, etc.
aws ssm put-parameter --type String --name /thestill/prod/PUBLIC_DOMAIN --value 'thestill.example.com'
aws ssm put-parameter --type String --name /thestill/prod/PUBLIC_BASE_URL --value 'https://thestill.example.com'
aws ssm put-parameter --type String --name /thestill/prod/ALLOWED_ORIGINS --value 'https://thestill.example.com'
aws ssm put-parameter --type String --name /thestill/prod/DALSTON_BASE_URL --value 'http://<dalston-private-dns>:<port>'
aws ssm put-parameter --type String --name /thestill/prod/URL_GUARD_ALLOWLIST --value '<dalston-private-dns>'

./fetch-secrets.sh
```

Notes:

- `DALSTON_BASE_URL` points at Dalston's **VPC-private** address; that host
  must also be in `URL_GUARD_ALLOWLIST` or the SSRF guard will block it.
  Prefer a stable private DNS name over a raw IP (spec #66 risk item).
- `DATABASE_URL` is **not** an SSM parameter — the compose file assembles it
  from `POSTGRES_PASSWORD`.
- Add the `https://<domain>/api/auth/callback` redirect URI in the Google
  OAuth console before cutover.

## 4. Migrate data (one-time)

1. Start only Postgres: `docker compose -f docker-compose.prod.yml up -d postgres`
2. Restore the latest dump from the old machine:

   ```bash
   gunzip -c dump.sql.gz | docker compose -f docker-compose.prod.yml exec -T postgres psql -U thestill thestill
   ```

3. Sync artifacts into `./data`: `raw_transcripts/`, `clean_transcripts/`,
   `summaries/`, `briefings/`, `narrations/`, `episode_facts/`,
   `podcast_facts/`, `corpus/`. Skip `logs/` and `debug_feeds/`.
4. Start the rest **with schedulers off** (`REFRESH_SCHEDULER_ENABLED=false`,
   `BRIEFING_SCHEDULER_ENABLED=false` in SSM/.env for the first boot):
   `docker compose -f docker-compose.prod.yml up -d`
5. Verify readiness — the compose healthcheck polls `/health/ready`, so
   `docker compose -f docker-compose.prod.yml ps` showing the app as
   `healthy` means the DB round-trip works. Then check login, search, and
   playback through Caddy once DNS resolves.
6. Flip the scheduler flags on in SSM, re-run `./fetch-secrets.sh`,
   `docker compose up -d`, and **stop the old instance** — two schedulers
   must never run against the same database.
7. Cut DNS to the instance's IP; confirm OAuth login on the new callback.

The app container runs `alembic upgrade head` itself on every boot
(`MIGRATE_ON_STARTUP=true` in the compose file), so schema drift between
image versions is handled automatically. A slow migration extends the deploy
window — watch `docker compose logs -f thestill` rather than assuming a hung
healthcheck means failure.

## 5. Backups

```bash
crontab -e
# 0 3 * * * BACKUP_BUCKET=<bucket> /srv/thestill/backup.sh >> /var/log/thestill-backup.log 2>&1
```

**Restore drill** (target: under an hour, loss bounded by the nightly cadence):

1. Provision a fresh instance per §1–2.
2. `aws s3 cp s3://<bucket>/pg/<latest>.sql.gz .` and restore per §4.
3. `aws s3 sync s3://<bucket>/data/ ./data`
4. `docker compose -f docker-compose.prod.yml up -d`, verify
   `/health/ready`, repoint DNS.

## 6. Upgrades

```bash
cd /srv/thestill
docker compose -f docker-compose.prod.yml pull thestill
docker compose -f docker-compose.prod.yml up -d thestill
```

Pin `THESTILL_IMAGE_TAG=prod-<git-sha>` in `.env` for controlled rollouts;
the default is `prod-latest` (pushed by CI on every merge to `main`).
Rollback is the same command with the previous sha tag — migrations are
forward-only, so verify a revision is backward-compatible before shipping
schema changes you may want to roll back across.

## Compose-level variables

These are read by `docker-compose.prod.yml`/`Caddyfile`, not by the app
itself (so they appear here rather than in
[configuration.md](configuration.md)):

| Variable | Purpose |
|---|---|
| `POSTGRES_PASSWORD` | Postgres superuser password; also feeds the assembled `DATABASE_URL` |
| `PUBLIC_DOMAIN` | Bare hostname Caddy serves (certificate + site address) |
| `THESTILL_IMAGE_TAG` | Image tag to run (default `prod-latest`) |
| `BACKUP_BUCKET` | S3 bucket for `backup.sh` (cron env, not `.env`) |

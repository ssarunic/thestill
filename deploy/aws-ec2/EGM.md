# deploy/aws-ec2: single-EC2 deployment kit

## Overview

This folder holds the files that run thestill in production on one EC2 instance (spec 66).
`thestill-aws` is a single-file boto3 CLI. It provisions and manages the AWS resources: S3,
IAM, security groups, EC2, Elastic IP, CloudWatch, Route53, SSM Parameter Store and the GitHub
OIDC deploy role. It also creates the bash bootstrap script for the instance. The other four
files are downloaded onto the instance from `raw.githubusercontent.com` at a pinned git ref:
a docker-compose stack (Caddy, thestill, Postgres), the Caddyfile, a secrets-to-`.env` script
and a nightly backup script. `thestill-aws` uses only the standard library and boto3, and
imports nothing from the `thestill` package.

[README.md](./README.md) in this folder is written by people and is kept up to date by hand.
It links to the runbook (`docs/aws-deployment.md`) and to the spec. This EGM.md adds the
code-grounded contract.

## Interface

### `thestill-aws` subcommands (`deploy/aws-ec2/thestill-aws:build_parser`)

| Subcommand | Flags | Handler |
| --- | --- | --- |
| `setup` | `--domain` (required), `--alias` (repeatable), `--region` (default `eu-west-2`), `--dalston-sg`, `--dalston-port` (default 8080), `--vpc` | `thestill-aws:cmd_setup` |
| `secrets {put,list,diff}` | `--from-env-file`, `--against` (default `.env`), `--overwrite` | `thestill-aws:cmd_secrets`, `thestill-aws:_secrets_diff` |
| `launch` | `--instance-type` (default `t4g.large`), `--volume-size` (default 40), `--image-tag` (default `prod-latest`), `--kit-ref`, `--no-eip` | `thestill-aws:cmd_launch` |
| `dns` | `--no-wait` | `thestill-aws:cmd_dns` |
| `reconcile` | `--image-tag` (default `state.image_tag`), `--kit-ref` (default `state.kit_ref` or `main`) | `thestill-aws:cmd_reconcile` |
| `status` | `--expect-image-tag`, `--wait N` | `thestill-aws:cmd_status` |
| `state {show,push}` | `--force` | `thestill-aws:cmd_state` |
| `github-oidc` | `--environment` (default `production`) | `thestill-aws:cmd_github_oidc` |
| `logs` | `--service` (default empty, meaning all services; the module docstring wrongly says `thestill`), `--lines` (default 50) | `thestill-aws:cmd_logs` |
| `ssh` | none | `thestill-aws:cmd_ssh` |
| `teardown` | `--yes` | `thestill-aws:cmd_teardown` |

### `thestill-aws` constants and helpers

- Names and paths:
  - `thestill-aws:NAME_PREFIX` is `thestill`.
  - `thestill-aws:STATE_FILE` is `~/.thestill/aws-state.json`.
  - `thestill-aws:AUDIT_LOG` is `~/.thestill/audit.log`.
  - `thestill-aws:SSM_PREFIX` is `/thestill/prod/` and holds the secrets.
  - `thestill-aws:DEPLOY_STATE_PARAM` is `/thestill/deploy/state` and holds the state mirror.
  - `thestill-aws:GITHUB_DEPLOY_ROLE` is `thestill-github-deploy`.
  - `thestill-aws:GITHUB_REPO` is `ssarunic/thestill`.
  - `thestill-aws:DEFAULT_IMAGE_TAG` is `prod-latest`.
- Instance and bootstrap:
  - `thestill-aws:DEFAULT_REGION` is `eu-west-2`.
  - `thestill-aws:INSTANCE_TYPE` is `t4g.large`.
  - `thestill-aws:ROOT_VOLUME_SIZE` is `40`.
  - `thestill-aws:REMOTE_DIR` is `/srv/thestill`.
  - `thestill-aws:BOOTSTRAP_MARKER` is `/var/lib/thestill-bootstrap-complete`.
  - `thestill-aws:BACKUP_ONCALENDAR` is `*-*-* 03:00:00`.
  - `thestill-aws:CONTAINER_UID` is `1000`.
- Secrets that must exist:
  - `thestill-aws:REQUIRED_SECRETS`: `JWT_SECRET_KEY`, `DALSTON_BASE_URL`, `POSTGRES_PASSWORD`,
    `PUBLIC_DOMAIN`, `PUBLIC_BASE_URL` and `ALLOWED_ORIGINS`.
  - `thestill-aws:LLM_SECRETS`: at least one of the Gemini, OpenAI, Anthropic or Mistral API
    keys.
- State model and persistence:
  - `thestill-aws:Deployment` is a dataclass with these fields: region, domain, aliases, VPC,
    subnet, security group, Dalston SG and port, bucket, role, profile, instance ID, image
    tag, kit ref, EIP allocation and created-at.
  - The functions that read and write it are `thestill-aws:save_state`, `thestill-aws:push_state`,
    `thestill-aws:load_state_with_source`, `thestill-aws:load_state` and `thestill-aws:require_state`.
- Helpers for get-or-create:
  - `thestill-aws:ensure_bucket` creates or finds `thestill-backups-<account>`.
  - `thestill-aws:ensure_iam` creates or finds `thestill-app-role` and `thestill-app-profile`.
  - `thestill-aws:ensure_app_sg` creates or finds `thestill-app-sg`.
  - The other helpers of this kind are `thestill-aws:ensure_dalston_rule`, `thestill-aws:ensure_elastic_ip`,
    `thestill-aws:ensure_github_oidc_provider` and `thestill-aws:upsert_a_record`.
- Other helpers:
  - `thestill-aws:put_secret` and `thestill-aws:list_secrets` write and read the secrets.
  - `thestill-aws:parse_env_file` reads a local `.env` file.
  - `thestill-aws:preflight_secrets` checks that the required secrets exist.
  - `thestill-aws:build_user_data` returns the bash bootstrap script.
  - `thestill-aws:run_remote` returns `(exit_code, output)` from an SSM Run Command.
  - `thestill-aws:default_kit_ref` and `thestill-aws:deploy_kit_base` choose and locate the
    deploy-kit ref.

### Files fetched onto the instance

- `deploy/aws-ec2/docker-compose.prod.yml` defines three services:
  - `thestill`: the `ghcr.io/ssarunic/thestill` image, tagged `${THESTILL_IMAGE_TAG:-prod-latest}`.
  - `postgres`: `pgvector/pgvector:pg17`.
  - `caddy`: `caddy:2`.
- `deploy/aws-ec2/Caddyfile` serves the `{$PUBLIC_DOMAIN}` site:
  - It proxies `/ingest/*` and `/ingest/static/*` to PostHog EU.
  - Every other path goes to `thestill:8000`.
  - It imports `/etc/caddy/redirects.caddy`.
- `deploy/aws-ec2/fetch-secrets.sh` reads env `SSM_PREFIX` (default `/thestill/prod/`) and
  writes `./.env`.
- `deploy/aws-ec2/backup.sh` requires env `BACKUP_BUCKET`.

## Invariants

- State and auditing:
  - `thestill-aws:load_state_with_source`: the state in SSM (`DEPLOY_STATE_PARAM`) wins over
    the local file. The local file is used only if the SSM read fails or the parameter does
    not exist. This stops a stale laptop cache from rolling prod back.
  - `thestill-aws:push_state`: if the SSM mirror write fails, the command only warns and
    carries on.
  - `thestill-aws:save_state`: the state directory has mode 0700 and the state file has mode
    0600.
  - `thestill-aws:_state_from_dict` drops unknown keys.
  - `thestill-aws:audit` appends one JSONL entry per audited action (bucket, IAM role and
    SG creation, the Dalston rule, DNS upsert, `setup`, `secrets put`, launch and start,
    Elastic IP allocation and association, `reconcile`, GitHub OIDC, `state push`,
    termination, EIP release and `teardown`). Some mutating calls write no entry:
    `_ensure_recovery_alarm` (`thestill-aws:1580`), the `put_role_policy`,
    `attach_role_policy`, instance-profile creation and role attachment in `ensure_iam`
    (`thestill-aws:557-617`), rule authorizations on an existing SG in `ensure_app_sg`, and
    the Dalston rule revoke in `cmd_teardown` (`thestill-aws:2047`).
- Idempotent commands:
  - `thestill-aws:cmd_setup` creates no billable resources.
  - `thestill-aws:cmd_setup` derives some secrets: `POSTGRES_PASSWORD` (`token_hex(32)`),
    `PUBLIC_*`, `ALLOWED_ORIGINS`, `ENVIRONMENT`, `COOKIE_SECURE` and
    `REFRESH_SCHEDULER_ENABLED=false`. It writes them with `put_secret(..., overwrite=False)`,
    so existing values are never replaced.
  - `thestill-aws:cmd_launch` does nothing if the instance is already running or pending. If
    the instance is stopped, it starts it rather than launching a new one.
  - Before a new launch, `thestill-aws:cmd_launch` runs `preflight_secrets`.
  - `thestill-aws:cmd_launch` requires IMDSv2 (`HttpTokens=required`) and creates an
    encrypted gp3 root volume.
  - `thestill-aws:ensure_elastic_ip` reuses an existing Elastic IP when it can: first the one
    in state, then an unattached one tagged for the project.
  - `thestill-aws:ensure_bucket` applies these settings only when it creates the bucket; an
    existing bucket is left as it is:
    - block all public access;
    - AES256 encryption;
    - versioning;
    - lifecycle rules: noncurrent versions expire after 30 days and `pg/` objects after 60 days.
- Network and IAM scope:
  - `thestill-aws:ensure_app_sg` opens only tcp/80, tcp/443 and udp/443. There is no port 22,
    8000 or 5432, and shell access goes through SSM Session Manager only.
  - `thestill-aws:ensure_dalston_rule` adds an SG-to-SG ingress rule on the Dalston security
    group. The rule is not based on IP addresses.
  - `thestill-aws:ensure_iam` grants the instance role two inline policies and one managed
    policy:
    - `s3:ListBucket`, plus `s3:GetObject` and `s3:PutObject`; there is no delete;
    - SSM read on `parameter/thestill/prod/*`;
    - `kms:Decrypt` only through SSM;
    - the AWS-managed `AmazonSSMManagedInstanceCore` policy (`thestill-aws:596-599`), which
      also grants `ssm:GetParameter(s)` on `*`, `ssmmessages:*`, `ec2messages:*` and more.
  - `thestill-aws:_github_trust_policy` lets a GitHub workflow assume the role only when
    `aud` is `sts.amazonaws.com` and `sub` is `repo:ssarunic/thestill:environment:<env>`.
  - `thestill-aws:_github_deploy_policy` grants the GitHub deploy role these permissions only:
    - `ec2:DescribeInstances`;
    - `ssm:SendCommand`, limited to instances tagged `thestill:role=app` and the
      `AWS-RunShellScript` document;
    - `ssm:GetCommandInvocation`;
    - get and put on `DEPLOY_STATE_PARAM`.

    It cannot launch or terminate instances, read secrets or touch the bucket.
- Secrets:
  - `thestill-aws:parse_env_file` is strict. It skips lines that are not `KEY=VALUE`, keys in
    lower case, unterminated quotes and empty values. It does not interpolate variables.
  - `thestill-aws:cmd_secrets` (`list`) and `thestill-aws:_secrets_diff` never print secret
    values.
  - `thestill-aws:_secrets_diff` exits 1 if a local variable is missing from SSM. Names in
    `_DIFF_EXPECTED_LOCAL_ONLY` (`STORAGE_PATH`, `DATABASE_PATH`, `DATABASE_URL`, `LOG_FILE`,
    `LOG_FORMAT`, `WHISPER_*`, `OLLAMA_*` and others) are subtracted first and only listed as
    ignored (`thestill-aws:1335`). Values in `_DIFF_EXPECTED_LOCAL_ONLY` or
    `_DIFF_EXPECTED_REMOTE_ONLY` are not reported as differing.
  - `thestill-aws:main` refuses `secrets put` when `--from-env-file` is not given.
- Bootstrap script (`thestill-aws:build_user_data`):
  - It quotes every interpolated value with `shlex.quote` and runs under `set -euo pipefail`.
  - The same script runs at first boot and on `reconcile`.
  - It always writes `redirects.caddy`, even when there are no aliases.
  - It removes old app images, keeping the image that is running and the target tag, then
    runs `docker image prune -f` (`thestill-aws:978`), which removes every dangling image.
  - It retries `caddy reload` up to 5 times and exits 1 if every attempt fails.
  - It writes the bootstrap marker last.
- Commands that check before acting:
  - `thestill-aws:cmd_reconcile` stops with an error unless the instance state is `running`.
    It saves `image_tag` and `kit_ref` only after the remote script succeeds.
  - `thestill-aws:cmd_status` with `--expect-image-tag` fails in these cases: there is no
    instance, the instance is not running, SSM cannot reach it (code 255), the bootstrap is
    incomplete, the image tag does not match, or `/health/ready` does not return 200. The
    deploy workflow uses this mode.
  - `thestill-aws:cmd_state` (`push`) refuses to overwrite a different state in SSM unless
    `--force` is given.
  - `thestill-aws:default_kit_ref` pins the kit to the HEAD SHA only if `origin/main`
    contains it. Otherwise it uses `main`.
- `thestill-aws:cmd_teardown`:
  - Unless `--yes` is given, it asks the user to type `yes`.
  - It never deletes the S3 bucket, the parameters under `SSM_PREFIX` or the Route53 records.
  - The code does not delete the GitHub OIDC role or provider either.
- Instance-side files:
  - `deploy/aws-ec2/docker-compose.prod.yml` publishes ports from `caddy` only. It sets these
    values for `thestill`, overriding `.env`: `STORAGE_PATH=/data`, `DATABASE_URL` (built from
    `POSTGRES_PASSWORD`), `TRANSCRIPTION_PROVIDER=dalston`, `ENVIRONMENT=production`,
    `COOKIE_SECURE=true`, `MIGRATE_ON_STARTUP=true`, `LOG_FORMAT=json` and `TRUSTED_PROXIES`.
  - `deploy/aws-ec2/Caddyfile` (`handle_path /ingest/*`) strips `Cookie`, `Authorization`,
    `Referer` and `X-Forwarded-For` before proxying to PostHog. The app proxy uses
    `flush_interval -1`, which keeps SSE responses unbuffered.
  - `deploy/aws-ec2/fetch-secrets.sh` refuses to write `.env` if any value contains a single
    quote or a newline, and it refuses to write an empty `.env`. The file is written with
    mode 600.
  - `deploy/aws-ec2/backup.sh` stops if `BACKUP_BUCKET` is unset. Its sync skips
    `original_audio/`, `downsampled_audio/`, `logs/`, `debug_feeds/` and `.cache/`.

## Side Effects

- `thestill-aws:audit` writes `~/.thestill/audit.log`. `thestill-aws:save_state` writes
  `~/.thestill/aws-state.json`.
- `thestill-aws:push_state` and `thestill-aws:_load_remote_state` call SSM PutParameter and
  GetParameter on `/thestill/deploy/state`.
- `thestill-aws:put_secret` and `thestill-aws:list_secrets` call SSM PutParameter (SecureString)
  and GetParametersByPath, both under `/thestill/prod/`.
- `thestill-aws:ensure_bucket` calls S3 CreateBucket and S3 Put*Configuration.
- `thestill-aws:ensure_iam` calls IAM CreateRole, PutRolePolicy, AttachRolePolicy,
  CreateInstanceProfile and AddRoleToInstanceProfile.
- `thestill-aws:ensure_app_sg` and `thestill-aws:ensure_dalston_rule` call EC2 CreateSecurityGroup
  and AuthorizeSecurityGroupIngress. This changes the Dalston security group as well.
- `thestill-aws:cmd_launch` calls EC2 RunInstances or StartInstances. It also runs the git
  subprocesses in `thestill-aws:default_kit_ref`.
- `thestill-aws:ensure_elastic_ip` calls EC2 AllocateAddress and AssociateAddress.
- `thestill-aws:_ensure_recovery_alarm` calls CloudWatch PutMetricAlarm to create the EC2
  auto-recover alarm.
- `thestill-aws:upsert_a_record` calls Route53 ChangeResourceRecordSets with an A-record UPSERT
  and TTL 60.
- `thestill-aws:run_remote` calls SSM SendCommand and GetCommandInvocation. Its output can go
  to `<bucket>/ssm-command-output`.
  - `thestill-aws:cmd_reconcile` uses it to run the full bootstrap.
  - `thestill-aws:cmd_status` and `thestill-aws:cmd_logs` use it to read compose state and logs.
- `thestill-aws:ensure_github_oidc_provider` calls IAM CreateOpenIDConnectProvider.
  `thestill-aws:cmd_github_oidc` calls IAM CreateRole, UpdateAssumeRolePolicy and
  PutRolePolicy.
- `thestill-aws:cmd_ssh` runs `aws ssm start-session` as a subprocess.
- `thestill-aws:cmd_teardown` makes these calls:
  - EC2 TerminateInstances, ReleaseAddress, RevokeSecurityGroupIngress and DeleteSecurityGroup;
  - CloudWatch DeleteAlarms;
  - IAM calls that delete the role, the instance profile and their policies.
- `thestill-aws:build_user_data` makes these changes on the instance:
  - `dnf install` of docker and jq;
  - downloads of docker-compose and the deploy kit into `/srv/thestill`;
  - writes `.env` and `redirects.caddy`;
  - `docker compose pull` and `docker compose up -d`;
  - installs the `thestill-backup.{service,timer}` systemd units;
  - logs to `/var/log/user-data.log`.
- `deploy/aws-ec2/backup.sh` streams `pg_dump` through gzip to
  `s3://$BACKUP_BUCKET/pg/<date>.sql.gz` and runs `aws s3 sync ./data s3://$BACKUP_BUCKET/data/`.
- `deploy/aws-ec2/Caddyfile` makes these external connections:
  - it issues TLS certificates through ACME (Let's Encrypt);
  - it proxies to `eu.i.posthog.com` and `eu-assets.i.posthog.com`.

## Known Drift

- A comment in `docker-compose.prod.yml` mentions a "30 GB host disk", but
  `thestill-aws:ROOT_VOLUME_SIZE` is 40.
- The header of `backup.sh` suggests scheduling it with cron. `thestill-aws:build_user_data`
  installs a systemd timer instead.

## Verification

- Run: `./venv/bin/pytest tests/unit/deploy` (the full suite is `make test`).
  `tests/unit/deploy/test_thestill_aws.py` covers user data, idempotency, alias domains, the
  Elastic IP, `parse_env_file`, the secrets diff, the SSM state mirror, GitHub OIDC, status
  verification and the deploy workflow.
- Never run `thestill-aws` subcommands as a test: they change live AWS resources.

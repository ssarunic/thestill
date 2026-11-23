# deploy

## Overview

Deployment assets, grouped by hosting topology. There is one topology today: a single EC2
instance managed by the `thestill-aws` boto3 CLI. The CLI provisions AWS resources and
bootstraps the box, which then runs a docker-compose stack of Caddy, thestill and Postgres.
The app container image is built elsewhere and published to GHCR. A `v*` tag deploys it
through `.github/workflows/deploy.yml`, which runs `thestill-aws reconcile` with an OIDC role
that `thestill-aws:cmd_github_oidc` creates.

## Subdirectories & Boundaries

- aws-ec2: [./aws-ec2/EGM.md](./aws-ec2/EGM.md) (human-maintained notes:
  [./aws-ec2/README.md](./aws-ec2/README.md)) - single-EC2 deployment kit:
  - `thestill-aws` subcommands: `setup`, `secrets`, `launch`, `dns`, `reconcile`, `status`,
    `state`, `github-oidc`, `logs`, `ssh` and `teardown`.
  - Instance-side files: `docker-compose.prod.yml`, `Caddyfile`, `fetch-secrets.sh` and
    `backup.sh`.
  - Two boundaries matter most:
    - deployment state lives in SSM `/thestill/deploy/state`, which wins over the laptop cache;
    - secrets live under SSM `/thestill/prod/`.
  - The kit sends traffic to and from three services outside AWS: Dalston (through an SG-to-SG
    rule), PostHog EU (proxied through Caddy) and GHCR (images).
  - Tests: `./venv/bin/pytest tests/unit/deploy`.

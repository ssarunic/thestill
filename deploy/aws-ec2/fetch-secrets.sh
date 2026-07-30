#!/usr/bin/env bash
# Materialize .env from SSM Parameter Store (spec #66).
#
# Reads every SecureString/String under the given path prefix and writes
# KEY=VALUE lines (the key is the last path segment) to ./.env with 0600
# permissions. Secrets never live in the repo or the image; the instance
# profile must allow ssm:GetParametersByPath (+ kms:Decrypt for the
# default SSM key) on this prefix. Requires: aws cli v2, jq.
#
# Usage:
#   ./fetch-secrets.sh                    # default prefix /thestill/prod/
#   SSM_PREFIX=/thestill/staging/ ./fetch-secrets.sh
set -euo pipefail

SSM_PREFIX="${SSM_PREFIX:-/thestill/prod/}"
ENV_FILE="$(cd "$(dirname "$0")" && pwd)/.env"

command -v jq >/dev/null || { echo "jq is required (dnf install -y jq)" >&2; exit 1; }

tmp="$(mktemp)"
trap 'rm -f "$tmp"' EXIT

aws ssm get-parameters-by-path \
    --path "$SSM_PREFIX" \
    --recursive \
    --with-decryption \
    --output json |
    jq -r '.Parameters[] | "\(.Name | split("/") | last)=\(.Value)"' > "$tmp"

count="$(wc -l < "$tmp" | tr -d ' ')"
if [ "$count" -eq 0 ]; then
    echo "No parameters found under $SSM_PREFIX — refusing to write an empty .env" >&2
    exit 1
fi

install -m 600 "$tmp" "$ENV_FILE"
echo "Wrote $count parameters from $SSM_PREFIX to $ENV_FILE"

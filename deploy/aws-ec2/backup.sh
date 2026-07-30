#!/usr/bin/env bash
# Nightly backup (spec #66): pg_dump to S3 + artifact sync.
#
# - pg_dump | gzip streamed straight to s3://$BACKUP_BUCKET/pg/<date>.sql.gz
# - aws s3 sync ./data (transcripts, summaries, facts, briefings) to
#   s3://$BACKUP_BUCKET/data/, excluding audio scratch and debug output.
#
# The bucket must have versioning on (fat-finger/ransomware protection) and
# a lifecycle rule expiring noncurrent versions (~30 days). Credentials come
# from the instance profile. Schedule from cron, e.g.:
#   0 3 * * * BACKUP_BUCKET=my-thestill-backups /srv/thestill/backup.sh >> /var/log/thestill-backup.log 2>&1
set -euo pipefail

: "${BACKUP_BUCKET:?Set BACKUP_BUCKET to the S3 bucket name}"

DEPLOY_DIR="$(cd "$(dirname "$0")" && pwd)"
STAMP="$(date -u +%F)"

echo "[$(date -u +%FT%TZ)] pg_dump -> s3://$BACKUP_BUCKET/pg/$STAMP.sql.gz"
docker compose -f "$DEPLOY_DIR/docker-compose.prod.yml" exec -T postgres \
    pg_dump -U thestill thestill |
    gzip |
    aws s3 cp - "s3://$BACKUP_BUCKET/pg/$STAMP.sql.gz"

echo "[$(date -u +%FT%TZ)] syncing artifacts -> s3://$BACKUP_BUCKET/data/"
aws s3 sync "$DEPLOY_DIR/data" "s3://$BACKUP_BUCKET/data/" \
    --exclude "original_audio/*" \
    --exclude "downsampled_audio/*" \
    --exclude "logs/*" \
    --exclude "debug_feeds/*" \
    --exclude ".cache/*"

echo "[$(date -u +%FT%TZ)] backup complete"

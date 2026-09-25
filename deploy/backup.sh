#!/usr/bin/env bash
# Nightly Postgres backup to S3. Add to crontab on the server:
#   15 3 * * * /home/ec2-user/invoiceflow/deploy/backup.sh >> /home/ec2-user/backup.log 2>&1
set -euo pipefail
cd "$(dirname "$0")/.."
BUCKET="${BACKUP_BUCKET:?set BACKUP_BUCKET}"
STAMP=$(date -u +%Y%m%d-%H%M%S)
docker compose exec -T db pg_dump -U invoiceflow invoiceflow | gzip > "/tmp/invoiceflow-$STAMP.sql.gz"
aws s3 cp "/tmp/invoiceflow-$STAMP.sql.gz" "s3://$BUCKET/backups/"
rm "/tmp/invoiceflow-$STAMP.sql.gz"
echo "Backed up invoiceflow-$STAMP.sql.gz"

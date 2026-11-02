#!/usr/bin/env bash
# Daily backup of the state that lives only on this box: the LangGraph
# checkpoint SQLite file (runs, review state, run ownership). Invoices and the
# ledger live in Supabase and source PDFs in Supabase Storage - backed up there.
#
# Uses SQLite's online backup API inside the container (a consistent copy
# while the app keeps running - copying the raw file mid-write can corrupt
# it), then ships it to S3 with the instance role. Retention: 7 local copies
# here, 30 days in S3 via the bucket's lifecycle rule (deploy/ec2.md).
set -euo pipefail
# shellcheck disable=SC1091
source /etc/verity/config.env
: "${BACKUP_BUCKET:?set BACKUP_BUCKET in /etc/verity/config.env}"

compose=(docker compose -f /opt/verity/docker-compose.yml --env-file /etc/verity/config.env)
stamp=$(date -u +%Y%m%dT%H%M%SZ)
dest=/var/backups/verity
install -d -m 700 "$dest"

"${compose[@]}" exec -T backend python -c '
import os, sqlite3
src = os.environ.get("CHECKPOINT_DB_PATH", "checkpoints/graph.sqlite")
with sqlite3.connect(src) as a, sqlite3.connect("/tmp/backup.sqlite") as b:
    a.backup(b)
'
"${compose[@]}" cp backend:/tmp/backup.sqlite "$dest/graph-$stamp.sqlite"
"${compose[@]}" exec -T backend rm -f /tmp/backup.sqlite
gzip "$dest/graph-$stamp.sqlite"

aws s3 cp --region "$AWS_REGION" --only-show-errors --sse AES256 \
  "$dest/graph-$stamp.sqlite.gz" "s3://$BACKUP_BUCKET/checkpoints/graph-$stamp.sqlite.gz"

ls -1t "$dest"/graph-*.sqlite.gz | tail -n +8 | xargs -r rm -f
echo "backup: graph-$stamp.sqlite.gz uploaded"

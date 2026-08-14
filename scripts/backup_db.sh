#!/usr/bin/env bash
# Production Readiness & Go-Live Decision Standard §Reliability - "Backups
# and restore test" was a real gap: no backup mechanism existed anywhere
# in this repo for the docker-compose Postgres instance (the local dev
# equivalent of the eventual production database). Uses `docker exec` +
# pg_dump inside the postgres container itself, not a host-installed
# pg_dump binary - works regardless of what's installed on the host.
#
# Usage: ./scripts/backup_db.sh [output_dir]
# Produces output_dir/zoiko_local_<UTC timestamp>.dump (pg_dump custom
# format - restorable with restore_db.sh / pg_restore, not a plain-text
# SQL file). See restore_db.sh for the tested restore half of this pair,
# and docs/runbooks/database-outage.md for when to use this.

set -euo pipefail

CONTAINER="${ZOIKO_DB_CONTAINER:-zoiko_local-postgres-1}"
DB_USER="${ZOIKO_DB_USER:-zoiko}"
DB_NAME="${ZOIKO_DB_NAME:-zoiko_local}"
OUTPUT_DIR="${1:-./backups}"

if ! docker ps --format '{{.Names}}' | grep -qx "$CONTAINER"; then
    echo "ERROR: container '$CONTAINER' is not running (checked via 'docker ps'). Is docker-compose up?" >&2
    exit 1
fi

mkdir -p "$OUTPUT_DIR"
timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
output_file="$OUTPUT_DIR/zoiko_local_${timestamp}.dump"

echo "Backing up '$DB_NAME' from container '$CONTAINER' to $output_file ..."
docker exec "$CONTAINER" pg_dump -U "$DB_USER" -d "$DB_NAME" -F custom > "$output_file"

size=$(wc -c < "$output_file")
if [ "$size" -lt 1000 ]; then
    echo "ERROR: backup file is suspiciously small ($size bytes) - refusing to treat this as a good backup." >&2
    exit 1
fi

echo "Backup complete: $output_file ($size bytes)"

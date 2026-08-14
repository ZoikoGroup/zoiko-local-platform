#!/usr/bin/env bash
# Restore half of the backup_db.sh/restore_db.sh pair - see backup_db.sh
# for why this exists and how the backup file gets produced.
#
# Usage: ./scripts/restore_db.sh <backup_file> <target_db_name>
#
# Deliberately REQUIRES an explicit target_db_name argument, with no
# default - a restore script that defaults to overwriting the live
# database is a real incident waiting to happen. To restore into a fresh
# scratch database for a verification test (recommended before trusting
# any backup): pick a name like zoiko_local_restore_test; this script
# creates it if it doesn't already exist. To actually recover production
# after real data loss, pass the real database name deliberately - this
# script does not try to guess that decision for you.

set -euo pipefail

if [ $# -ne 2 ]; then
    echo "Usage: $0 <backup_file> <target_db_name>" >&2
    echo "Example (safe verification restore): $0 ./backups/zoiko_local_20260813T120000Z.dump zoiko_local_restore_test" >&2
    exit 1
fi

BACKUP_FILE="$1"
TARGET_DB="$2"
CONTAINER="${ZOIKO_DB_CONTAINER:-zoiko_local-postgres-1}"
DB_USER="${ZOIKO_DB_USER:-zoiko}"

if [ ! -f "$BACKUP_FILE" ]; then
    echo "ERROR: backup file '$BACKUP_FILE' does not exist." >&2
    exit 1
fi

if ! docker ps --format '{{.Names}}' | grep -qx "$CONTAINER"; then
    echo "ERROR: container '$CONTAINER' is not running (checked via 'docker ps'). Is docker-compose up?" >&2
    exit 1
fi

echo "Ensuring target database '$TARGET_DB' exists ..."
# --template=template0 (not the createdb default template1) - this
# container's host has a glibc collation version newer than what template1
# was initialized under (confirmed live: "template database template1 has
# a collation version mismatch"), which fails plain createdb outright.
# template0 has no locale-dependent objects and isn't subject to this.
docker exec "$CONTAINER" psql -U "$DB_USER" -d postgres -tc \
    "SELECT 1 FROM pg_database WHERE datname = '$TARGET_DB'" | grep -q 1 || \
    docker exec "$CONTAINER" createdb -U "$DB_USER" --template=template0 "$TARGET_DB"

echo "Restoring $BACKUP_FILE into '$TARGET_DB' ..."
docker exec -i "$CONTAINER" pg_restore -U "$DB_USER" -d "$TARGET_DB" --clean --if-exists --no-owner < "$BACKUP_FILE"

table_count=$(docker exec "$CONTAINER" psql -U "$DB_USER" -d "$TARGET_DB" -tAc \
    "SELECT count(*) FROM information_schema.tables WHERE table_schema = 'public'")
echo "Restore complete: '$TARGET_DB' now has $table_count tables in the public schema."

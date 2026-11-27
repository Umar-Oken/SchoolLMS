#!/usr/bin/env bash
set -euo pipefail

# Restore only to a new synthetic/test database. Never drops or overwrites a DB.
: "${BACKUP_PATH:?Set BACKUP_PATH to one backup directory.}"
: "${RESTORE_TARGET_DB:?Set a new restore_*/test_* target database name.}"
: "${RESTORE_PRIVATE_ROOT:?Set a new empty absolute target directory.}"

if [[ ! "$RESTORE_TARGET_DB" =~ ^(restore|test)_[a-z0-9_]+$ ]]; then
  echo 'Refusing restore: target must start with restore_ or test_.' >&2
  exit 2
fi
if [[ "$RESTORE_PRIVATE_ROOT" != /* ]] || [[ -e "$RESTORE_PRIVATE_ROOT" ]]; then
  echo 'Refusing restore: RESTORE_PRIVATE_ROOT must be a new absolute path.' >&2
  exit 2
fi
for tool in psql createdb pg_restore sha256sum rsync; do
  command -v "$tool" >/dev/null || {
    echo "Missing required tool: $tool" >&2
    exit 127
  }
done
test -f "$BACKUP_PATH/database.dump"
test -f "$BACKUP_PATH/inventory.json"
test -f "$BACKUP_PATH/SHA256SUMS"
(cd "$BACKUP_PATH" && sha256sum --check SHA256SUMS)

if psql --dbname=postgres --tuples-only --no-align \
  --command "SELECT 1 FROM pg_database WHERE datname = '$RESTORE_TARGET_DB'" \
  | grep -qx 1; then
  echo 'Refusing restore: target database already exists.' >&2
  exit 2
fi

createdb "$RESTORE_TARGET_DB"
pg_restore --dbname="$RESTORE_TARGET_DB" --no-owner --no-privileges \
  "$BACKUP_PATH/database.dump"
mkdir -p "$RESTORE_PRIVATE_ROOT"
rsync -a --checksum "$BACKUP_PATH/private-files/" "$RESTORE_PRIVATE_ROOT/"

project_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
python_bin=${PYTHON_BIN:-python}
(cd "$project_root" && DB_NAME="$RESTORE_TARGET_DB" \
  PRIVATE_STORAGE_BACKEND=local PRIVATE_STORAGE_ROOT="$RESTORE_PRIVATE_ROOT" \
  "$python_bin" manage.py verify_restore --inventory "$BACKUP_PATH/inventory.json" \
  --require-records)
echo "Restore drill passed: $RESTORE_TARGET_DB"

#!/usr/bin/env bash
set -euo pipefail

# Run only while writes/uploads are operationally quiesced for a consistent
# PostgreSQL + private-bytes snapshot. Credentials stay in standard PG env vars.
: "${BACKUP_DIR:?Set an absolute backup directory outside the repository.}"
: "${BACKUP_QUIESCED:?Set BACKUP_QUIESCED=true after pausing writes/uploads.}"

if [[ "$BACKUP_QUIESCED" != "true" ]]; then
  echo 'Refusing backup: writes/uploads must be quiesced first.' >&2
  exit 2
fi
if [[ "$BACKUP_DIR" != /* ]]; then
  echo 'Refusing backup: BACKUP_DIR must be absolute.' >&2
  exit 2
fi
for tool in pg_dump sha256sum rsync; do
  command -v "$tool" >/dev/null || {
    echo "Missing required tool: $tool" >&2
    exit 127
  }
done

project_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
python_bin=${PYTHON_BIN:-python}
timestamp=$(date -u +%Y%m%dT%H%M%SZ)
backup_path="$BACKUP_DIR/schoollms-$timestamp"
umask 077
mkdir -p "$backup_path"

pg_dump --format=custom --no-owner --no-privileges \
  --file "$backup_path/database.dump"
(cd "$project_root" && "$python_bin" manage.py backup_inventory \
  --output "$backup_path/inventory.json")

case "${PRIVATE_STORAGE_BACKEND:-local}" in
  local)
    : "${PRIVATE_STORAGE_ROOT:?Set PRIVATE_STORAGE_ROOT for local private bytes.}"
    test -d "$PRIVATE_STORAGE_ROOT" || {
      echo 'Private storage root does not exist.' >&2
      exit 2
    }
    mkdir -p "$backup_path/private-files"
    rsync -a --checksum "$PRIVATE_STORAGE_ROOT/" "$backup_path/private-files/"
    ;;
  s3)
    echo 'S3 bytes need a separately completed provider snapshot; see docs/BACKUP_RESTORE.md.' >&2
    exit 2
    ;;
  *)
    echo 'Unknown PRIVATE_STORAGE_BACKEND.' >&2
    exit 2
    ;;
esac

sha256sum "$backup_path/database.dump" "$backup_path/inventory.json" \
  > "$backup_path/SHA256SUMS"
echo "Backup created: $backup_path"

#!/usr/bin/env bash
#
# Backup data/ to Google Drive via rclone
#
# Backs up: DB, CSV files, reports, ECB rates, crypto prices
# Excludes: backups/, supporting_documents/, __pycache__/, queries/
#
# Usage:
#   ./backup_drive.sh           # full backup
#   ./backup_drive.sh --dry-run # preview only
#
# If token expired, run: rclone config reconnect gdrive:
#
set -e

RCLONE="${RCLONE_PATH:-/opt/homebrew/bin/rclone}"
REMOTE="gdrive:crypto-fifo-tracker/backups"
DATA_DIR="$(cd "$(dirname "$0")" && pwd)/data"

if [ ! -d "$DATA_DIR" ]; then
    echo "✗ Data directory not found: $DATA_DIR"
    exit 1
fi

DRYRUN=""
if [ "$1" = "--dry-run" ]; then
    DRYRUN="--dry-run"
    echo "=== DRY RUN (no changes will be made) ==="
fi

echo "Backing up $DATA_DIR → $REMOTE"
echo "  Date: $(date '+%Y-%m-%d %H:%M')"
echo ""

$RCLONE sync "$DATA_DIR" "$REMOTE" $DRYRUN \
    --filter "- backups/**" \
    --filter "- supporting_documents/**" \
    --filter "- __pycache__/**" \
    --filter "- queries/**" \
    --filter "+ *.db" \
    --filter "+ *.csv" \
    --filter "+ reports/*.xlsx" \
    --filter "- *" \
    --progress \
    --stats-one-line

echo ""
echo "✓ Backup complete"
echo ""

# Show what's on Drive
echo "Remote contents:"
$RCLONE ls "$REMOTE" --max-depth 2 2>/dev/null | head -20

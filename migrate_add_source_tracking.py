"""
Migration: Add source tracking columns to transactions table.

Adds:
  - source       TEXT    — filename of the CSV that originated this record
  - imported_at  TEXT    — ISO timestamp of when the record was imported
  - record_hash  TEXT    — SHA256 hash for dedup and audit

Also creates indexes for fast lookups.

Usage:
    python3 migrate_add_source_tracking.py

Safe to run multiple times — checks for existing columns.
"""

import sqlite3
import os
import sys
import shutil
from datetime import datetime

# Project setup
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, PROJECT_ROOT)

try:
    from config import DATABASE_PATH
except ImportError:
    DATABASE_PATH = os.path.join(PROJECT_ROOT, 'data', 'crypto_fifo.db')

BACKUPS_DIR = os.path.join(PROJECT_ROOT, 'data', 'backups')


def get_existing_columns(conn):
    """Get list of column names in transactions table."""
    cursor = conn.execute("PRAGMA table_info(transactions)")
    return [row[1] for row in cursor.fetchall()]


def migrate(db_path=None):
    if db_path is None:
        db_path = DATABASE_PATH

    if not os.path.exists(db_path):
        print(f"✗ Database not found: {db_path}")
        return False

    # Backup first
    os.makedirs(BACKUPS_DIR, exist_ok=True)
    backup_name = f"crypto_fifo.db.backup_pre_migration_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    backup_path = os.path.join(BACKUPS_DIR, backup_name)
    shutil.copy2(db_path, backup_path)
    print(f"✓ Backup created: {backup_name}")

    conn = sqlite3.connect(db_path)
    try:
        existing = get_existing_columns(conn)
        print(f"  Existing columns: {len(existing)}")

        changes = 0

        # Add source column
        if 'source' not in existing:
            conn.execute("ALTER TABLE transactions ADD COLUMN source TEXT")
            print("  ✓ Added column: source")
            changes += 1
        else:
            print("  · Column already exists: source")

        # Add imported_at column
        if 'imported_at' not in existing:
            conn.execute("ALTER TABLE transactions ADD COLUMN imported_at TEXT")
            print("  ✓ Added column: imported_at")
            changes += 1
        else:
            print("  · Column already exists: imported_at")

        # Add record_hash column
        if 'record_hash' not in existing:
            conn.execute("ALTER TABLE transactions ADD COLUMN record_hash TEXT")
            print("  ✓ Added column: record_hash")
            changes += 1
        else:
            print("  · Column already exists: record_hash")

        # Create indexes (IF NOT EXISTS is safe to repeat)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_transactions_source ON transactions(source)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_transactions_hash ON transactions(record_hash)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_transactions_imported_at ON transactions(imported_at)")
        print("  ✓ Indexes created/verified")

        conn.commit()

        # Show current state
        total = conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
        with_source = conn.execute("SELECT COUNT(*) FROM transactions WHERE source IS NOT NULL").fetchone()[0]
        with_hash = conn.execute("SELECT COUNT(*) FROM transactions WHERE record_hash IS NOT NULL").fetchone()[0]

        print(f"\n  Total records:     {total:,}")
        print(f"  With source:       {with_source:,}")
        print(f"  With record_hash:  {with_hash:,}")

        if total > 0 and with_source < total:
            print(f"\n  ⚠ {total - with_source:,} records have NULL source.")
            print(f"    Run: python3 backfill_source_hash.py")
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    print(f"\n{'✓ Migration complete' if changes > 0 else '· Nothing to migrate'}")
    return True


if __name__ == "__main__":
    print("=" * 60)
    print("  Migration: Add source tracking to transactions")
    print("=" * 60)
    print()
    migrate()

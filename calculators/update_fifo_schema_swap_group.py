#!/usr/bin/env python3
"""Add transactions.swap_group_id — the link between the two legs of one swap.

Idempotent: checks PRAGMA table_info before ALTER, creates the index with
IF NOT EXISTS. Takes the database path as argv[1], defaults to config.

No backup is taken before running: doc/project_guidelines.md's "idempotenti,
con backup" is aimed at the destructive migrations code_guidelines.md §4.4
calls out (DROP, data rewrites); ADD COLUMN is additive and non-destructive,
so there is nothing here a backup would protect against.

Usage: python3 calculators/update_fifo_schema_swap_group.py [db_path]
"""
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import DATABASE_PATH

COLUMN = 'swap_group_id'
INDEX = 'idx_transactions_swap_group'


def migrate(db_path):
    conn = sqlite3.connect(db_path)
    try:
        conn.execute('BEGIN')
        existing = [row[1] for row in conn.execute('PRAGMA table_info(transactions)')]
        if COLUMN in existing:
            print(f"Column '{COLUMN}' already present - nothing to do")
        else:
            conn.execute(f'ALTER TABLE transactions ADD COLUMN {COLUMN} TEXT')
            print(f"Column '{COLUMN}' added")
        conn.execute(f'CREATE INDEX IF NOT EXISTS {INDEX} ON transactions({COLUMN})')
        print(f"Index '{INDEX}' present")
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == '__main__':
    migrate(sys.argv[1] if len(sys.argv) > 1 else DATABASE_PATH)

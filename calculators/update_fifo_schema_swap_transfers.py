#!/usr/bin/env python3
"""Create swap_lot_transfers — lot inheritance across a deferred swap.

One row per lot slice that moves from the asset given up to the asset received:
the acquisition date and cost travel, the gain does not.

Idempotent (CREATE TABLE IF NOT EXISTS). Takes the database path as argv[1].
"""
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import DATABASE_PATH

# One statement per element, executed inside the explicit transaction.
# NOT executescript(): it commits whatever transaction is open before it runs,
# so a BEGIN around it is theatre and the rollback below would save nothing.
STATEMENTS = (
    """CREATE TABLE IF NOT EXISTS swap_lot_transfers (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    swap_group_id        TEXT NOT NULL,
    from_cryptocurrency  TEXT NOT NULL,
    to_cryptocurrency    TEXT NOT NULL,
    swap_date            TEXT NOT NULL,
    source_purchase_date TEXT NOT NULL,
    amount_from          REAL NOT NULL,
    cost_basis           REAL NOT NULL,
    created_at           TEXT NOT NULL DEFAULT (datetime('now'))
)""",
    """CREATE INDEX IF NOT EXISTS idx_swap_lot_transfers_group
    ON swap_lot_transfers(swap_group_id)""",
    """CREATE INDEX IF NOT EXISTS idx_swap_lot_transfers_to
    ON swap_lot_transfers(to_cryptocurrency)""",
)


def migrate(db_path):
    conn = sqlite3.connect(db_path)
    try:
        conn.execute('BEGIN')
        for statement in STATEMENTS:
            conn.execute(statement)
        conn.commit()
        print('✓ swap_lot_transfers present')
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == '__main__':
    migrate(sys.argv[1] if len(sys.argv) > 1 else DATABASE_PATH)

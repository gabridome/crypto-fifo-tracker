#!/usr/bin/env python3
"""Create swap_lot_transfers — lot inheritance across a deferred swap.

One row per lot slice that moves from the asset given up to the asset received:
the acquisition date and cost travel, the gain does not.

Idempotent: CREATE TABLE/INDEX IF NOT EXISTS throughout. Takes the database
path as argv[1], defaults to config.

No backup is taken before running: doc/project_guidelines.md's "idempotenti,
con backup" is aimed at the destructive migrations code_guidelines.md §4.4
calls out (DROP, data rewrites); CREATE TABLE/INDEX IF NOT EXISTS is additive
and non-destructive, so there is nothing here a backup would protect against.

This DDL must stay identical to the swap_lot_transfers block in
doc/schema.sql (tests/test_swap_deferral.py asserts the two agree) — a fresh
install and tests/conftest.py's db_path fixture build from schema.sql only,
never from this script, so a column added or renamed in just one of the two
copies would otherwise drift silently.

Usage: python3 calculators/update_fifo_schema_swap_transfers.py [db_path]
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
    swap_group_id        TEXT NOT NULL CHECK (swap_group_id <> ''),
    source_fifo_lot_id   INTEGER NOT NULL,
    from_cryptocurrency  TEXT NOT NULL CHECK (from_cryptocurrency <> ''),
    to_cryptocurrency    TEXT NOT NULL CHECK (to_cryptocurrency <> ''),
    swap_date            TEXT NOT NULL,
    source_purchase_date TEXT NOT NULL,
    amount_from          REAL NOT NULL CHECK (amount_from > 0),
    cost_basis           REAL NOT NULL CHECK (cost_basis >= 0),
    -- TEXT + explicit default, not TIMESTAMP DEFAULT CURRENT_TIMESTAMP like
    -- the rest of doc/schema.sql: TIMESTAMP has NUMERIC affinity in SQLite,
    -- so a stored ISO datetime string would be subject to silent coercion.
    -- TEXT says what it actually is. Deliberate — do not "uniform" it back.
    created_at           TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (source_fifo_lot_id) REFERENCES fifo_lots(id)
)""",
    """CREATE INDEX IF NOT EXISTS idx_swap_lot_transfers_group
    ON swap_lot_transfers(swap_group_id)""",
    # Targets from_cryptocurrency, not to_cryptocurrency: the DELETE in
    # task 9 filters on the give side on every engine pass; the take side
    # is already covered by the group index above.
    """CREATE INDEX IF NOT EXISTS idx_swap_lot_transfers_from
    ON swap_lot_transfers(from_cryptocurrency)""",
    # One lot moves into one swap group's inheritance at most once (one swap
    # group is exactly one SELL, and FIFO consumption for one SELL visits each
    # lot once). This does NOT make a re-run safe: the engine reinserts the
    # lots on every pass, so the same lot returns with a new id and the pair
    # differs. Only the DELETE in calculate_fifo_lots prevents a second pass
    # from doubling the inherited cost basis.
    """CREATE UNIQUE INDEX IF NOT EXISTS idx_swap_lot_transfers_unique_group_lot
    ON swap_lot_transfers(swap_group_id, source_fifo_lot_id)""",
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

"""Deferral path: a swap that does not realize, and the inheritance it creates."""
import os
import re
import sqlite3
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

PROJECT_ROOT = os.path.join(os.path.dirname(__file__), '..')


def test_migration_creates_swap_lot_transfers(tmp_path):
    """Exercise the real transition: a database that does not have the table yet.

    Building the fixture from doc/schema.sql would be a false positive — the
    schema file carries the table, so CREATE TABLE IF NOT EXISTS would be a
    no-op and the migration's own SQL would never run. Task 1 shipped exactly
    that mistake; do not repeat it.
    """
    script = os.path.join(PROJECT_ROOT, 'calculators', 'update_fifo_schema_swap_transfers.py')
    schema = open(os.path.join(PROJECT_ROOT, 'doc', 'schema.sql')).read()

    # Pre-migration schema: drop the swap_lot_transfers DDL block.
    before = re.sub(r'CREATE (TABLE|INDEX)[^;]*swap_lot_transfers[^;]*;', '', schema,
                    flags=re.IGNORECASE | re.DOTALL)
    assert 'swap_lot_transfers' not in before, 'the strip pattern no longer matches the schema'

    db = str(tmp_path / 'pre_migration.db')
    conn = sqlite3.connect(db)
    try:
        conn.executescript(before)
        conn.commit()
        assert conn.execute("SELECT name FROM sqlite_master WHERE name='swap_lot_transfers'").fetchone() is None
    finally:
        conn.close()

    for _ in range(2):  # first run creates, second must be a safe no-op
        r = subprocess.run([sys.executable, script, db], capture_output=True, text=True)
        assert r.returncode == 0, r.stderr

    conn = sqlite3.connect(db)
    try:
        cols = [row[1] for row in conn.execute('PRAGMA table_info(swap_lot_transfers)')]
        idx = [row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='swap_lot_transfers'")]
        indexed = [row[2] for row in conn.execute('PRAGMA index_info(idx_swap_lot_transfers_group)')]
    finally:
        conn.close()

    assert cols == ['id', 'swap_group_id', 'from_cryptocurrency', 'to_cryptocurrency',
                    'swap_date', 'source_purchase_date', 'amount_from', 'cost_basis',
                    'created_at']
    assert 'idx_swap_lot_transfers_group' in idx
    assert indexed == ['swap_group_id'], 'the index must target the column it is named after'

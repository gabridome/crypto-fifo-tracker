"""Non-BTC legs importer: schema, idempotency, and the BTC ledger it must not touch."""
import os
import sqlite3
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

PROJECT_ROOT = os.path.join(os.path.dirname(__file__), '..')


def _columns(db_path, table):
    conn = sqlite3.connect(db_path)
    try:
        return [r[1] for r in conn.execute(f'PRAGMA table_info({table})')]
    finally:
        conn.close()


def test_migration_adds_swap_group_id(db_path):
    """The migration adds the column and its index, and is safe to re-run."""
    script = os.path.join(PROJECT_ROOT, 'calculators', 'update_fifo_schema_swap_group.py')

    for _ in range(2):  # idempotent: second run must not fail
        r = subprocess.run([sys.executable, script, db_path], capture_output=True, text=True)
        assert r.returncode == 0, r.stderr

    assert 'swap_group_id' in _columns(db_path, 'transactions')

    conn = sqlite3.connect(db_path)
    try:
        idx = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='transactions'")]
    finally:
        conn.close()
    assert 'idx_transactions_swap_group' in idx

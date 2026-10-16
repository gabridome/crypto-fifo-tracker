"""Non-BTC legs importer: schema, idempotency, and the BTC ledger it must not touch.

The importer and the BTC-ledger non-regression cases this docstring promises
land with later tasks of the non-btc-legs plan; this file currently only
covers the swap_group_id migration (task 1).
"""
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

PROJECT_ROOT = os.path.join(os.path.dirname(__file__), '..')
SCHEMA_FILE = os.path.join(PROJECT_ROOT, 'doc', 'schema.sql')

# The two statements the swap_group_id migration is responsible for adding.
# Stripping them out of the real doc/schema.sql gives a pre-migration schema,
# so the test can exercise the actual ALTER TABLE / CREATE INDEX statements
# instead of starting from a DB that already has the column (which is what
# the shared `db_path` fixture gives us now, since schema.sql carries the
# column — see tests/conftest.py).
_COLUMN_BLOCK = re.compile(
    r"    record_hash TEXT,(?P<comment>[^\n]*)\n"
    r"    swap_group_id TEXT[^\n]*\n"
)
_INDEX_LINE = re.compile(
    r"CREATE INDEX IF NOT EXISTS idx_transactions_swap_group "
    r"ON transactions\(swap_group_id\);\n"
)


def _pre_migration_schema():
    """doc/schema.sql with the swap_group_id column and index stripped out.

    Fails loudly if either pattern no longer matches the real schema file
    (e.g. after a reformat), instead of silently handing back a schema that
    still has the column — which would quietly turn this test back into the
    false positive it exists to replace.
    """
    with open(SCHEMA_FILE) as f:
        schema = f.read()

    schema, n = _COLUMN_BLOCK.subn(r"    record_hash TEXT\g<comment>\n", schema)
    assert n == 1, (
        "Could not find the swap_group_id column line in doc/schema.sql — "
        "update _COLUMN_BLOCK to match the current schema; otherwise this "
        "test silently stops exercising the ALTER TABLE path."
    )

    schema, n = _INDEX_LINE.subn('', schema)
    assert n == 1, (
        "Could not find the idx_transactions_swap_group index line in "
        "doc/schema.sql — update _INDEX_LINE to match the current schema; "
        "otherwise this test silently stops exercising the CREATE INDEX path."
    )

    return schema


def _columns(db_path, table):
    conn = sqlite3.connect(db_path)
    try:
        return [r[1] for r in conn.execute(f'PRAGMA table_info({table})')]
    finally:
        conn.close()


def _index_names(db_path, table):
    conn = sqlite3.connect(db_path)
    try:
        return [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name=?", (table,))]
    finally:
        conn.close()


def _index_columns(db_path, index_name):
    """Column names covered by an index, via PRAGMA index_info — not just its name."""
    conn = sqlite3.connect(db_path)
    try:
        return [r[2] for r in conn.execute(f'PRAGMA index_info({index_name})')]
    finally:
        conn.close()


@pytest.fixture
def pre_migration_db():
    """A temp DB built from doc/schema.sql with swap_group_id stripped out.

    Local to this test module on purpose: the shared `db_path` fixture in
    conftest.py builds from the real, current schema.sql (which already has
    swap_group_id) and other tests rely on that as-is — it must not change.
    """
    tmp_dir = tempfile.mkdtemp()
    path = os.path.join(tmp_dir, 'pre_migration.db')

    conn = sqlite3.connect(path)
    try:
        conn.executescript(_pre_migration_schema())
        conn.commit()
    finally:
        conn.close()

    yield path

    shutil.rmtree(tmp_dir)


def test_migration_adds_swap_group_id(pre_migration_db):
    """The migration adds the column and its index on a real pre-migration DB, and is safe to re-run."""
    script = os.path.join(PROJECT_ROOT, 'calculators', 'update_fifo_schema_swap_group.py')

    # Before: confirm the pre-migration DB really lacks both the column and the index.
    assert 'swap_group_id' not in _columns(pre_migration_db, 'transactions')
    assert 'idx_transactions_swap_group' not in _index_names(pre_migration_db, 'transactions')

    # First run: exercises the real ALTER TABLE / CREATE INDEX statements.
    r = subprocess.run([sys.executable, script, pre_migration_db], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr

    assert 'swap_group_id' in _columns(pre_migration_db, 'transactions')
    assert 'idx_transactions_swap_group' in _index_names(pre_migration_db, 'transactions')
    assert _index_columns(pre_migration_db, 'idx_transactions_swap_group') == ['swap_group_id']

    # Second run: column and index already exist — exercises the no-op (idempotent) branch.
    r = subprocess.run([sys.executable, script, pre_migration_db], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr

    assert 'swap_group_id' in _columns(pre_migration_db, 'transactions')
    assert 'idx_transactions_swap_group' in _index_names(pre_migration_db, 'transactions')
    assert _index_columns(pre_migration_db, 'idx_transactions_swap_group') == ['swap_group_id']

"""Deferral path: a swap that does not realize, and the inheritance it creates."""
import os
import re
import sqlite3
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from conftest import _columns, _index_columns, _index_names  # noqa: E402

PROJECT_ROOT = os.path.join(os.path.dirname(__file__), '..')
SCHEMA_FILE = os.path.join(PROJECT_ROOT, 'doc', 'schema.sql')
SCRIPT = os.path.join(PROJECT_ROOT, 'calculators', 'update_fifo_schema_swap_transfers.py')
TABLE = 'swap_lot_transfers'

EXPECTED_COLUMNS = [
    'id', 'swap_group_id', 'source_fifo_lot_id', 'from_cryptocurrency',
    'to_cryptocurrency', 'swap_date', 'source_purchase_date', 'amount_from',
    'cost_basis', 'created_at',
]


def _pre_migration_db(db_path):
    """A DB built from the real doc/schema.sql, then with swap_lot_transfers dropped.

    Built from the whole schema, not a regex strip of it — a strip pattern
    can be fooled by a comment that happens to contain the table name inside
    another statement, before that statement's own ';'. Dropping the table
    after a full, real install instead means every OTHER table in the
    fixture is exactly what a fresh install or tests/conftest.py's db_path
    fixture would give it, and only swap_lot_transfers itself is missing —
    which is the one thing this fixture needs to be true.

    No IF EXISTS on the DROP: if doc/schema.sql ever stops carrying the
    table, this raises `no such table` loudly, instead of silently handing
    back a schema that lacks the table for the wrong reason (which is
    exactly the false positive this fixture exists to avoid — see task 1).
    """
    conn = sqlite3.connect(db_path)
    try:
        with open(SCHEMA_FILE) as f:
            conn.executescript(f.read())
        conn.execute(f'DROP TABLE {TABLE}')  # indexes on it are dropped with it
        conn.commit()
        assert conn.execute(
            "SELECT name FROM sqlite_master WHERE name=?", (TABLE,)
        ).fetchone() is None
        # the drop must not have touched any other table
        for table in ('transactions', 'fifo_lots', 'sale_lot_matches'):
            assert conn.execute(
                "SELECT name FROM sqlite_master WHERE name=?", (table,)
            ).fetchone() is not None, f'{table} should survive dropping {TABLE}'
    finally:
        conn.close()
    return db_path


def _run_migration(db_path):
    r = subprocess.run([sys.executable, SCRIPT, db_path], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return r


def _valid_row(**overrides):
    row = {
        'swap_group_id': 'swap-test-0001',
        'source_fifo_lot_id': 1,
        'from_cryptocurrency': 'ETH',
        'to_cryptocurrency': 'USDT',
        'swap_date': '2024-01-01',
        'source_purchase_date': '2023-06-01',
        'amount_from': 1.5,
        'cost_basis': 100.0,
    }
    row.update(overrides)
    return row


_INSERT_SQL = """INSERT INTO swap_lot_transfers
    (swap_group_id, source_fifo_lot_id, from_cryptocurrency, to_cryptocurrency,
     swap_date, source_purchase_date, amount_from, cost_basis)
    VALUES (?,?,?,?,?,?,?,?)"""


def _params(row):
    return (row['swap_group_id'], row['source_fifo_lot_id'], row['from_cryptocurrency'],
             row['to_cryptocurrency'], row['swap_date'], row['source_purchase_date'],
             row['amount_from'], row['cost_basis'])


def test_migration_creates_swap_lot_transfers(tmp_path):
    """Exercise the real transition: a database that does not have the table yet."""
    db = _pre_migration_db(str(tmp_path / 'pre_migration.db'))

    for _ in range(2):  # first run creates, second must be a safe no-op
        _run_migration(db)

    assert _columns(db, TABLE) == EXPECTED_COLUMNS

    idx = _index_names(db, TABLE)
    assert 'idx_swap_lot_transfers_group' in idx
    assert 'idx_swap_lot_transfers_from' in idx
    assert 'idx_swap_lot_transfers_unique_group_lot' in idx
    assert _index_columns(db, 'idx_swap_lot_transfers_group') == ['swap_group_id'], (
        'the group index must target the column it is named after'
    )
    assert _index_columns(db, 'idx_swap_lot_transfers_from') == ['from_cryptocurrency'], (
        'the from index must target the column the DELETE in task 9 filters on'
    )
    assert _index_columns(db, 'idx_swap_lot_transfers_unique_group_lot') == [
        'swap_group_id', 'source_fifo_lot_id',
    ], 'the unique index must cover both columns of the idempotency invariant'


def test_all_columns_except_id_are_not_null(tmp_path):
    """Every column but the surrogate key must be NOT NULL.

    Catches dropping NOT NULL from any column (e.g. swap_group_id) without
    naming that column specifically — the next column added to this table
    is covered by the same loop, for free.
    """
    db = _pre_migration_db(str(tmp_path / 'notnull.db'))
    _run_migration(db)
    conn = sqlite3.connect(db)
    try:
        info = conn.execute(f'PRAGMA table_info({TABLE})').fetchall()
    finally:
        conn.close()
    assert info, 'table_info returned nothing — did the migration run?'
    for _cid, name, _coltype, notnull, _dflt, _pk in info:
        if name == 'id':
            continue
        assert notnull == 1, f'{name} should be NOT NULL'


INDEX_NAMES = (
    'idx_swap_lot_transfers_group',
    'idx_swap_lot_transfers_from',
    'idx_swap_lot_transfers_unique_group_lot',
)


def _normalize_sql(sql):
    """Strip SQL comments and collapse whitespace for a structural compare.

    sqlite_master.sql stores the CREATE statement verbatim, comments
    included, so two DDL copies that differ only in how a comment happens
    to wrap (a real but harmless difference — see the created_at comment in
    doc/schema.sql vs. the migration script) would otherwise compare
    unequal for the wrong reason.
    """
    without_comments = re.sub(r'--[^\n]*', '', sql)
    return re.sub(r'\s+', ' ', without_comments).strip()


def _table_and_index_sql(db_path):
    """The verbatim CREATE statements sqlite_master stored for the table and its indexes."""
    conn = sqlite3.connect(db_path)
    try:
        table_sql = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (TABLE,)
        ).fetchone()[0]
        index_sql = {
            name: conn.execute(
                "SELECT sql FROM sqlite_master WHERE type='index' AND name=?", (name,)
            ).fetchone()[0]
            for name in INDEX_NAMES
        }
    finally:
        conn.close()
    return table_sql, index_sql


def test_schema_sql_and_migration_agree_on_ddl(tmp_path):
    """The two copies of the DDL (doc/schema.sql and the migration script)
    must describe the exact same table and indexes — CHECKs and foreign
    keys included, not just the columns PRAGMA table_info reports.

    tests/conftest.py's db_path fixture and a fresh install build from
    doc/schema.sql; the migration script never runs for them. A CHECK or a
    FOREIGN KEY present in one copy and missing from the other would pass
    a column-only comparison silently — and a CHECK cannot be re-added
    later with an ALTER TABLE, so the installation that ends up missing it
    (a fresh install or the web app) stays unprotected until the table is
    rebuilt. Comparing the two copies' `sqlite_master.sql` directly (after
    stripping comments) catches that; comparing each against a third,
    independently hand-written DDL string would not, for the same reason a
    hand-written column list didn't.
    """
    fresh_db = str(tmp_path / 'fresh_install.db')
    conn = sqlite3.connect(fresh_db)
    try:
        with open(SCHEMA_FILE) as f:
            conn.executescript(f.read())
        conn.commit()
    finally:
        conn.close()
    schema_table_sql, schema_index_sql = _table_and_index_sql(fresh_db)

    migrated_db = _pre_migration_db(str(tmp_path / 'migrated.db'))
    _run_migration(migrated_db)
    migration_table_sql, migration_index_sql = _table_and_index_sql(migrated_db)

    assert _normalize_sql(schema_table_sql) == _normalize_sql(migration_table_sql), (
        'doc/schema.sql and update_fifo_schema_swap_transfers.py have drifted on the '
        'swap_lot_transfers CREATE TABLE statement (columns, CHECKs or foreign keys)'
    )
    for name in INDEX_NAMES:
        assert _normalize_sql(schema_index_sql[name]) == _normalize_sql(migration_index_sql[name]), (
            f'doc/schema.sql and update_fifo_schema_swap_transfers.py have drifted on {name}'
        )


@pytest.mark.parametrize('overrides', [
    {'swap_group_id': ''},
    {'from_cryptocurrency': ''},
    {'to_cryptocurrency': ''},
    {'amount_from': 0},
    {'amount_from': -1},
    {'cost_basis': -0.01},
], ids=[
    'empty-swap-group-id', 'empty-from-crypto', 'empty-to-crypto',
    'zero-amount', 'negative-amount', 'negative-cost-basis',
])
def test_check_constraints_reject_invalid_rows(tmp_path, overrides):
    """The empty string satisfies NOT NULL — it takes a CHECK to stop it.

    This is the regression task 9 would otherwise ship: a SELL whose
    matching BUY leg never imported gets swap_takes.get(group, '') ==
    ''. Without a CHECK on to_cryptocurrency, that row inserts silently,
    the lot it consumed is gone, and no gain is ever realized for it.
    """
    db = str(tmp_path / 'checks.db')
    conn = sqlite3.connect(db)
    try:
        with open(SCHEMA_FILE) as f:
            conn.executescript(f.read())
        conn.commit()
        row = _valid_row(**overrides)
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(_INSERT_SQL, _params(row))
    finally:
        conn.close()


def test_check_constraints_accept_a_legitimate_zero_cost_lot(tmp_path):
    """cost_basis = 0 must be accepted: a forked coin was never bought."""
    db = str(tmp_path / 'checks_valid.db')
    conn = sqlite3.connect(db)
    try:
        with open(SCHEMA_FILE) as f:
            conn.executescript(f.read())
        conn.commit()
        row = _valid_row(cost_basis=0.0)
        conn.execute(_INSERT_SQL, _params(row))
        conn.commit()
        stored = conn.execute(
            f'SELECT cost_basis FROM {TABLE} WHERE swap_group_id = ?', (row['swap_group_id'],)
        ).fetchone()
    finally:
        conn.close()
    assert stored == (0.0,)


def test_unique_index_rejects_the_same_lot_twice_in_one_swap_group(tmp_path):
    """One lot can be drawn into one swap group's inheritance at most once.

    A swap group is exactly one SELL (the two legs come from one normalized
    row — see importers/non_btc_valuation.py:_swap_id); FIFO consumption for
    that one SELL visits each lot once. If a future bug re-ran that
    consumption twice for the same group, this is the constraint that
    turns it into an immediate, loud failure instead of a silently doubled
    cost basis.
    """
    db = str(tmp_path / 'unique.db')
    conn = sqlite3.connect(db)
    try:
        with open(SCHEMA_FILE) as f:
            conn.executescript(f.read())
        conn.commit()
        row = _valid_row()
        conn.execute(_INSERT_SQL, _params(row))
        conn.commit()
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(_INSERT_SQL, _params(row))
    finally:
        conn.close()


def test_unique_index_allows_two_lots_in_the_same_swap_group(tmp_path):
    """A SELL big enough to drain more than one lot must still insert both rows."""
    db = str(tmp_path / 'unique_two_lots.db')
    conn = sqlite3.connect(db)
    try:
        with open(SCHEMA_FILE) as f:
            conn.executescript(f.read())
        conn.commit()
        conn.execute(_INSERT_SQL, _params(_valid_row(source_fifo_lot_id=1)))
        conn.execute(_INSERT_SQL, _params(_valid_row(source_fifo_lot_id=2)))
        conn.commit()
        count = conn.execute(
            f"SELECT COUNT(*) FROM {TABLE} WHERE swap_group_id = 'swap-test-0001'"
        ).fetchone()[0]
    finally:
        conn.close()
    assert count == 2

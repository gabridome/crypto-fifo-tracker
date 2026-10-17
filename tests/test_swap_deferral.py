"""Deferral path: a swap that does not realize, and the inheritance it creates."""
import os
import re
import sqlite3
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from tests.schema_helpers import _columns, _index_columns, _index_names  # noqa: E402

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


INDEX_NAMES = (
    'idx_swap_lot_transfers_group',
    'idx_swap_lot_transfers_from',
    'idx_swap_lot_transfers_unique_group_lot',
)


def test_migration_creates_swap_lot_transfers(tmp_path):
    """Exercise the real transition: a database that does not have the table yet."""
    db = _pre_migration_db(str(tmp_path / 'pre_migration.db'))

    for _ in range(2):  # first run creates, second must be a safe no-op
        _run_migration(db)

    assert _columns(db, TABLE) == EXPECTED_COLUMNS

    idx = _index_names(db, TABLE)
    for name in INDEX_NAMES:
        assert name in idx, f'migration did not create index {name}'
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
    """The verbatim CREATE statements sqlite_master stored for the table and
    *every* index it has — not a fixed list of expected names. An index
    that exists in only one of the two copies is itself a drift (a
    constraint one database enforces and the other doesn't), and a fixed
    name list would walk straight past it instead of catching it.
    """
    conn = sqlite3.connect(db_path)
    try:
        table_sql = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (TABLE,)
        ).fetchone()[0]
        index_sql = dict(conn.execute(
            "SELECT name, sql FROM sqlite_master WHERE type='index' AND tbl_name=?", (TABLE,)
        ).fetchall())
    finally:
        conn.close()
    return table_sql, index_sql


def test_schema_sql_and_migration_agree_on_ddl(tmp_path):
    """The two copies of the DDL (doc/schema.sql and the migration script)
    must describe the exact same table and the exact same set of indexes
    — CHECKs, foreign keys and uniqueness included, not just the columns
    PRAGMA table_info reports.

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

    # This comparison is exact after stripping comments and collapsing
    # whitespace, so a difference of pure formatting (e.g.
    # "CHECK (amount_from > 0)" vs. "CHECK (amount_from>0)") also trips it
    # — deliberately: better a false alarm from a cosmetic rewrite than a
    # missed CHECK or foreign key.
    assert _normalize_sql(schema_table_sql) == _normalize_sql(migration_table_sql), (
        'doc/schema.sql and update_fifo_schema_swap_transfers.py have drifted on the '
        'swap_lot_transfers CREATE TABLE statement — a column, a CHECK, a foreign key, '
        'or just formatting'
    )

    schema_names = set(schema_index_sql)
    migration_names = set(migration_index_sql)
    assert schema_names == migration_names, (
        'doc/schema.sql and update_fifo_schema_swap_transfers.py disagree on which '
        f'indexes exist for {TABLE}: only in doc/schema.sql: '
        f'{sorted(schema_names - migration_names)}; only in the migration: '
        f'{sorted(migration_names - schema_names)}'
    )
    for name in sorted(schema_names):
        schema_sql = schema_index_sql.get(name)
        migration_sql = migration_index_sql.get(name)
        assert schema_sql is not None, f'{name} missing from doc/schema.sql'
        assert migration_sql is not None, (
            f'{name} missing from update_fifo_schema_swap_transfers.py'
        )
        assert _normalize_sql(schema_sql) == _normalize_sql(migration_sql), (
            f'doc/schema.sql and update_fifo_schema_swap_transfers.py have drifted on '
            f'{name} — columns, uniqueness, or just formatting'
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


# ---- the engine: the give side of a deferred swap (task 9) ----

from calculators.crypto_fifo_tracker import CryptoFIFOTracker  # noqa: E402


def _tx(conn, **kw):
    """Insert one transaction; returns its id."""
    cols = ('transaction_date', 'transaction_type', 'cryptocurrency', 'amount',
            'price_per_unit', 'total_value', 'fee_amount', 'fee_currency', 'currency',
            'exchange_name', 'source', 'imported_at', 'record_hash', 'swap_group_id')
    row = {c: kw.get(c) for c in cols}
    row.setdefault('fee_amount', 0)
    cur = conn.execute(
        f"INSERT INTO transactions ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
        tuple(row[c] for c in cols))
    conn.commit()
    return cur.lastrowid


def _seed_swap(db_path):
    """Buy 1 ETH for EUR 1000 in 2020, swap it for 2000 USDT in 2024."""
    conn = sqlite3.connect(db_path)
    try:
        _tx(conn, transaction_date='2020-01-10T10:00:00+00:00', transaction_type='BUY',
            cryptocurrency='ETH', amount=1.0, price_per_unit=1000.0, total_value=1000.0,
            fee_currency='EUR', currency='EUR', exchange_name='Test',
            source='seed', imported_at='2026-01-01T00:00:00', record_hash='h1')
        _tx(conn, transaction_date='2024-04-22T15:00:00+00:00', transaction_type='SELL',
            cryptocurrency='ETH', amount=1.0, price_per_unit=3000.0, total_value=3000.0,
            fee_currency='EUR', currency='EUR', exchange_name='Test',
            source='seed', imported_at='2026-01-01T00:00:00', record_hash='h2',
            swap_group_id='swap-1')
        _tx(conn, transaction_date='2024-04-22T15:00:00+00:00', transaction_type='BUY',
            cryptocurrency='USDT', amount=2000.0, price_per_unit=1.5, total_value=3000.0,
            fee_currency='EUR', currency='EUR', exchange_name='Test',
            source='seed', imported_at='2026-01-01T00:00:00', record_hash='h3',
            swap_group_id='swap-1')
    finally:
        conn.close()


def test_deferred_swap_realizes_nothing_and_records_the_transfer(db_path, monkeypatch):
    _seed_swap(db_path)
    monkeypatch.setattr('calculators.crypto_fifo_tracker.SWAP_REALIZES', False)

    tracker = CryptoFIFOTracker(db_path)
    try:
        tracker.calculate_fifo_lots('ETH')
    finally:
        tracker.close()

    conn = sqlite3.connect(db_path)
    try:
        matches = conn.execute("SELECT COUNT(*) FROM sale_lot_matches WHERE cryptocurrency='ETH'").fetchone()[0]
        transfers = conn.execute("""SELECT swap_group_id, from_cryptocurrency, to_cryptocurrency,
                                           source_purchase_date, amount_from, cost_basis
                                    FROM swap_lot_transfers""").fetchall()
        # The JOIN is the assertion: source_fifo_lot_id must resolve to the lot
        # actually consumed. Two buys on the same day make two lots with the
        # same purchase_date, so a date alone does not identify one.
        inherited = conn.execute("""SELECT l.cryptocurrency, l.purchase_date
                                   FROM swap_lot_transfers t
                                   JOIN fifo_lots l ON l.id = t.source_fifo_lot_id""").fetchall()
        remaining = conn.execute("SELECT ROUND(SUM(remaining_amount),8) FROM fifo_lots "
                                 "WHERE cryptocurrency='ETH'").fetchone()[0]
    finally:
        conn.close()

    assert matches == 0, 'a deferred swap must not produce a realized match'
    assert transfers == [('swap-1', 'ETH', 'USDT', '2020-01-10T10:00:00+00:00', 1.0, 1000.0)]
    assert inherited == [('ETH', '2020-01-10T10:00:00+00:00')], \
        'the transfer must point at the lot it consumed, not merely carry its date'
    assert remaining == 0.0, 'the lots of the asset given up are consumed'


def test_deferred_swap_without_its_buy_leg_refuses_to_lose_the_cost_basis(db_path, monkeypatch):
    """The one way this branch could destroy a number silently.

    A SELL carrying a swap_group_id whose BUY leg was never imported: the lots
    are consumed, and under deferral there is no realized match either. Before
    this guard the row was written with an empty to_cryptocurrency, which the
    take side never queries — cost basis out of the chain, no error, wrong tax.
    """
    _seed_swap(db_path)
    conn = sqlite3.connect(db_path)
    try:  # remove the BUY leg of the swap, keep the disposal
        conn.execute("DELETE FROM transactions WHERE cryptocurrency='USDT'")
        conn.commit()
    finally:
        conn.close()
    monkeypatch.setattr('calculators.crypto_fifo_tracker.SWAP_REALIZES', False)

    tracker = CryptoFIFOTracker(db_path)
    try:
        with pytest.raises(ValueError, match='swap-1'):
            tracker.calculate_fifo_lots('ETH')
    finally:
        tracker.close()


def test_deferral_on_a_database_without_the_transfers_table_stops_and_names_the_migration(
        tmp_path, monkeypatch):
    """Deferral needs the table, and the engine does not create it.

    A fourth copy of that DDL inside the engine would be a fourth thing that
    can drift, and this table's CHECKs cannot be added by a later ALTER.
    So the engine requires the table and names the migration that installs it.
    """
    db = _pre_migration_db(str(tmp_path / 'no_transfers_table.db'))
    _seed_swap(db)
    monkeypatch.setattr('calculators.crypto_fifo_tracker.SWAP_REALIZES', False)

    tracker = CryptoFIFOTracker(db)
    try:
        with pytest.raises(ValueError, match='update_fifo_schema_swap_transfers'):
            tracker.calculate_fifo_lots('ETH')
    finally:
        tracker.close()


def test_realizing_swap_on_a_database_without_the_transfers_table_is_not_stopped(tmp_path):
    """The default path must not require the table it never writes.

    The engine self-creates fifo_lots and sale_lot_matches precisely because
    a database predating them must keep working; swap_lot_transfers is not
    self-created, so every statement touching it has to be reached only when
    deferral is in use.
    """
    db = _pre_migration_db(str(tmp_path / 'realizing_no_table.db'))
    _seed_swap(db)

    tracker = CryptoFIFOTracker(db)
    try:
        tracker.calculate_fifo_lots('ETH')
    finally:
        tracker.close()

    conn = sqlite3.connect(db)
    try:
        gain = conn.execute("SELECT ROUND(SUM(gain_loss),2) FROM sale_lot_matches "
                            "WHERE cryptocurrency='ETH'").fetchone()[0]
    finally:
        conn.close()
    assert gain == 2000.0


def test_realizing_swap_still_produces_a_match(db_path):
    """With the default setting nothing changes: the swap is a disposal."""
    _seed_swap(db_path)

    tracker = CryptoFIFOTracker(db_path)
    try:
        tracker.calculate_fifo_lots('ETH')
    finally:
        tracker.close()

    conn = sqlite3.connect(db_path)
    try:
        gain = conn.execute("SELECT ROUND(SUM(gain_loss),2) FROM sale_lot_matches "
                            "WHERE cryptocurrency='ETH'").fetchone()[0]
        transfers = conn.execute('SELECT COUNT(*) FROM swap_lot_transfers').fetchone()[0]
    finally:
        conn.close()

    assert gain == 2000.0
    assert transfers == 0

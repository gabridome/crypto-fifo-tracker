"""Deferral path: a swap that does not realize, and the inheritance it creates."""
import inspect
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
    # kw.get() has already put the key there as None, so setdefault would be a
    # no-op: this is what actually makes the fee 0 rather than NULL.
    row['fee_amount'] = row['fee_amount'] or 0
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


def _db_without_swap_group_column(db_path):
    """A DB from the real doc/schema.sql, then with transactions.swap_group_id removed.

    Built from the whole schema and then narrowed, for the same reason as
    _pre_migration_db: every other column and table is then exactly what a
    fresh install has, and only the one thing this fixture is about is
    missing. ALTER TABLE DROP COLUMN needs SQLite >= 3.35 and refuses while
    an index still references the column, so idx_transactions_swap_group goes
    first; both facts are checked here rather than assumed, so an
    environment where the drop silently did nothing cannot make this test
    pass for the wrong reason.
    """
    if sqlite3.sqlite_version_info < (3, 35):
        pytest.skip(f'ALTER TABLE DROP COLUMN needs SQLite >= 3.35, '
                    f'this is {sqlite3.sqlite_version}')
    conn = sqlite3.connect(db_path)
    try:
        with open(SCHEMA_FILE) as f:
            conn.executescript(f.read())
        conn.execute('DROP INDEX idx_transactions_swap_group')
        conn.execute('ALTER TABLE transactions DROP COLUMN swap_group_id')
        conn.commit()
        columns = [r[1] for r in conn.execute('PRAGMA table_info(transactions)')]
        assert 'swap_group_id' not in columns, 'the drop did not take effect'
        assert 'record_hash' in columns, 'the drop removed more than its column'
    finally:
        conn.close()
    return db_path


def test_engine_on_a_database_without_the_swap_group_column_stops_and_names_the_migration(tmp_path):
    """Read in every mode, so task 1's migration is a prerequisite, not an option.

    Default swap_realizes = True here on purpose: the query selects
    swap_group_id whatever the rule says, so without this guard the engine
    dies on a raw sqlite3.OperationalError (no such column) partway through
    a crypto instead of saying which migration is missing.
    """
    db = _db_without_swap_group_column(str(tmp_path / 'no_swap_group_column.db'))

    tracker = CryptoFIFOTracker(db)
    try:
        with pytest.raises(ValueError, match='update_fifo_schema_swap_group'):
            tracker.calculate_fifo_lots('ETH')
    finally:
        tracker.close()


def test_a_second_deferral_pass_replaces_the_transfers_instead_of_doubling_them(db_path, monkeypatch):
    """The DELETE at the top is what makes a re-run idempotent per crypto.

    Measured by removing the DELETE: the second pass does NOT raise
    IntegrityError. The lots are deleted and re-inserted on every pass, so the
    re-created lot has a new id and the UNIQUE (swap_group_id,
    source_fifo_lot_id) index sees ('swap-1', 2) as a different row from
    ('swap-1', 1) — two rows, inherited cost basis silently doubled, no error.
    The DELETE is the only thing that prevents it. The JOIN pins the other
    half: the pointer must be the id of the lot the second pass created, not
    the stale id from the first.
    """
    _seed_swap(db_path)
    monkeypatch.setattr('calculators.crypto_fifo_tracker.SWAP_REALIZES', False)

    tracker = CryptoFIFOTracker(db_path)
    try:
        tracker.calculate_fifo_lots('ETH')
        tracker.calculate_fifo_lots('ETH')
    finally:
        tracker.close()

    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute("""SELECT t.swap_group_id, t.cost_basis, l.cryptocurrency, l.purchase_date
                               FROM swap_lot_transfers t
                               JOIN fifo_lots l ON l.id = t.source_fifo_lot_id""").fetchall()
        total = conn.execute('SELECT COUNT(*) FROM swap_lot_transfers').fetchone()[0]
    finally:
        conn.close()

    assert total == 1, 'the second pass must replace the transfer, not add one'
    assert rows == [('swap-1', 1000.0, 'ETH', '2020-01-10T10:00:00+00:00')], \
        'the pointer must resolve to the lot the second pass created'


def _driver_loop(db_path, cryptos):
    """One tracker, several cryptos, an exception caught per crypto, loop carried on.

    This is the shape of the real driver, calculators/calculate_fifo.py:87-119:
    it calls calculate_fifo_lots() inside a per-crypto try/except Exception on
    one shared connection and keeps going. The engine's transactional
    behaviour is what is under test here, not the driver's three lines of
    reporting — but the sequence matters, because it is the next crypto's
    commit() that decides the fate of a failed crypto's uncommitted DELETEs.
    Returns the list of (crypto, exception) that failed.
    """
    failed = []
    tracker = CryptoFIFOTracker(db_path)
    try:
        for crypto in cryptos:
            try:
                tracker.calculate_fifo_lots(crypto)
            except Exception as e:  # the driver prints the traceback and continues
                failed.append((crypto, e))
    finally:
        tracker.close()
    return failed


def _fifo_state(db_path, crypto):
    conn = sqlite3.connect(db_path)
    try:
        lots = conn.execute('SELECT COUNT(*) FROM fifo_lots WHERE cryptocurrency = ?',
                            (crypto,)).fetchone()[0]
        matches = conn.execute('SELECT COUNT(*) FROM sale_lot_matches WHERE cryptocurrency = ?',
                               (crypto,)).fetchone()[0]
        gain = conn.execute('SELECT ROUND(SUM(gain_loss),2) FROM sale_lot_matches '
                            'WHERE cryptocurrency = ?', (crypto,)).fetchone()[0]
    finally:
        conn.close()
    return {'lots': lots, 'matches': matches, 'gain': gain}


def _seed_two_cryptos_one_orphan_swap(db_path):
    """ETH with a deferred swap whose BUY leg is missing, plus an unrelated crypto."""
    conn = sqlite3.connect(db_path)
    try:
        _tx(conn, transaction_date='2020-01-10T10:00:00+00:00', transaction_type='BUY',
            cryptocurrency='ETH', amount=1.0, price_per_unit=1000.0, total_value=1000.0,
            fee_currency='EUR', currency='EUR', exchange_name='Test',
            source='seed', imported_at='2026-01-01T00:00:00', record_hash='o1')
        _tx(conn, transaction_date='2024-04-22T15:00:00+00:00', transaction_type='SELL',
            cryptocurrency='ETH', amount=1.0, price_per_unit=3000.0, total_value=3000.0,
            fee_currency='EUR', currency='EUR', exchange_name='Test',
            source='seed', imported_at='2026-01-01T00:00:00', record_hash='o2',
            swap_group_id='swap-orphan')  # no BUY leg anywhere for this group
        _tx(conn, transaction_date='2021-03-01T10:00:00+00:00', transaction_type='BUY',
            cryptocurrency='XYZ', amount=2.0, price_per_unit=50.0, total_value=100.0,
            fee_currency='EUR', currency='EUR', exchange_name='Test',
            source='seed', imported_at='2026-01-01T00:00:00', record_hash='o3')
        _tx(conn, transaction_date='2023-07-01T10:00:00+00:00', transaction_type='SELL',
            cryptocurrency='XYZ', amount=1.0, price_per_unit=80.0, total_value=80.0,
            fee_currency='EUR', currency='EUR', exchange_name='Test',
            source='seed', imported_at='2026-01-01T00:00:00', record_hash='o4')
    finally:
        conn.close()


def test_a_failing_crypto_keeps_the_fifo_data_it_had_instead_of_losing_it(db_path, monkeypatch):
    """A raise inside calculate_fifo_lots must not destroy that crypto's data.

    calculate_fifo_lots DELETEs a crypto's lots and matches before rewriting
    them. The driver catches per crypto and keeps going on the same
    connection, so without an explicit transaction and a rollback the NEXT
    crypto's commit() makes the failed crypto's DELETEs permanent: ETH ends
    up with zero lots and zero matches, committed, while the driver prints
    that the calculation is complete and every later report simply omits ETH.
    doc/code_guidelines.md §1.1 requires BEGIN + ROLLBACK for exactly this.
    """
    _seed_two_cryptos_one_orphan_swap(db_path)

    assert _driver_loop(db_path, ['ETH', 'XYZ']) == [], 'the default run must not fail'
    before = _fifo_state(db_path, 'ETH')
    assert before == {'lots': 1, 'matches': 1, 'gain': 2000.0}, 'seeding failed'

    monkeypatch.setattr('calculators.crypto_fifo_tracker.SWAP_REALIZES', False)
    failed = _driver_loop(db_path, ['ETH', 'XYZ'])

    assert [crypto for crypto, _ in failed] == ['ETH'], 'ETH should raise, XYZ should not'
    assert _fifo_state(db_path, 'ETH') == before, \
        'the failed crypto lost committed FIFO data — the DELETEs were not rolled back'
    assert _fifo_state(db_path, 'XYZ')['matches'] == 1, 'the healthy crypto must still compute'


def _seed_deferred_swap(db_path, crypto, group, take_asset='USDT', tag='s',
                        amount=1.0, price=1000.0, sale_price=3000.0):
    """BUY <amount> <crypto> in 2020, swapped in 2024 for <take_asset>, both legs tagged."""
    conn = sqlite3.connect(db_path)
    try:
        _tx(conn, transaction_date='2020-01-10T10:00:00+00:00', transaction_type='BUY',
            cryptocurrency=crypto, amount=amount, price_per_unit=price,
            total_value=amount * price, fee_currency='EUR', currency='EUR',
            exchange_name='Test', source='seed', imported_at='2026-01-01T00:00:00',
            record_hash=f'{tag}-buy')
        _tx(conn, transaction_date='2024-04-22T15:00:00+00:00', transaction_type='SELL',
            cryptocurrency=crypto, amount=amount, price_per_unit=sale_price,
            total_value=amount * sale_price, fee_currency='EUR', currency='EUR',
            exchange_name='Test', source='seed', imported_at='2026-01-01T00:00:00',
            record_hash=f'{tag}-sell', swap_group_id=group)
        _tx(conn, transaction_date='2024-04-22T15:00:00+00:00', transaction_type='BUY',
            cryptocurrency=take_asset, amount=amount * sale_price, price_per_unit=1.0,
            total_value=amount * sale_price, fee_currency='EUR', currency='EUR',
            exchange_name='Test', source='seed', imported_at='2026-01-01T00:00:00',
            record_hash=f'{tag}-take', swap_group_id=group)
    finally:
        conn.close()


def test_a_plain_sale_still_realizes_while_a_swap_defers_in_the_same_crypto(db_path, monkeypatch):
    """Deferral is for swaps, not for every sale: `and bool(swap_group_id)` matters.

    Drop that half of the condition and under deferral EVERY sale stops
    realizing — all capital gains vanish from the reports while the run looks
    healthy. Same crypto, same lot, two sales: one plain, one swap.
    """
    conn = sqlite3.connect(db_path)
    try:
        _tx(conn, transaction_date='2020-01-10T10:00:00+00:00', transaction_type='BUY',
            cryptocurrency='ETH', amount=2.0, price_per_unit=1000.0, total_value=2000.0,
            fee_currency='EUR', currency='EUR', exchange_name='Test',
            source='seed', imported_at='2026-01-01T00:00:00', record_hash='m1')
        _tx(conn, transaction_date='2024-04-22T15:00:00+00:00', transaction_type='SELL',
            cryptocurrency='ETH', amount=1.0, price_per_unit=3000.0, total_value=3000.0,
            fee_currency='EUR', currency='EUR', exchange_name='Test',
            source='seed', imported_at='2026-01-01T00:00:00', record_hash='m2')  # plain
        _tx(conn, transaction_date='2024-04-23T15:00:00+00:00', transaction_type='SELL',
            cryptocurrency='ETH', amount=1.0, price_per_unit=3000.0, total_value=3000.0,
            fee_currency='EUR', currency='EUR', exchange_name='Test',
            source='seed', imported_at='2026-01-01T00:00:00', record_hash='m3',
            swap_group_id='swap-mix')
        _tx(conn, transaction_date='2024-04-23T15:00:00+00:00', transaction_type='BUY',
            cryptocurrency='USDT', amount=3000.0, price_per_unit=1.0, total_value=3000.0,
            fee_currency='EUR', currency='EUR', exchange_name='Test',
            source='seed', imported_at='2026-01-01T00:00:00', record_hash='m4',
            swap_group_id='swap-mix')
    finally:
        conn.close()
    monkeypatch.setattr('calculators.crypto_fifo_tracker.SWAP_REALIZES', False)

    tracker = CryptoFIFOTracker(db_path)
    try:
        tracker.calculate_fifo_lots('ETH')
    finally:
        tracker.close()

    conn = sqlite3.connect(db_path)
    try:
        matches = conn.execute("""SELECT sale_date, amount_sold, gain_loss FROM sale_lot_matches
                                  WHERE cryptocurrency='ETH'""").fetchall()
        transfers = conn.execute("""SELECT swap_group_id, to_cryptocurrency, cost_basis
                                    FROM swap_lot_transfers""").fetchall()
    finally:
        conn.close()

    assert matches == [('2024-04-22T15:00:00+00:00', 1.0, 2000.0)], \
        'the plain sale must still realize its gain under deferral'
    assert transfers == [('swap-mix', 'USDT', 1000.0)], 'only the swap defers'


def test_recalculating_one_crypto_leaves_another_cryptos_inheritance_alone(db_path, monkeypatch):
    """The DELETE is per crypto, and the driver recalculates them one by one.

    Drop the WHERE from_cryptocurrency and every other crypto's inheritance
    disappears when the next crypto is recalculated on the same connection:
    the last crypto processed is the only one left with a cost basis.
    """
    _seed_deferred_swap(db_path, 'ETH', 'swap-eth', tag='eth')
    _seed_deferred_swap(db_path, 'SOL', 'swap-sol', tag='sol')
    monkeypatch.setattr('calculators.crypto_fifo_tracker.SWAP_REALIZES', False)

    tracker = CryptoFIFOTracker(db_path)
    try:
        tracker.calculate_fifo_lots('ETH')
        tracker.calculate_fifo_lots('SOL')  # must not touch ETH's row
    finally:
        tracker.close()

    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute("""SELECT from_cryptocurrency, swap_group_id FROM swap_lot_transfers
                               ORDER BY from_cryptocurrency""").fetchall()
    finally:
        conn.close()

    assert rows == [('ETH', 'swap-eth'), ('SOL', 'swap-sol')], \
        "recalculating SOL deleted ETH's inheritance"


def test_the_inherited_cost_basis_is_rounded_to_cents(db_path, monkeypatch):
    """The cost basis crosses into the DB as EUR, so it is quantized, not raw.

    3 units at EUR 100.333333 cost EUR 300.999999 exactly; what must land in
    swap_lot_transfers is EUR 301.00 (doc/project_guidelines.md: EUR values
    quantize to 0.01 before every INSERT). A plain float() would store
    300.999999 and carry the unrounded tail into whatever inherits it.
    """
    _seed_deferred_swap(db_path, 'ETH', 'swap-round', tag='r',
                        amount=3.0, price=100.333333, sale_price=200.0)
    monkeypatch.setattr('calculators.crypto_fifo_tracker.SWAP_REALIZES', False)

    tracker = CryptoFIFOTracker(db_path)
    try:
        tracker.calculate_fifo_lots('ETH')
    finally:
        tracker.close()

    conn = sqlite3.connect(db_path)
    try:
        stored = conn.execute('SELECT cost_basis FROM swap_lot_transfers').fetchall()
    finally:
        conn.close()

    assert stored == [(301.0,)], 'the inherited cost basis must be rounded to cents'


def test_an_orphan_deferred_group_raises_even_when_no_lot_is_consumed(db_path, monkeypatch):
    """Validation before the loop, not inside it.

    A deferred SELL with no purchase on record consumes nothing, so a check
    placed inside the consumption loop never runs — and the orphan group
    passes unnoticed until its missing cost basis surfaces somewhere else.
    """
    conn = sqlite3.connect(db_path)
    try:
        _tx(conn, transaction_date='2024-04-22T15:00:00+00:00', transaction_type='SELL',
            cryptocurrency='ETH', amount=1.0, price_per_unit=3000.0, total_value=3000.0,
            fee_currency='EUR', currency='EUR', exchange_name='Test',
            source='seed', imported_at='2026-01-01T00:00:00', record_hash='n1',
            swap_group_id='swap-nolots')
    finally:
        conn.close()
    monkeypatch.setattr('calculators.crypto_fifo_tracker.SWAP_REALIZES', False)

    tracker = CryptoFIFOTracker(db_path)
    try:
        with pytest.raises(ValueError, match='swap-nolots'):
            tracker.calculate_fifo_lots('ETH')
    finally:
        tracker.close()


def _seed_swap_group_with_two_take_legs(db_path):
    """One swap group whose BUY leg exists twice, in two different assets."""
    _seed_deferred_swap(db_path, 'ETH', 'swap-two', tag='t')
    conn = sqlite3.connect(db_path)
    try:
        _tx(conn, transaction_date='2024-04-22T15:00:00+00:00', transaction_type='BUY',
            cryptocurrency='SOL', amount=10.0, price_per_unit=300.0, total_value=3000.0,
            fee_currency='EUR', currency='EUR', exchange_name='Test',
            source='seed', imported_at='2026-01-01T00:00:00', record_hash='t-take2',
            swap_group_id='swap-two')
    finally:
        conn.close()


def test_two_buy_legs_in_one_swap_group_refuse_to_pick_a_winner(db_path, monkeypatch):
    """Which asset inherits must not be decided by row order.

    A dict comprehension keyed on swap_group_id silently keeps the last row,
    and the query has no ORDER BY, so the winner is not even stable between
    runs. Under deferral that choice decides where a cost basis lands.
    """
    _seed_swap_group_with_two_take_legs(db_path)
    monkeypatch.setattr('calculators.crypto_fifo_tracker.SWAP_REALIZES', False)

    tracker = CryptoFIFOTracker(db_path)
    try:
        with pytest.raises(ValueError, match='two different assets'):
            tracker.calculate_fifo_lots('ETH')
    finally:
        tracker.close()


def test_two_buy_legs_in_one_swap_group_only_warn_on_the_default_path(db_path, capsys):
    """The default path never reads swap_takes, so it must not be stopped by it.

    These are the figures already declared: a broken import that cannot change
    any of them is a warning, not an abort. Under deferral the same data
    raises (test above).
    """
    _seed_swap_group_with_two_take_legs(db_path)

    tracker = CryptoFIFOTracker(db_path)
    try:
        tracker.calculate_fifo_lots('ETH')
    finally:
        tracker.close()
    out = capsys.readouterr().out

    assert 'two different assets' in out, 'the conflict must still be reported'
    conn = sqlite3.connect(db_path)
    try:
        gain = conn.execute("SELECT ROUND(SUM(gain_loss),2) FROM sale_lot_matches "
                            "WHERE cryptocurrency='ETH'").fetchone()[0]
    finally:
        conn.close()
    assert gain == 2000.0, 'the default path must still realize the disposal'


def test_deferral_warns_loudly_that_the_chain_is_incomplete_until_task_11(
        db_path, monkeypatch, capsys):
    """Between task 9 and task 11 the numbers are wrong in both directions.

    The give side defers but the asset received does not inherit yet, so its
    BUY leg opens an ordinary lot at market value with a fresh date: both the
    exempt/taxable split and the cost of the asset received are wrong, and
    nothing in the output would say so without this warning. The task number
    is in the message on purpose — it says when the scaffolding comes out.
    Asserted in both directions: absent on the default path, which has no
    scaffolding to warn about.
    """
    _seed_swap(db_path)

    tracker = CryptoFIFOTracker(db_path)
    try:
        tracker.calculate_fifo_lots('ETH')
    finally:
        tracker.close()
    default_out = capsys.readouterr().out
    assert 'INCOMPLETE' not in default_out, 'the default path has no scaffolding to warn about'

    monkeypatch.setattr('calculators.crypto_fifo_tracker.SWAP_REALIZES', False)
    tracker = CryptoFIFOTracker(db_path)
    try:
        tracker.calculate_fifo_lots('ETH')
    finally:
        tracker.close()
    deferred_out = capsys.readouterr().out

    assert 'INCOMPLETE' in deferred_out
    assert 'Do NOT use these numbers for any tax return' in deferred_out
    assert 'Task 11' in deferred_out, 'the warning must say what removes it'


def _seed_two_sells_in_one_swap_group(db_path):
    """ETH disposed of twice under one swap group, plus an unrelated crypto.

    Two SELLs sharing a swap_group_id draw the same lot into the same group's
    inheritance twice, which the UNIQUE (swap_group_id, source_fifo_lot_id)
    index refuses — mid-write, after the DELETEs. The importer does not
    produce this (one group is one normalized row), but a hand-edited or
    double-imported row does, and it is the realistic way this engine raises
    with its tables already emptied.
    """
    conn = sqlite3.connect(db_path)
    try:
        _tx(conn, transaction_date='2020-01-10T10:00:00+00:00', transaction_type='BUY',
            cryptocurrency='ETH', amount=2.0, price_per_unit=1000.0, total_value=2000.0,
            fee_currency='EUR', currency='EUR', exchange_name='Test',
            source='seed', imported_at='2026-01-01T00:00:00', record_hash='d1')
        for tag, date in (('d2', '2024-04-22T15:00:00+00:00'), ('d3', '2024-04-23T15:00:00+00:00')):
            _tx(conn, transaction_date=date, transaction_type='SELL',
                cryptocurrency='ETH', amount=1.0, price_per_unit=3000.0, total_value=3000.0,
                fee_currency='EUR', currency='EUR', exchange_name='Test',
                source='seed', imported_at='2026-01-01T00:00:00', record_hash=tag,
                swap_group_id='swap-dup')
        _tx(conn, transaction_date='2024-04-22T15:00:00+00:00', transaction_type='BUY',
            cryptocurrency='USDT', amount=6000.0, price_per_unit=1.0, total_value=6000.0,
            fee_currency='EUR', currency='EUR', exchange_name='Test',
            source='seed', imported_at='2026-01-01T00:00:00', record_hash='d4',
            swap_group_id='swap-dup')
        _tx(conn, transaction_date='2021-03-01T10:00:00+00:00', transaction_type='BUY',
            cryptocurrency='XYZ', amount=2.0, price_per_unit=50.0, total_value=100.0,
            fee_currency='EUR', currency='EUR', exchange_name='Test',
            source='seed', imported_at='2026-01-01T00:00:00', record_hash='d5')
        _tx(conn, transaction_date='2023-07-01T10:00:00+00:00', transaction_type='SELL',
            cryptocurrency='XYZ', amount=1.0, price_per_unit=80.0, total_value=80.0,
            fee_currency='EUR', currency='EUR', exchange_name='Test',
            source='seed', imported_at='2026-01-01T00:00:00', record_hash='d6')
    finally:
        conn.close()


def test_a_failure_after_the_deletes_is_rolled_back_not_left_for_the_next_commit(
        db_path, monkeypatch):
    """The transaction, not the validation: this one raises mid-write.

    Validation before the first DELETE (the test above) cannot cover every
    way the write phase can fail — here the UNIQUE index on
    swap_lot_transfers rejects the second row, with ETH's lots and matches
    already deleted inside the open transaction. Without BEGIN + ROLLBACK the
    next crypto's commit() makes those DELETEs permanent and ETH is silently
    reduced to zero lots, zero matches, while the driver reports success.
    doc/code_guidelines.md §1.1.
    """
    _seed_two_sells_in_one_swap_group(db_path)

    assert _driver_loop(db_path, ['ETH', 'XYZ']) == [], 'the default run must not fail'
    before = _fifo_state(db_path, 'ETH')
    assert before == {'lots': 1, 'matches': 2, 'gain': 4000.0}, 'seeding failed'

    monkeypatch.setattr('calculators.crypto_fifo_tracker.SWAP_REALIZES', False)
    failed = _driver_loop(db_path, ['ETH', 'XYZ'])

    assert [crypto for crypto, _ in failed] == ['ETH'], \
        'ETH must fail mid-write and XYZ must still be calculable on the same connection'
    assert isinstance(failed[0][1], sqlite3.IntegrityError), \
        f'expected the UNIQUE index to reject the second transfer, got {failed[0][1]!r}'
    assert _fifo_state(db_path, 'ETH') == before, \
        'the failed crypto lost committed FIFO data — the write was not rolled back'
    assert _fifo_state(db_path, 'XYZ')['matches'] == 1, 'the healthy crypto must still compute'


def test_the_write_phase_opens_an_explicit_transaction():
    """Pins `self.conn.execute('BEGIN')` in calculate_fifo_lots, deliberately.

    Three things, in order, because the third is the only one that matters:

    1. Today this line is redundant. Measured on this interpreter: with the
       sqlite3 module's legacy transaction control (the default,
       isolation_level == ''), `conn.in_transaction` is already True after the
       first DELETE with no explicit BEGIN, so the rollback() in the error
       path would undo the deletes anyway.
    2. So a reviewer can demonstrate the line does nothing and delete it. That
       is the removal this test exists to stop, which is why it asserts the
       source rather than a behaviour: there is no behaviour to assert while
       the legacy default holds, and the two tests above
       (…_keeps_the_fifo_data_it_had, …_rolled_back_not_left_for_the_next_commit)
       stay green without it.
    3. What makes rollback() effective is not our code, it is that module
       default — and the sqlite3 module has an announced move towards
       autocommit. Under autocommit a rollback() with no explicit BEGIN undoes
       nothing, the DELETEs are already permanent, and the data loss this
       branch just closed comes back silently on a tax-history database.

    Remove it only together with an explicit declaration of the connection's
    transaction mode in __init__ — not because it is redundant. Measured, so
    that the choice is informed: `autocommit=False` is the declaration that
    would survive the change, and it does not exist on the floor this project
    declares (pyproject.toml target-version = py311; on 3.11.17 the kwarg
    raises TypeError and Connection has no autocommit attribute), while
    passing isolation_level='' only restates today's default and would be
    ignored once autocommit is set.
    """
    source = inspect.getsource(CryptoFIFOTracker.calculate_fifo_lots)

    assert "self.conn.execute('BEGIN')" in source, (
        'the write phase of calculate_fifo_lots must open its transaction explicitly'
    )
    assert source.index("self.conn.execute('BEGIN')") < source.index('DELETE FROM'), (
        'BEGIN must come before the first DELETE, or the deletes are not in the '
        'transaction that the error path rolls back'
    )


# ---------------------------------------------------------------------------
# Task 10 — the take side: lots that inherit date and cost.
#
# Idempotency on this side needs nothing new: the engine clears fifo_lots and
# sale_lot_matches for the crypto it is about to process, so inherited lots are
# rewritten from scratch on every pass. The swap_lot_transfers unique index is
# no guard here — a re-run brings the lots back with new ids, so the
# (swap_group_id, source_fifo_lot_id) pair differs. The asymmetry is on
# purpose: the engine deletes transfers by from_cryptocurrency, so processing
# the take side never destroys the rows it is there to read.
# ---------------------------------------------------------------------------


def test_received_asset_inherits_date_and_cost(db_path, monkeypatch):
    """The USDT received keeps the ETH acquisition date: the clock does not restart."""
    _seed_swap(db_path)
    monkeypatch.setattr('calculators.crypto_fifo_tracker.SWAP_REALIZES', False)

    tracker = CryptoFIFOTracker(db_path)
    try:
        tracker.calculate_fifo_lots('ETH')    # give side first
        tracker.calculate_fifo_lots('USDT')   # take side
    finally:
        tracker.close()

    conn = sqlite3.connect(db_path)
    try:
        lots = conn.execute("""SELECT purchase_date, ROUND(original_amount,8),
                                      ROUND(cost_basis,2), ROUND(purchase_price_per_unit,8)
                               FROM fifo_lots WHERE cryptocurrency='USDT'""").fetchall()
    finally:
        conn.close()

    assert lots == [('2020-01-10T10:00:00+00:00', 2000.0, 1000.0, 0.5)]


def test_two_source_lots_become_two_inherited_lots(db_path, monkeypatch):
    """One swap of two lots creates two lots, each with its own inherited date."""
    conn = sqlite3.connect(db_path)
    try:
        _tx(conn, transaction_date='2019-01-01T10:00:00+00:00', transaction_type='BUY',
            cryptocurrency='ETH', amount=0.5, price_per_unit=200.0, total_value=100.0,
            fee_currency='EUR', currency='EUR', exchange_name='Test', source='seed',
            imported_at='2026-01-01T00:00:00', record_hash='a1')
        _tx(conn, transaction_date='2021-01-01T10:00:00+00:00', transaction_type='BUY',
            cryptocurrency='ETH', amount=0.5, price_per_unit=600.0, total_value=300.0,
            fee_currency='EUR', currency='EUR', exchange_name='Test', source='seed',
            imported_at='2026-01-01T00:00:00', record_hash='a2')
        _tx(conn, transaction_date='2024-04-22T15:00:00+00:00', transaction_type='SELL',
            cryptocurrency='ETH', amount=1.0, price_per_unit=3000.0, total_value=3000.0,
            fee_currency='EUR', currency='EUR', exchange_name='Test', source='seed',
            imported_at='2026-01-01T00:00:00', record_hash='a3', swap_group_id='swap-2')
        _tx(conn, transaction_date='2024-04-22T15:00:00+00:00', transaction_type='BUY',
            cryptocurrency='USDT', amount=1000.0, price_per_unit=3.0, total_value=3000.0,
            fee_currency='EUR', currency='EUR', exchange_name='Test', source='seed',
            imported_at='2026-01-01T00:00:00', record_hash='a4', swap_group_id='swap-2')
    finally:
        conn.close()
    monkeypatch.setattr('calculators.crypto_fifo_tracker.SWAP_REALIZES', False)

    tracker = CryptoFIFOTracker(db_path)
    try:
        tracker.calculate_fifo_lots('ETH')
        tracker.calculate_fifo_lots('USDT')
    finally:
        tracker.close()

    conn = sqlite3.connect(db_path)
    try:
        lots = conn.execute("""SELECT purchase_date, ROUND(original_amount,8), ROUND(cost_basis,2)
                               FROM fifo_lots WHERE cryptocurrency='USDT'
                               ORDER BY purchase_date""").fetchall()
    finally:
        conn.close()

    # 1000 USDT split pro-rata on the ETH consumed (0.5 + 0.5), each slice keeping its cost
    assert lots == [('2019-01-01T10:00:00+00:00', 500.0, 100.0),
                    ('2021-01-01T10:00:00+00:00', 500.0, 300.0)]


def test_an_inherited_lot_older_than_an_existing_lot_is_consumed_first(db_path, monkeypatch):
    """FIFO follows the acquisition date, including an inherited one.

    The received asset already had a lot of its own, bought later than the lot
    the swap hands it. Appending the inherited lot would put the newer lot
    first in mem_lots and the consumption loop walks that list in order, so a
    later sale would match the wrong lot — wrong cost basis, wrong holding
    period, wrong exempt/taxable split.
    """
    conn = sqlite3.connect(db_path)
    try:
        _tx(conn, transaction_date='2019-01-01T10:00:00+00:00', transaction_type='BUY',
            cryptocurrency='ETH', amount=1.0, price_per_unit=1000.0, total_value=1000.0,
            fee_currency='EUR', currency='EUR', exchange_name='Test', source='seed',
            imported_at='2026-01-01T00:00:00', record_hash='o1')
        # USDT bought on its own, LATER than the ETH the swap will hand over
        _tx(conn, transaction_date='2022-06-01T10:00:00+00:00', transaction_type='BUY',
            cryptocurrency='USDT', amount=500.0, price_per_unit=0.9, total_value=450.0,
            fee_currency='EUR', currency='EUR', exchange_name='Test', source='seed',
            imported_at='2026-01-01T00:00:00', record_hash='o2')
        _tx(conn, transaction_date='2024-04-22T15:00:00+00:00', transaction_type='SELL',
            cryptocurrency='ETH', amount=1.0, price_per_unit=3000.0, total_value=3000.0,
            fee_currency='EUR', currency='EUR', exchange_name='Test', source='seed',
            imported_at='2026-01-01T00:00:00', record_hash='o3', swap_group_id='swap-3')
        _tx(conn, transaction_date='2024-04-22T15:00:00+00:00', transaction_type='BUY',
            cryptocurrency='USDT', amount=1000.0, price_per_unit=3.0, total_value=3000.0,
            fee_currency='EUR', currency='EUR', exchange_name='Test', source='seed',
            imported_at='2026-01-01T00:00:00', record_hash='o4', swap_group_id='swap-3')
        # Sell 600 USDT: FIFO must take the 1000 inherited (2019) before the 500 own (2022)
        _tx(conn, transaction_date='2024-09-01T10:00:00+00:00', transaction_type='SELL',
            cryptocurrency='USDT', amount=600.0, price_per_unit=1.0, total_value=600.0,
            fee_currency='EUR', currency='EUR', exchange_name='Test', source='seed',
            imported_at='2026-01-01T00:00:00', record_hash='o5')
    finally:
        conn.close()
    monkeypatch.setattr('calculators.crypto_fifo_tracker.SWAP_REALIZES', False)

    tracker = CryptoFIFOTracker(db_path)
    try:
        tracker.calculate_fifo_lots('ETH')
        tracker.calculate_fifo_lots('USDT')
    finally:
        tracker.close()

    conn = sqlite3.connect(db_path)
    try:
        matched = conn.execute("""SELECT purchase_date, ROUND(amount_sold,8)
                                  FROM sale_lot_matches WHERE cryptocurrency='USDT'
                                  ORDER BY purchase_date""").fetchall()
    finally:
        conn.close()

    assert matched == [('2019-01-01T10:00:00+00:00', 600.0)], (
        'the sale must consume the inherited lot first: it was acquired in 2019, '
        'the asset own lot only in 2022'
    )


def test_inherited_lot_ordering_survives_both_date_formats(db_path, monkeypatch):
    """The production database stores both forms, so the key must cope.

    Measured read-only on data/crypto_fifo.db (2026-10-17): most rows end in
    '+00:00' and a minority have no offset at all. datetime.fromisoformat on the two
    gives an aware and a naive datetime, and comparing them raises TypeError.
    The existing holding-period code sidesteps it by comparing .date(); the
    ordering added by this task must not reintroduce it.
    """
    conn = sqlite3.connect(db_path)
    try:
        _tx(conn, transaction_date='2019-01-01T10:00:00', transaction_type='BUY',   # naive
            cryptocurrency='ETH', amount=1.0, price_per_unit=1000.0, total_value=1000.0,
            fee_currency='EUR', currency='EUR', exchange_name='Test', source='seed',
            imported_at='2026-01-01T00:00:00', record_hash='m1')
        _tx(conn, transaction_date='2022-06-01T10:00:00+00:00', transaction_type='BUY',  # aware
            cryptocurrency='USDT', amount=500.0, price_per_unit=0.9, total_value=450.0,
            fee_currency='EUR', currency='EUR', exchange_name='Test', source='seed',
            imported_at='2026-01-01T00:00:00', record_hash='m2')
        _tx(conn, transaction_date='2024-04-22T15:00:00+00:00', transaction_type='SELL',
            cryptocurrency='ETH', amount=1.0, price_per_unit=3000.0, total_value=3000.0,
            fee_currency='EUR', currency='EUR', exchange_name='Test', source='seed',
            imported_at='2026-01-01T00:00:00', record_hash='m3', swap_group_id='swap-4')
        _tx(conn, transaction_date='2024-04-22T15:00:00+00:00', transaction_type='BUY',
            cryptocurrency='USDT', amount=1000.0, price_per_unit=3.0, total_value=3000.0,
            fee_currency='EUR', currency='EUR', exchange_name='Test', source='seed',
            imported_at='2026-01-01T00:00:00', record_hash='m4', swap_group_id='swap-4')
    finally:
        conn.close()
    monkeypatch.setattr('calculators.crypto_fifo_tracker.SWAP_REALIZES', False)

    tracker = CryptoFIFOTracker(db_path)
    try:
        tracker.calculate_fifo_lots('ETH')
        tracker.calculate_fifo_lots('USDT')     # must not raise TypeError
    finally:
        tracker.close()

    conn = sqlite3.connect(db_path)
    try:
        dates = [r[0] for r in conn.execute(
            "SELECT purchase_date FROM fifo_lots WHERE cryptocurrency='USDT' ORDER BY id")]
    finally:
        conn.close()

    assert dates == ['2019-01-01T10:00:00', '2022-06-01T10:00:00+00:00'], (
        'the inherited naive-dated lot belongs before the aware-dated own lot'
    )


def test_take_side_without_its_give_side_fails_loudly(db_path, monkeypatch):
    """Processing the received asset before the one given up is an ordering bug."""
    _seed_swap(db_path)
    monkeypatch.setattr('calculators.crypto_fifo_tracker.SWAP_REALIZES', False)

    tracker = CryptoFIFOTracker(db_path)
    try:
        with pytest.raises(ValueError, match='swap-1'):
            tracker.calculate_fifo_lots('USDT')  # give side not computed yet
    finally:
        tracker.close()


# ---------------------------------------------------------------------------
# Four more guards, each closing a mutation that the five tests above let
# through. Found by mutating the engine one line at a time and running the
# whole suite: a surviving mutation is an uncovered mechanism, not a safe one.
# ---------------------------------------------------------------------------


def test_the_received_asset_takes_a_fresh_lot_on_the_default_path(db_path):
    """With swap_realizes True nothing inherits: these are the declared figures.

    Nothing in the suite computed the *receiving* asset of a swap on the
    default path before this test, so dropping `not SWAP_REALIZES` from the
    new BUY condition changed nothing any test could see — while in reality it
    sends every take leg of every swap looking for an inheritance that the
    default path never records.
    """
    _seed_swap(db_path)  # no monkeypatch: SWAP_REALIZES stays at its default

    tracker = CryptoFIFOTracker(db_path)
    try:
        tracker.calculate_fifo_lots('ETH')
        tracker.calculate_fifo_lots('USDT')
    finally:
        tracker.close()

    conn = sqlite3.connect(db_path)
    try:
        lots = conn.execute("""SELECT purchase_date, ROUND(original_amount,8),
                                      ROUND(cost_basis,2), ROUND(purchase_price_per_unit,8)
                               FROM fifo_lots WHERE cryptocurrency='USDT'""").fetchall()
        transfers = conn.execute('SELECT COUNT(*) FROM swap_lot_transfers').fetchone()[0]
    finally:
        conn.close()

    assert lots == [('2024-04-22T15:00:00+00:00', 2000.0, 3000.0, 1.5)], \
        'on the default path the asset received is an ordinary purchase at market value'
    assert transfers == 0, 'the default path records no inheritance'


def test_a_match_on_an_inherited_lot_points_at_the_lot_it_consumed(db_path, monkeypatch):
    """fifo_lot_id must resolve to the right lot once lots are inserted, not appended.

    matches_batch records a lot while mem_lots is still being built and
    resolves it to a DB id after the inserts. Recording the lot's *position*
    works only while lots are appended: inserting an inherited lot in
    acquisition-date order shifts every position already recorded, silently,
    onto a different lot. purchase_date and cost on the match row would still
    look right — they are copied at match time — while fifo_lot_id, the only
    audit trail back to the purchase (generate_holding_report JOINs on it for
    the purchase exchange and transaction id), points at someone else's lot.
    """
    conn = sqlite3.connect(db_path)
    try:
        _tx(conn, transaction_date='2019-01-01T10:00:00+00:00', transaction_type='BUY',
            cryptocurrency='ETH', amount=1.0, price_per_unit=1000.0, total_value=1000.0,
            fee_currency='EUR', currency='EUR', exchange_name='Test', source='seed',
            imported_at='2026-01-01T00:00:00', record_hash='j1')
        _tx(conn, transaction_date='2022-06-01T10:00:00+00:00', transaction_type='BUY',
            cryptocurrency='USDT', amount=500.0, price_per_unit=0.9, total_value=450.0,
            fee_currency='EUR', currency='EUR', exchange_name='Test', source='seed',
            imported_at='2026-01-01T00:00:00', record_hash='j2')
        _tx(conn, transaction_date='2024-04-22T15:00:00+00:00', transaction_type='SELL',
            cryptocurrency='ETH', amount=1.0, price_per_unit=3000.0, total_value=3000.0,
            fee_currency='EUR', currency='EUR', exchange_name='Test', source='seed',
            imported_at='2026-01-01T00:00:00', record_hash='j3', swap_group_id='swap-6')
        _tx(conn, transaction_date='2024-04-22T15:00:00+00:00', transaction_type='BUY',
            cryptocurrency='USDT', amount=1000.0, price_per_unit=3.0, total_value=3000.0,
            fee_currency='EUR', currency='EUR', exchange_name='Test', source='seed',
            imported_at='2026-01-01T00:00:00', record_hash='j4', swap_group_id='swap-6')
        # 1200 spans both lots: the inherited one (1000) first, then 200 of its own
        _tx(conn, transaction_date='2024-09-01T10:00:00+00:00', transaction_type='SELL',
            cryptocurrency='USDT', amount=1200.0, price_per_unit=1.0, total_value=1200.0,
            fee_currency='EUR', currency='EUR', exchange_name='Test', source='seed',
            imported_at='2026-01-01T00:00:00', record_hash='j5')
    finally:
        conn.close()
    monkeypatch.setattr('calculators.crypto_fifo_tracker.SWAP_REALIZES', False)

    tracker = CryptoFIFOTracker(db_path)
    try:
        tracker.calculate_fifo_lots('ETH')
        tracker.calculate_fifo_lots('USDT')
    finally:
        tracker.close()

    conn = sqlite3.connect(db_path)
    try:
        joined = conn.execute("""SELECT m.purchase_date, l.purchase_date, ROUND(m.amount_sold,8)
                                 FROM sale_lot_matches m
                                 JOIN fifo_lots l ON l.id = m.fifo_lot_id
                                 WHERE m.cryptocurrency='USDT'
                                 ORDER BY m.purchase_date""").fetchall()
    finally:
        conn.close()

    assert joined == [('2019-01-01T10:00:00+00:00', '2019-01-01T10:00:00+00:00', 1000.0),
                      ('2022-06-01T10:00:00+00:00', '2022-06-01T10:00:00+00:00', 200.0)], \
        'fifo_lot_id must point at the lot whose date and cost the match carries'


def test_an_inherited_lot_older_than_an_exhausted_lot_is_still_reachable(db_path, monkeypatch):
    """The insertion point starts at lot_ptr, and that is not an optimization.

    lot_ptr is where the consumption loop begins; everything before it is an
    exhausted lot. An inherited lot is older than the lots already on record,
    so scanning from 0 would place it *before* lot_ptr when an older-dated lot
    has already been used up — out of the loop's reach for good. The next sale
    then reports its amount as unmatched and the inherited cost basis leaves
    the chain in silence.
    """
    conn = sqlite3.connect(db_path)
    try:
        _tx(conn, transaction_date='2019-01-01T10:00:00+00:00', transaction_type='BUY',
            cryptocurrency='ETH', amount=1.0, price_per_unit=1000.0, total_value=1000.0,
            fee_currency='EUR', currency='EUR', exchange_name='Test', source='seed',
            imported_at='2026-01-01T00:00:00', record_hash='p1')
        # a USDT lot of its own, bought and then entirely sold before the swap
        _tx(conn, transaction_date='2021-01-01T10:00:00+00:00', transaction_type='BUY',
            cryptocurrency='USDT', amount=500.0, price_per_unit=0.8, total_value=400.0,
            fee_currency='EUR', currency='EUR', exchange_name='Test', source='seed',
            imported_at='2026-01-01T00:00:00', record_hash='p2')
        _tx(conn, transaction_date='2022-01-01T10:00:00+00:00', transaction_type='SELL',
            cryptocurrency='USDT', amount=500.0, price_per_unit=1.0, total_value=500.0,
            fee_currency='EUR', currency='EUR', exchange_name='Test', source='seed',
            imported_at='2026-01-01T00:00:00', record_hash='p3')
        _tx(conn, transaction_date='2024-04-22T15:00:00+00:00', transaction_type='SELL',
            cryptocurrency='ETH', amount=1.0, price_per_unit=3000.0, total_value=3000.0,
            fee_currency='EUR', currency='EUR', exchange_name='Test', source='seed',
            imported_at='2026-01-01T00:00:00', record_hash='p4', swap_group_id='swap-7')
        _tx(conn, transaction_date='2024-04-22T15:00:00+00:00', transaction_type='BUY',
            cryptocurrency='USDT', amount=1000.0, price_per_unit=3.0, total_value=3000.0,
            fee_currency='EUR', currency='EUR', exchange_name='Test', source='seed',
            imported_at='2026-01-01T00:00:00', record_hash='p5', swap_group_id='swap-7')
        _tx(conn, transaction_date='2025-03-01T10:00:00+00:00', transaction_type='SELL',
            cryptocurrency='USDT', amount=400.0, price_per_unit=1.1, total_value=440.0,
            fee_currency='EUR', currency='EUR', exchange_name='Test', source='seed',
            imported_at='2026-01-01T00:00:00', record_hash='p6')
    finally:
        conn.close()
    monkeypatch.setattr('calculators.crypto_fifo_tracker.SWAP_REALIZES', False)

    tracker = CryptoFIFOTracker(db_path)
    try:
        tracker.calculate_fifo_lots('ETH')
        tracker.calculate_fifo_lots('USDT')
    finally:
        tracker.close()

    conn = sqlite3.connect(db_path)
    try:
        matched = conn.execute("""SELECT sale_date, purchase_date, ROUND(amount_sold,8),
                                         ROUND(cost_basis,2)
                                  FROM sale_lot_matches WHERE cryptocurrency='USDT'
                                  ORDER BY sale_date""").fetchall()
    finally:
        conn.close()

    assert matched == [
        ('2022-01-01T10:00:00+00:00', '2021-01-01T10:00:00+00:00', 500.0, 400.0),
        ('2025-03-01T10:00:00+00:00', '2019-01-01T10:00:00+00:00', 400.0, 400.0),
    ], 'the 2025 sale must reach the inherited lot, placed after the exhausted one'


def test_an_inherited_lot_queues_behind_an_existing_lot_of_the_same_date(db_path, monkeypatch):
    """Equal acquisition instants: the lot already on record goes first.

    FIFO by acquisition date says nothing about two lots acquired at the same
    instant, and the holding period is identical either way, so neither order
    is wrong. The order still decides which cost basis is realized in which
    year, so it is pinned here rather than left to flip silently: `<=` in the
    scan keeps the existing lot ahead, which makes the insertion stable.
    """
    conn = sqlite3.connect(db_path)
    try:
        _tx(conn, transaction_date='2020-01-10T10:00:00+00:00', transaction_type='BUY',
            cryptocurrency='ETH', amount=1.0, price_per_unit=1000.0, total_value=1000.0,
            fee_currency='EUR', currency='EUR', exchange_name='Test', source='seed',
            imported_at='2026-01-01T00:00:00', record_hash='q1')
        # same instant as the ETH purchase, so the inherited lot ties with it
        _tx(conn, transaction_date='2020-01-10T10:00:00+00:00', transaction_type='BUY',
            cryptocurrency='USDT', amount=300.0, price_per_unit=0.5, total_value=150.0,
            fee_currency='EUR', currency='EUR', exchange_name='Test', source='seed',
            imported_at='2026-01-01T00:00:00', record_hash='q2')
        _tx(conn, transaction_date='2024-04-22T15:00:00+00:00', transaction_type='SELL',
            cryptocurrency='ETH', amount=1.0, price_per_unit=3000.0, total_value=3000.0,
            fee_currency='EUR', currency='EUR', exchange_name='Test', source='seed',
            imported_at='2026-01-01T00:00:00', record_hash='q3', swap_group_id='swap-8')
        _tx(conn, transaction_date='2024-04-22T15:00:00+00:00', transaction_type='BUY',
            cryptocurrency='USDT', amount=1000.0, price_per_unit=3.0, total_value=3000.0,
            fee_currency='EUR', currency='EUR', exchange_name='Test', source='seed',
            imported_at='2026-01-01T00:00:00', record_hash='q4', swap_group_id='swap-8')
        # 300 units: EUR 150 if its own lot goes first, EUR 300 if the inherited one does
        _tx(conn, transaction_date='2024-09-01T10:00:00+00:00', transaction_type='SELL',
            cryptocurrency='USDT', amount=300.0, price_per_unit=1.0, total_value=300.0,
            fee_currency='EUR', currency='EUR', exchange_name='Test', source='seed',
            imported_at='2026-01-01T00:00:00', record_hash='q5')
    finally:
        conn.close()
    monkeypatch.setattr('calculators.crypto_fifo_tracker.SWAP_REALIZES', False)

    tracker = CryptoFIFOTracker(db_path)
    try:
        tracker.calculate_fifo_lots('ETH')
        tracker.calculate_fifo_lots('USDT')
    finally:
        tracker.close()

    conn = sqlite3.connect(db_path)
    try:
        matched = conn.execute("""SELECT ROUND(amount_sold,8), ROUND(cost_basis,2)
                                  FROM sale_lot_matches WHERE cryptocurrency='USDT'""").fetchall()
    finally:
        conn.close()

    assert matched == [(300.0, 150.0)], \
        'on a tie the lot already in this asset ledger is consumed first'


def test_two_source_lots_of_the_same_date_keep_their_give_side_order(db_path, monkeypatch):
    """Slices are read oldest first, and on a tie in the order they were consumed.

    Two purchases of the asset given up at the same timestamp produce two
    transfers with the same source_purchase_date and different costs. The
    ORDER BY on swap_lot_transfers.id is what keeps them in the order the give
    side consumed them — FIFO order, since the rows were appended as the lots
    were used. Reverse it and a partial sale of the received asset realizes
    the other lot cost basis, same date, same holding period, different gain.
    """
    conn = sqlite3.connect(db_path)
    try:
        for tag, price, total in (('s1', 100.0, 100.0), ('s2', 300.0, 300.0)):
            _tx(conn, transaction_date='2020-03-01T10:00:00+00:00', transaction_type='BUY',
                cryptocurrency='ETH', amount=1.0, price_per_unit=price, total_value=total,
                fee_currency='EUR', currency='EUR', exchange_name='Test', source='seed',
                imported_at='2026-01-01T00:00:00', record_hash=tag)
        _tx(conn, transaction_date='2024-04-22T15:00:00+00:00', transaction_type='SELL',
            cryptocurrency='ETH', amount=2.0, price_per_unit=1500.0, total_value=3000.0,
            fee_currency='EUR', currency='EUR', exchange_name='Test', source='seed',
            imported_at='2026-01-01T00:00:00', record_hash='s3', swap_group_id='swap-9')
        _tx(conn, transaction_date='2024-04-22T15:00:00+00:00', transaction_type='BUY',
            cryptocurrency='USDT', amount=1000.0, price_per_unit=3.0, total_value=3000.0,
            fee_currency='EUR', currency='EUR', exchange_name='Test', source='seed',
            imported_at='2026-01-01T00:00:00', record_hash='s4', swap_group_id='swap-9')
        # 500 of the 1000 received: the slice of the first ETH lot (EUR 100), not the second
        _tx(conn, transaction_date='2024-09-01T10:00:00+00:00', transaction_type='SELL',
            cryptocurrency='USDT', amount=500.0, price_per_unit=1.0, total_value=500.0,
            fee_currency='EUR', currency='EUR', exchange_name='Test', source='seed',
            imported_at='2026-01-01T00:00:00', record_hash='s5')
    finally:
        conn.close()
    monkeypatch.setattr('calculators.crypto_fifo_tracker.SWAP_REALIZES', False)

    tracker = CryptoFIFOTracker(db_path)
    try:
        tracker.calculate_fifo_lots('ETH')
        tracker.calculate_fifo_lots('USDT')
    finally:
        tracker.close()

    conn = sqlite3.connect(db_path)
    try:
        matched = conn.execute("""SELECT ROUND(amount_sold,8), ROUND(cost_basis,2)
                                  FROM sale_lot_matches WHERE cryptocurrency='USDT'""").fetchall()
    finally:
        conn.close()

    assert matched == [(500.0, 100.0)], \
        'the slice of the first ETH lot consumed must be the first lot of USDT'


def test_an_asset_that_inherits_and_is_then_swapped_on_passes_the_original_date(
        db_path, monkeypatch):
    """Two hops: BTC to ETH to USDT, with ETH holding a lot of its own as well.

    The only case where the give side itself inserts lots mid-list, so it is
    the only case that can prove the other half of the key-not-index change:
    the transfers ETH writes must point at the lots ETH actually consumed,
    which are no longer at the positions they were appended to. It is also
    what the deferral is for — the 2019 acquisition date has to survive both
    swaps, or the exempt/taxable split two hops out is computed from the wrong
    clock.
    """
    conn = sqlite3.connect(db_path)
    try:
        _tx(conn, transaction_date='2019-05-05T10:00:00+00:00', transaction_type='BUY',
            cryptocurrency='BTC', amount=0.5, price_per_unit=2000.0, total_value=1000.0,
            fee_currency='EUR', currency='EUR', exchange_name='Test', source='seed',
            imported_at='2026-01-01T00:00:00', record_hash='c1')
        # ETH lot of its own, newer than the BTC the first swap hands over
        _tx(conn, transaction_date='2021-07-07T10:00:00+00:00', transaction_type='BUY',
            cryptocurrency='ETH', amount=2.0, price_per_unit=50.0, total_value=100.0,
            fee_currency='EUR', currency='EUR', exchange_name='Test', source='seed',
            imported_at='2026-01-01T00:00:00', record_hash='c2')
        _tx(conn, transaction_date='2023-02-02T10:00:00+00:00', transaction_type='SELL',
            cryptocurrency='BTC', amount=0.5, price_per_unit=4200.0, total_value=2100.0,
            fee_currency='EUR', currency='EUR', exchange_name='Test', source='seed',
            imported_at='2026-01-01T00:00:00', record_hash='c3', swap_group_id='swap-a')
        _tx(conn, transaction_date='2023-02-02T10:00:00+00:00', transaction_type='BUY',
            cryptocurrency='ETH', amount=3.0, price_per_unit=700.0, total_value=2100.0,
            fee_currency='EUR', currency='EUR', exchange_name='Test', source='seed',
            imported_at='2026-01-01T00:00:00', record_hash='c4', swap_group_id='swap-a')
        _tx(conn, transaction_date='2024-08-08T10:00:00+00:00', transaction_type='SELL',
            cryptocurrency='ETH', amount=5.0, price_per_unit=1000.0, total_value=5000.0,
            fee_currency='EUR', currency='EUR', exchange_name='Test', source='seed',
            imported_at='2026-01-01T00:00:00', record_hash='c5', swap_group_id='swap-b')
        _tx(conn, transaction_date='2024-08-08T10:00:00+00:00', transaction_type='BUY',
            cryptocurrency='USDT', amount=5000.0, price_per_unit=1.0, total_value=5000.0,
            fee_currency='EUR', currency='EUR', exchange_name='Test', source='seed',
            imported_at='2026-01-01T00:00:00', record_hash='c6', swap_group_id='swap-b')
    finally:
        conn.close()
    monkeypatch.setattr('calculators.crypto_fifo_tracker.SWAP_REALIZES', False)

    tracker = CryptoFIFOTracker(db_path)
    try:
        tracker.calculate_fifo_lots('BTC')
        tracker.calculate_fifo_lots('ETH')
        tracker.calculate_fifo_lots('USDT')
    finally:
        tracker.close()

    conn = sqlite3.connect(db_path)
    try:
        # the JOIN is the assertion: the lot the transfer names must be the lot
        # whose date and cost the transfer carries
        handed_on = conn.execute("""SELECT t.source_purchase_date, l.purchase_date,
                                           ROUND(t.amount_from,8), ROUND(t.cost_basis,2)
                                    FROM swap_lot_transfers t
                                    JOIN fifo_lots l ON l.id = t.source_fifo_lot_id
                                    WHERE t.from_cryptocurrency='ETH'
                                    ORDER BY t.id""").fetchall()
        usdt = conn.execute("""SELECT purchase_date, ROUND(original_amount,8),
                                      ROUND(cost_basis,2)
                               FROM fifo_lots WHERE cryptocurrency='USDT'
                               ORDER BY purchase_date""").fetchall()
    finally:
        conn.close()

    assert handed_on == [
        ('2019-05-05T10:00:00+00:00', '2019-05-05T10:00:00+00:00', 3.0, 1000.0),
        ('2021-07-07T10:00:00+00:00', '2021-07-07T10:00:00+00:00', 2.0, 100.0),
    ], 'each transfer must name the ETH lot it consumed, not the one at that position'
    assert usdt == [('2019-05-05T10:00:00+00:00', 3000.0, 1000.0),
                    ('2021-07-07T10:00:00+00:00', 2000.0, 100.0)], \
        'the BTC acquisition date of 2019 must survive both swaps'

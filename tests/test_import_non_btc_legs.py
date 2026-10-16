"""Non-BTC legs importer: schema, idempotency, and the BTC ledger it must not touch.

Covers the swap_group_id migration (task 1) and, from task 6, the importer
itself: the source-tag isolation that keeps a re-import from ever deleting a
BTC row written from the same file, the warnings for a withdrawal recorded
as a sale or a zero-proceeds transfer, and idempotency.
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

from importers.import_non_btc_legs import SOURCE_SUFFIX, import_non_btc_legs  # noqa: E402

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


class _FakeRates:
    """No-network rate source for these tests: EUR passes through unchanged.

    Constructing the real `Rates()` would build an `ECBRates` and a
    `CryptoPrices`, which read CSV caches by a path relative to the working
    directory and may reach out to the network when the cache looks stale
    (see import_non_btc_legs.Rates). A unit test must not depend on either,
    which is exactly why `import_non_btc_legs` takes a `rates=` seam — these
    tests use it, which is a required adaptation of the task's own verbatim
    test calls (none of which pass `rates=` as written).

    `crypto_to_eur` uses a fixed rate (2.0) rather than refusing outright:
    the swap_group_id end-to-end test below needs a real crypto-to-crypto
    leg to price, and the fixed rate is enough to prove the two legs link —
    no test here asserts a specific valuation figure from it.
    """

    def fiat_to_eur(self, amount, currency, when):
        if currency != 'EUR':
            raise AssertionError(f'unexpected fiat currency in test fixture: {currency!r}')
        return amount

    def crypto_to_eur(self, coin, amount, when):
        return amount * 2.0


def _seed_btc_row(db_path, source):
    """A BTC row written by another importer, from the same file."""
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("""
            INSERT INTO transactions (transaction_date, transaction_type, exchange_name,
                cryptocurrency, amount, price_per_unit, total_value, fee_amount,
                fee_currency, currency, source, imported_at, record_hash)
            VALUES ('2024-02-01T09:00:00+00:00','SELL','Bybit','BTC',0.010000,50000.00,
                    500.00,0,'EUR','EUR',?, '2026-01-01T00:00:00', 'hash-btc-1')
        """, (source,))
        conn.commit()
    finally:
        conn.close()


DEPOSIT_WITHDRAWAL = """UID: 123,Company Name: ,Country:
Uid,Date & Time(UTC),Coin,QTY,Type,Account Balance,Description
123,2024-03-05 10:00:00,USDC,10700.000000,Fiat,10700.000000,Convert
123,2024-03-05 09:59:50,EUR,-10000.000000,Fiat,0.000000,Convert
"""


def test_import_writes_legs_without_touching_btc_rows(db_path, tmp_path, monkeypatch):
    """The regression that matters: the #non-btc tag must not delete BTC rows."""
    export = tmp_path / 'bybit_dw.csv'
    export.write_text(DEPOSIT_WITHDRAWAL)
    _seed_btc_row(db_path, export.name)

    inserted = import_non_btc_legs(str(export), exchange_name='Bybit',
                                    shape='deposit_withdrawal', db_path=db_path,
                                    rates=_FakeRates())

    assert inserted == 1
    conn = sqlite3.connect(db_path)
    try:
        btc = conn.execute("SELECT COUNT(*) FROM transactions WHERE cryptocurrency='BTC'").fetchone()[0]
        legs = conn.execute("SELECT cryptocurrency, transaction_type, total_value, source "
                             "FROM transactions WHERE cryptocurrency='USDC'").fetchall()
    finally:
        conn.close()

    assert btc == 1, 'the BTC row of the same file was deleted'
    assert legs == [('USDC', 'BUY', 10000.0, export.name + SOURCE_SUFFIX)]


def test_convert_rows_are_listed_for_confirmation(db_path, tmp_path, capsys):
    """A withdrawal recorded as a sale must be announced, never silent."""
    export = tmp_path / 'account_history.csv'
    export.write_text(
        'ID,Timestamp,Transaction Type,Asset,Quantity Transacted,Price Currency,'
        'Price at Transaction,Subtotal,Total (inclusive of fees and/or spread),Fees and/or Spread\n'
        'w1,2024-02-21 08:00:00 UTC,Withdrawal,USDC,-50000.0,EUR,€0.92,€46500.0,€46500.0,€0.00\n'
    )

    import_non_btc_legs(str(export), exchange_name='Coinbase',
                         shape='account_history', db_path=db_path, rates=_FakeRates())

    err = capsys.readouterr().err
    assert 'recorded as a SALE' in err
    assert '50000.00000000 USDC' in err


def test_zero_proceeds_withdrawals_are_listed_too(db_path, tmp_path, capsys):
    """The transfer side of the fork gets its own list, honestly scoped.

    It lists the whole TRANSFER_OUT class, not just the doubtful rows —
    the ambiguity (which export label produced this row) is not carried in
    the normalized row, so the warning cannot claim to have shortlisted
    anything. The wording must say that plainly rather than imply a
    filtered-down set of suspects.
    """
    export = tmp_path / 'account_history.csv'
    export.write_text(
        'ID,Timestamp,Transaction Type,Asset,Quantity Transacted,Price Currency,'
        'Price at Transaction,Subtotal,Total (inclusive of fees and/or spread),Fees and/or Spread\n'
        'w2,2024-02-22 08:00:00 UTC,Withdrawal,USDC,-25.0,EUR,€0.00,€0.00,€0.00,€0.00\n'
    )

    import_non_btc_legs(str(export), exchange_name='Coinbase',
                         shape='account_history', db_path=db_path, rates=_FakeRates())

    err = capsys.readouterr().err
    assert 'recorded as non-taxable' in err
    assert '25.00000000 USDC' in err


def test_import_is_idempotent(db_path, tmp_path):
    export = tmp_path / 'bybit_dw.csv'
    export.write_text(DEPOSIT_WITHDRAWAL)

    import_non_btc_legs(str(export), 'Bybit', 'deposit_withdrawal', db_path, rates=_FakeRates())
    first = sqlite3.connect(db_path).execute(
        'SELECT record_hash FROM transactions ORDER BY id').fetchall()
    import_non_btc_legs(str(export), 'Bybit', 'deposit_withdrawal', db_path, rates=_FakeRates())
    second = sqlite3.connect(db_path).execute(
        'SELECT record_hash FROM transactions ORDER BY id').fetchall()

    assert first == second and len(first) == 1


def test_failed_row_names_the_row_and_leaves_nothing_written(db_path, tmp_path):
    """The property that matters most: a mid-import failure must not touch
    the database at all — not the BTC row from another importer, not a
    non-BTC leg a *previous*, successful run of this importer already wrote
    from the same file. value_row is walked over every row before
    import_and_verify (and therefore before any delete) is ever called, so
    a later row's failure must propagate before a single statement runs.

    Also covers the companion fix: `_apply_fee`'s own message ("fee charged
    in BNB, which is neither leg...") names only amounts and symbols, which
    does not localize a row on a real export where several rows share a fee
    amount. The index and the row's own date/kind must be in the raised
    message too.
    """
    export = tmp_path / 'trade_history.csv'
    export.write_text(
        'Date(UTC),Pair,Side,Executed,Amount,Fee,Order No.\n'
        '2024-04-01 09:00:00,USDCEUR,BUY,1000.000000,930.000000,0,o1\n'
    )
    _seed_btc_row(db_path, export.name)
    inserted = import_non_btc_legs(str(export), 'Bybit', 'trade_history', db_path,
                                    rates=_FakeRates())
    assert inserted == 1

    conn = sqlite3.connect(db_path)
    try:
        before_btc = conn.execute(
            "SELECT record_hash FROM transactions WHERE cryptocurrency='BTC'").fetchall()
        before_legs = conn.execute(
            "SELECT record_hash FROM transactions WHERE cryptocurrency!='BTC' "
            "ORDER BY id").fetchall()
    finally:
        conn.close()
    assert before_btc and before_legs  # sanity: both kinds of row exist before the failing call

    # Re-import the same file, now with a second row whose fee is in a third
    # asset (BNB) — _apply_fee refuses it. The first row's amount is changed
    # (930 -> 931) so that if it were ever re-inserted before the second row
    # is reached, the hash comparison below would actually notice — a row
    # that happened to come out byte-identical to the one it replaced would
    # hide a partial-write bug behind an unchanged hash.
    export.write_text(
        'Date(UTC),Pair,Side,Executed,Amount,Fee,Order No.\n'
        '2024-04-01 09:00:00,USDCEUR,BUY,1000.000000,931.000000,0,o1\n'
        '2024-04-02 11:00:00,BTCUSDT,SELL,2.000000,120000.000000,0.5BNB,o2\n'
    )

    with pytest.raises(ValueError) as exc_info:
        import_non_btc_legs(str(export), 'Bybit', 'trade_history', db_path, rates=_FakeRates())
    message = str(exc_info.value)
    assert 'row 1' in message
    assert '2024-04-02' in message
    assert 'TRADE' in message
    assert 'neither leg' in message  # the wrapped message, not swallowed

    conn = sqlite3.connect(db_path)
    try:
        after_btc = conn.execute(
            "SELECT record_hash FROM transactions WHERE cryptocurrency='BTC'").fetchall()
        after_legs = conn.execute(
            "SELECT record_hash FROM transactions WHERE cryptocurrency!='BTC' "
            "ORDER BY id").fetchall()
    finally:
        conn.close()

    assert after_btc == before_btc, 'the BTC row changed after a failed non-btc import'
    assert after_legs == before_legs, 'the previous successful leg changed after a failed import'


def test_crypto_to_crypto_swap_round_trips_swap_group_id(db_path, tmp_path):
    """Both legs of a non-BTC swap must land in the DB sharing one swap_group_id.

    Column existence is the migration test's job; the id's shape (stable,
    collision-resistant) is value_row's own unit tests. Neither proves that
    an actual end-to-end import — through a real reader, through this
    importer's INSERT — ever puts a non-null, shared id into the database.
    Tasks 9-11 are keyed on exactly this column, so this is the missing
    middle: read back out of the database, not just asserted on the
    in-memory dicts value_row returns.
    """
    export = tmp_path / 'trade_history.csv'
    export.write_text(
        'Date(UTC),Pair,Side,Executed,Amount,Fee,Order No.\n'
        '2024-05-01 09:00:00,USDCUSDT,SELL,1000.000000,999.000000,0,o1\n'
    )

    inserted = import_non_btc_legs(str(export), 'Bybit', 'trade_history', db_path,
                                    rates=_FakeRates())

    assert inserted == 2
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            "SELECT cryptocurrency, transaction_type, swap_group_id FROM transactions "
            "ORDER BY cryptocurrency").fetchall()
    finally:
        conn.close()

    assert [r[0] for r in rows] == ['USDC', 'USDT']
    assert [r[1] for r in rows] == ['SELL', 'BUY']
    assert rows[0][2] is not None
    assert rows[0][2] == rows[1][2]


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

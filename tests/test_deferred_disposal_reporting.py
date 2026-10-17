"""What the reports say when a disposal defers instead of realizing.

`sale_lot_matches` is the register of *realized* gains, not the register of
disposals: under `tax_rules.swap_realizes = False` the SELL leg of a swap
consumes lots and writes no match row. Every report that reads that table as
"the disposals" is short by exactly those rows, while every report that reads
`transactions` counts them at full value.

These tests pin the two places where both kinds of figure are printed side by
side, and the single definition of "a disposal that deferred" they share.
Nothing here asserts that a figure changed — the figures must not move; what
must change is that the output says the two sections do not reconcile.
"""
import io
import os
import re
import sqlite3
import sys
from contextlib import redirect_stdout

PROJECT_ROOT = os.path.join(os.path.dirname(__file__), '..')
SCHEMA_FILE = os.path.join(PROJECT_ROOT, 'doc', 'schema.sql')
sys.path.insert(0, PROJECT_ROOT)

import pytest  # noqa: E402

from calculators.crypto_fifo_tracker import CryptoFIFOTracker  # noqa: E402
from calculators.swap_reporting import (  # noqa: E402
    deferred_disposals, inherited_lots, realized_swap_disposals,
)

DRIVER = os.path.join(PROJECT_ROOT, 'calculators', 'calculate_fifo.py')
CLAUDE_MD = os.path.join(PROJECT_ROOT, 'CLAUDE.md')

_TX_COLS = ('transaction_date', 'transaction_type', 'cryptocurrency', 'amount',
            'price_per_unit', 'total_value', 'fee_amount', 'fee_currency', 'currency',
            'exchange_name', 'source', 'imported_at', 'record_hash', 'swap_group_id')


def _tx(conn, **kw):
    """Insert one transaction; returns its id."""
    row = {c: kw.get(c) for c in _TX_COLS}
    row['fee_amount'] = row['fee_amount'] or 0
    row.setdefault('fee_currency', 'EUR')
    cur = conn.execute(
        f"INSERT INTO transactions ({','.join(_TX_COLS)}) "
        f"VALUES ({','.join('?' * len(_TX_COLS))})",
        tuple(row[c] for c in _TX_COLS))
    conn.commit()
    return cur.lastrowid


def _seed_one_plain_and_one_swap_sale(db_path):
    """2 ETH bought at EUR 1000 each; one sold for EUR 3000, one swapped for USDT.

    The plain SELL carries no `swap_group_id`, so it realizes under either
    value of the rule — which is what makes the two sections comparable: the
    difference between them is exactly the swap.
    """
    conn = sqlite3.connect(db_path)
    try:
        _tx(conn, transaction_date='2020-01-10T10:00:00+00:00', transaction_type='BUY',
            cryptocurrency='ETH', amount=2.0, price_per_unit=1000.0, total_value=2000.0,
            fee_currency='EUR', currency='EUR', exchange_name='Test',
            source='seed', imported_at='2026-01-01T00:00:00', record_hash='h1')
        _tx(conn, transaction_date='2024-04-22T15:00:00+00:00', transaction_type='SELL',
            cryptocurrency='ETH', amount=1.0, price_per_unit=3000.0, total_value=3000.0,
            fee_currency='EUR', currency='EUR', exchange_name='Test',
            source='seed', imported_at='2026-01-01T00:00:00', record_hash='h2')
        _tx(conn, transaction_date='2024-06-15T15:00:00+00:00', transaction_type='SELL',
            cryptocurrency='ETH', amount=1.0, price_per_unit=3200.0, total_value=3200.0,
            fee_currency='EUR', currency='EUR', exchange_name='Test',
            source='seed', imported_at='2026-01-01T00:00:00', record_hash='h3',
            swap_group_id='swap-1')
        _tx(conn, transaction_date='2024-06-15T15:00:00+00:00', transaction_type='BUY',
            cryptocurrency='USDT', amount=3200.0, price_per_unit=1.0, total_value=3200.0,
            fee_currency='EUR', currency='EUR', exchange_name='Test',
            source='seed', imported_at='2026-01-01T00:00:00', record_hash='h4',
            swap_group_id='swap-1')
    finally:
        conn.close()
    return db_path


def _run_engine(db_path, *cryptos):
    tracker = CryptoFIFOTracker(db_path)
    try:
        for crypto in cryptos:
            tracker.calculate_fifo_lots(crypto)
    finally:
        tracker.close()


def _deferred_db(db_path, monkeypatch):
    """The seed, calculated under deferral: 1 realized match, 1 transfer."""
    _seed_one_plain_and_one_swap_sale(db_path)
    monkeypatch.setattr('calculators.crypto_fifo_tracker.SWAP_REALIZES', False)
    _run_engine(db_path, 'ETH', 'USDT')  # give side before take side
    return db_path


def _realizing_db(db_path):
    """The same seed under the declared rule: 2 realized matches, no transfer."""
    _seed_one_plain_and_one_swap_sale(db_path)
    _run_engine(db_path, 'ETH', 'USDT')
    return db_path


# doc/schema.sql with the swap_group_id column and its index stripped out —
# the one shape tests/conftest.py's db_path fixture cannot give us, because it
# builds from the real schema, which has the column. Same patterns and the same
# loud failure as tests/test_import_non_btc_legs.py:42-90: if either stops
# matching (a reformat of the schema, say) this raises instead of quietly
# handing back a schema that still has the column, which would turn the test
# below back into the false positive it exists to replace.
_COLUMN_BLOCK = re.compile(
    r"    record_hash TEXT,(?P<comment>[^\n]*)\n"
    r"    swap_group_id TEXT[^\n]*\n"
)
_INDEX_LINE = re.compile(
    r"CREATE INDEX IF NOT EXISTS idx_transactions_swap_group "
    r"ON transactions\(swap_group_id\);\n"
)


@pytest.fixture
def pre_swap_column_db(tmp_path):
    with open(SCHEMA_FILE) as f:
        schema = f.read()

    schema, n = _COLUMN_BLOCK.subn(r"    record_hash TEXT\g<comment>\n", schema)
    assert n == 1, (
        'Could not find the swap_group_id column line in doc/schema.sql — update '
        '_COLUMN_BLOCK to match the current schema; otherwise this fixture '
        'silently stops exercising the missing-column path.'
    )
    schema, n = _INDEX_LINE.subn('', schema)
    assert n == 1, (
        'Could not find the idx_transactions_swap_group index line in '
        'doc/schema.sql — update _INDEX_LINE to match the current schema.'
    )

    path = str(tmp_path / 'pre_swap_column.db')
    conn = sqlite3.connect(path)
    try:
        conn.executescript(schema)
        conn.commit()
    finally:
        conn.close()
    return path


def _connect(db_path):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


# ---- the shared definition: calculators/swap_reporting.py ----

def test_a_database_with_no_disposal_at_all_reports_nothing_deferred(db_path):
    conn = sqlite3.connect(db_path)
    try:
        assert deferred_disposals(conn) == {'count': 0, 'amount': 0.0, 'total_value': 0.0}
    finally:
        conn.close()


def test_a_deferred_swap_disposal_is_counted_with_its_quantity_and_value(db_path, monkeypatch):
    _deferred_db(db_path, monkeypatch)

    conn = _connect(db_path)
    try:
        result = deferred_disposals(conn)
    finally:
        conn.close()

    assert result['count'] == 1, 'the swap SELL consumed lots and wrote no match row'
    assert result['amount'] == 1.0
    assert result['total_value'] == 3200.0, 'the value transactions carries, as VENDITE reads it'


def test_a_plain_disposal_is_never_counted_as_deferred(db_path, monkeypatch):
    """The plain SELL of the same asset in the same year must not be swept in."""
    _deferred_db(db_path, monkeypatch)

    conn = _connect(db_path)
    try:
        result = deferred_disposals(conn)
        realized = conn.execute('SELECT COUNT(*) FROM sale_lot_matches').fetchone()[0]
    finally:
        conn.close()

    assert realized == 1, 'the plain SELL must still realize under deferral'
    assert result['count'] == 1, 'only the swap disposal defers, not the plain one'
    assert result['total_value'] == 3200.0, 'the plain SELL (EUR 3000) must not be added in'


def test_a_swap_that_realized_is_not_counted_as_deferred(db_path):
    """Same swap, same `swap_group_id`, calculated under the declared rule.

    This is the mutation that `swap_group_id IS NOT NULL` alone would pass:
    the column says a row is a swap leg, not that it deferred.
    """
    _realizing_db(db_path)

    conn = _connect(db_path)
    try:
        result = deferred_disposals(conn)
        realized = conn.execute('SELECT COUNT(*) FROM sale_lot_matches').fetchone()[0]
        transfers = conn.execute('SELECT COUNT(*) FROM swap_lot_transfers').fetchone()[0]
    finally:
        conn.close()

    assert realized == 2, 'under the declared rule both disposals realize'
    assert transfers == 0
    assert result['count'] == 0, 'a swap that realized has nothing missing from the register'


def test_a_database_whose_fifo_was_never_run_is_not_a_deferral_regime(db_path):
    """The reason the definition needs positive evidence, not a missing row.

    Before any FIFO run `sale_lot_matches` is empty, so "a swap SELL with no
    match row" is true of every swap leg in the database. Counting that would
    tell the reader of an uncalculated database that its figures exclude
    deferred disposals, which is a different and false explanation of why they
    are missing.
    """
    _seed_one_plain_and_one_swap_sale(db_path)  # no engine run

    conn = _connect(db_path)
    try:
        result = deferred_disposals(conn)
    finally:
        conn.close()

    assert result['count'] == 0, (
        'with no fifo_lots and no swap_lot_transfers nothing has deferred — '
        'the FIFO simply has not run'
    )


def test_a_disposal_the_register_does_carry_is_not_reported_as_missing_from_it(db_path, monkeypatch):
    """Separates the two halves of the definition, which the engine keeps apart.

    `test_a_swap_that_realized_is_not_counted_as_deferred` above cannot do it:
    there the engine ran under the declared rule, so `swap_lot_transfers` is
    empty and the positive-evidence half alone already excludes the row. Only a
    database carrying **both** a transfer and a match for the same disposal
    tells the two halves apart — and without the `NOT EXISTS` half the function
    would report, as absent from the FIFO section, a disposal that section
    shows.

    The engine does not produce this state: its per-crypto DELETE clears the
    transfers of the asset given up on every pass, under either rule. It is
    reachable by the operational practice `doc/en/audit_trail_guide.md`
    recommends — copying database files around before and after a critical
    operation — which can land a `sale_lot_matches` from one run next to a
    `swap_lot_transfers` from another. Defensive, not a live defect; built by
    hand here for that reason.
    """
    _deferred_db(db_path, monkeypatch)

    conn = _connect(db_path)
    try:
        sale_id, lot_id = conn.execute(
            "SELECT t.id, l.id FROM transactions t, fifo_lots l "
            "WHERE t.swap_group_id = 'swap-1' AND t.transaction_type = 'SELL' "
            "AND l.cryptocurrency = 'ETH'").fetchone()
        conn.execute("""INSERT INTO sale_lot_matches
            (sale_transaction_id, fifo_lot_id, sale_date, purchase_date, cryptocurrency,
             amount_sold, purchase_price_per_unit, sale_price_per_unit, cost_basis,
             proceeds, gain_loss, holding_period_days)
            VALUES (?, ?, '2024-06-15T15:00:00+00:00', '2020-01-10T10:00:00+00:00',
                    'ETH', 1.0, 1000.0, 3200.0, 1000.0, 3200.0, 2200.0, 1618)""",
            (sale_id, lot_id))
        conn.commit()
        assert conn.execute(
            'SELECT COUNT(*) FROM swap_lot_transfers').fetchone()[0] == 1, \
            'the transfer must still be there, or this test proves nothing'
        result = deferred_disposals(conn)
    finally:
        conn.close()

    assert result['count'] == 0, (
        'the disposal now has a row in sale_lot_matches: whatever the transfers '
        'say, it is not missing from the register a report reads'
    )


def test_the_count_can_be_narrowed_to_one_cryptocurrency(db_path, monkeypatch):
    _deferred_db(db_path, monkeypatch)

    conn = _connect(db_path)
    try:
        assert deferred_disposals(conn, cryptocurrency='ETH')['count'] == 1
        assert deferred_disposals(conn, cryptocurrency='USDT')['count'] == 0, \
            'USDT is the asset received: it disposed of nothing'
    finally:
        conn.close()


def test_the_count_can_be_narrowed_to_one_year(db_path, monkeypatch):
    _deferred_db(db_path, monkeypatch)

    conn = _connect(db_path)
    try:
        assert deferred_disposals(conn, year=2024)['count'] == 1
        assert deferred_disposals(conn, year=2023)['count'] == 0
        assert deferred_disposals(conn, year=2025)['count'] == 0
    finally:
        conn.close()


def test_a_database_without_the_transfers_table_answers_zero_instead_of_raising(db_path):
    """Reports run on databases older than the migrations; they must still print.

    Zero is not a swallowed error here: without `swap_lot_transfers` no run
    under deferral was ever possible — the engine refuses that combination — so
    no disposal can have deferred.

    This covers the TABLE only. The `swap_group_id` column has its own test
    below, with its own fixture: the docstring here used to claim both while
    dropping only the table, which left the column guard with no test at all.
    """
    conn = sqlite3.connect(db_path)
    try:
        conn.execute('DROP TABLE swap_lot_transfers')
        conn.commit()
        assert deferred_disposals(conn)['count'] == 0
    finally:
        conn.close()


def test_a_database_without_the_swap_group_column_answers_zero_for_every_evidence(pre_swap_column_db):
    """The guard that had no test, and the failure it prevents.

    Without `transactions.swap_group_id` a row cannot be identified as a swap
    leg at all, so zero is the true answer. Unguarded, `realized_swap_disposals`
    has no table check in front of it, so the banner raises
    `sqlite3.OperationalError` at the *end* of generate_annual_summary.py —
    after every section has already printed — turning a report into a traceback
    on its last line.

    Every other database in this file is built from the real doc/schema.sql,
    which has the column, so no other test here can reach this.
    """
    conn = sqlite3.connect(pre_swap_column_db)
    try:
        assert 'swap_group_id' not in [
            r[1] for r in conn.execute('PRAGMA table_info(transactions)')
        ], 'the fixture must actually be missing the column'
        assert deferred_disposals(conn)['count'] == 0
        assert realized_swap_disposals(conn)['count'] == 0
        assert inherited_lots(conn)['count'] == 0
        assert config.tax_rule_banner(conn) == TODAYS_LINE, \
            'and the banner still prints, rather than raising on its own line'
    finally:
        conn.close()


def test_a_database_without_sale_lot_matches_answers_zero_for_both_disposal_evidences(db_path):
    """Both disposal queries read that table; without it they would raise.

    A report would then die at its *start* rather than halfway, which is less
    dangerous and still wrong: zero is the true answer, because a database with
    no register of realized gains has neither a realized nor a deferred
    disposal on record.
    """
    conn = sqlite3.connect(db_path)
    try:
        conn.execute('DROP TABLE sale_lot_matches')
        conn.commit()
        assert deferred_disposals(conn)['count'] == 0
        assert realized_swap_disposals(conn)['count'] == 0
        assert config.tax_rule_banner(conn) == TODAYS_LINE
    finally:
        conn.close()


def test_the_disposal_evidences_only_look_at_sell_legs(db_path, monkeypatch):
    """Keeps `transaction_type = 'SELL'` from becoming load-bearing by accident.

    Adding `s.from_cryptocurrency = t.cryptocurrency` to the deferred evidence
    made the SELL filter redundant for every state the engine produces — the
    BUY leg of a swap is the asset *received*, which is never the asset a
    transfer came from. Measured: without this test, deleting the SELL filter
    broke nothing.

    It is kept because the function counts *disposals*, and the clause is what
    makes the query say that. The state where it still bites is a group whose
    BUY leg is in the same asset as its SELL leg: that leg would then be
    counted as a disposal that deferred, and its `amount` and `total_value`
    added to the EUR figure a report prints. Hand-built — the importer cannot
    make a group give up and receive the same asset, and the engine's
    multiple-BUY-leg guard would refuse this database before calculating it.
    This query is not the engine, and a report runs on what is on record.
    """
    _deferred_db(db_path, monkeypatch)

    conn = _connect(db_path)
    try:
        _tx(conn, transaction_date='2024-06-15T15:00:00+00:00', transaction_type='BUY',
            cryptocurrency='ETH', amount=1.0, price_per_unit=3200.0, total_value=3200.0,
            fee_currency='EUR', currency='EUR', exchange_name='Test', source='seed',
            imported_at='2026-01-01T00:00:00', record_hash='h8', swap_group_id='swap-1')
        result = deferred_disposals(conn)
    finally:
        conn.close()

    assert result['count'] == 1, 'a BUY leg is an acquisition, never a disposal'
    assert result['total_value'] == 3200.0, \
        'and its value must not be added to the proceeds a report reconciles'


def test_the_inherited_evidence_only_looks_at_lots_acquired_by_a_buy(db_path):
    """Defensive, and marked as such — it has no live path.

    `fifo_lots.purchase_transaction_id` points at a BUY by construction: the
    engine builds lots in its BUY branches only. Landing on a SELL that carries
    a `swap_group_id` and a different date would count as an inherited lot and
    raise a warning with nothing behind it.

    What can put it there is a pointer that resolves to a *different* row.
    Deleting and re-importing a transaction is **not** one of those: the ids are
    `AUTOINCREMENT`, which is precisely what stops them being reused, so a lot
    left behind points at a row that is absent and this query's INNER JOIN drops
    it — that is the orphan-lot case in doc/TODO.md, which has been observed on
    the real database, and it is a different defect. The paths that do land on a
    different row are a table rebuilt with renumbering, and the file-copying
    practice `doc/en/audit_trail_guide.md` recommends: a `fifo_lots` from one
    database alongside a `transactions` from another.

    A false warning is not a harmless warning here: this banner is a fiscal
    safety device, and one that cries wolf is one a reader learns to scroll
    past.
    """
    conn = _connect(db_path)
    try:
        sell_id = _tx(conn, transaction_date='2024-06-15T15:00:00+00:00',
                      transaction_type='SELL', cryptocurrency='ETH', amount=1.0,
                      price_per_unit=3200.0, total_value=3200.0, fee_currency='EUR',
                      currency='EUR', exchange_name='Test', source='seed',
                      imported_at='2026-01-01T00:00:00', record_hash='s1',
                      swap_group_id='swap-stale')
        # A lot pointing at that SELL, with a date that differs from it.
        conn.execute("""INSERT INTO fifo_lots
            (purchase_transaction_id, cryptocurrency, purchase_date, original_amount,
             remaining_amount, purchase_price_per_unit, cost_basis, purchase_fee_total,
             exchange_name)
            VALUES (?, 'ETH', '2020-01-10T10:00:00+00:00', 1.0, 1.0, 1000.0, 1000.0, 0, 'Test')""",
            (sell_id,))
        conn.commit()
        result = inherited_lots(conn)
        banner = config.tax_rule_banner(conn)
    finally:
        conn.close()

    assert result['count'] == 0, \
        'a lot whose acquisition row is a SELL is a broken pointer, not an inheritance'
    assert banner == TODAYS_LINE, 'and it must not raise a warning with nothing behind it'


def test_the_inherited_evidence_only_looks_at_lots_acquired_in_a_swap(db_path):
    """The mirror of the test above, on the other filter of the same query.

    A lot whose acquisition is an ORDINARY BUY — no `swap_group_id` — with a
    `purchase_date` that does not match it. Nothing inherited anything here: no
    swap is involved at all. Without the filter the count is one and the banner
    shouts on a database where no disposal has ever deferred, which is a warning
    with nothing behind it on a device whose whole value is that it only fires
    when it means something.

    Same reachability as the sibling test: a `fifo_lots` and a `transactions`
    that do not come from the same database, which the file-copying practice in
    `doc/en/audit_trail_guide.md` makes easy to produce.
    """
    conn = _connect(db_path)
    try:
        buy_id = _tx(conn, transaction_date='2024-06-15T15:00:00+00:00',
                     transaction_type='BUY', cryptocurrency='ETH', amount=1.0,
                     price_per_unit=3200.0, total_value=3200.0, fee_currency='EUR',
                     currency='EUR', exchange_name='Test', source='seed',
                     imported_at='2026-01-01T00:00:00', record_hash='o1')
        assert conn.execute(
            'SELECT swap_group_id FROM transactions WHERE id = ?', (buy_id,)
        ).fetchone()[0] is None, 'an ordinary BUY, with no swap group'
        conn.execute("""INSERT INTO fifo_lots
            (purchase_transaction_id, cryptocurrency, purchase_date, original_amount,
             remaining_amount, purchase_price_per_unit, cost_basis, purchase_fee_total,
             exchange_name)
            VALUES (?, 'ETH', '2020-01-10T10:00:00+00:00', 1.0, 1.0, 1000.0, 1000.0, 0, 'Test')""",
            (buy_id,))
        conn.commit()
        result = inherited_lots(conn)
        banner = config.tax_rule_banner(conn)
    finally:
        conn.close()

    assert result['count'] == 0, \
        'no swap, nothing inherited: a mismatched date on an ordinary BUY is a ' \
        'different defect and belongs to the stale-lot entry in doc/TODO.md'
    assert banner == TODAYS_LINE, \
        'and the banner must not shout about deferral on a database that never deferred'


def test_a_database_without_fifo_lots_answers_zero_for_the_inherited_evidence(db_path):
    """The third evidence's own prerequisite, in the shape of the other two.

    `fifo_lots` is where an inherited lot lives, so without that table none can
    be on record and zero is the true answer. Unguarded the banner would raise
    instead of printing — the same failure the other guards prevent, on the
    third query.
    """
    conn = sqlite3.connect(db_path)
    try:
        conn.execute('DROP TABLE fifo_lots')
        conn.commit()
        assert inherited_lots(conn)['count'] == 0
        assert config.tax_rule_banner(conn) == TODAYS_LINE
    finally:
        conn.close()


def test_the_deferred_evidence_requires_the_transfer_to_come_from_this_asset(db_path, monkeypatch):
    """Defensive, and marked as such.

    `EXISTS (... WHERE s.swap_group_id = t.swap_group_id)` alone does not check
    that the transfer came from the asset *this* leg gave up, so a group with
    two SELL legs in different assets would report a disposal that deferred
    nothing. Not producible by the importer — verified upstream 2026-10-17:
    `non_btc_valuation.value_row` emits exactly one SELL and one BUY per
    normalized row, `row_ref` is unique per row and folded into the group id,
    and no other file writes the column. Built by hand here for that reason.
    """
    _deferred_db(db_path, monkeypatch)

    conn = _connect(db_path)
    try:
        # A second SELL leg in a different asset, sharing the group. Its own
        # asset never deferred: the transfer on record is ETH's.
        _tx(conn, transaction_date='2024-06-15T15:00:00+00:00', transaction_type='SELL',
            cryptocurrency='BTC', amount=0.1, price_per_unit=50000.0, total_value=5000.0,
            fee_currency='EUR', currency='EUR', exchange_name='Test',
            source='seed', imported_at='2026-01-01T00:00:00', record_hash='h9',
            swap_group_id='swap-1')
        assert conn.execute(
            'SELECT from_cryptocurrency FROM swap_lot_transfers'
        ).fetchone()[0] == 'ETH'
        result = deferred_disposals(conn)
        btc_only = deferred_disposals(conn, cryptocurrency='BTC')
    finally:
        conn.close()

    assert btc_only['count'] == 0, \
        'the BTC leg gave up nothing that deferred: the transfer on record is ETH-side'
    assert result['count'] == 1, 'only the ETH leg actually deferred'
    assert result['total_value'] == 3200.0, 'and the BTC leg must not be added in'


# ---- report 1: calculators/generate_annual_summary.py ----

def _annual_summary(db_path, year=2024):
    from calculators.generate_annual_summary import main
    buf = io.StringIO()
    argv = sys.argv
    sys.argv = ['generate_annual_summary.py', str(year), db_path]
    try:
        with redirect_stdout(buf):
            main()
    finally:
        sys.argv = argv
    return buf.getvalue()


HEADINGS = ['ACQUISTI', 'VENDITE', 'RISULTATO FIFO', 'DETTAGLIO PER EXCHANGE',
            'HOLDINGS RESIDUI', 'RIEPILOGO FINALE']


def _heading_at(output, title):
    """Offset of a section heading *line*, not of a mention of its name.

    Anchored on the two-space indentation the headings are printed with: the
    notes this change adds name the other sections inside their own text, at a
    deeper indent, and a plain `str.index` would cut a section short there.
    """
    m = re.search(r'(?m)^  ' + re.escape(title) + r'\b', output)
    assert m, f'section {title!r} not printed'
    return m.start()


def _section(output, title):
    """The text between one section heading and the next."""
    start = _heading_at(output, title)
    ends = [m.start() for h in HEADINGS
            for m in re.finditer(r'(?m)^  ' + re.escape(h) + r'\b', output)
            if m.start() > start]
    return output[start:min(ends)] if ends else output[start:]


def test_the_annual_summary_says_the_two_sections_do_not_reconcile(db_path, monkeypatch):
    """The worst of the thirteen: VENDITE counts the swap, RISULTATO FIFO does not.

    Both numbers are right for what they measure. Printed next to each other
    with no note, they tell a reader reconciling them that the FIFO lost a
    sale — the plausible wrong conclusion this whole branch exists to prevent.
    """
    _deferred_db(db_path, monkeypatch)

    output = _annual_summary(db_path)

    sales = _section(output, 'VENDITE')
    fifo = _section(output, 'RISULTATO FIFO')

    assert 'Permute differite comprese in questa sezione: 1' in sales, (
        'VENDITE reads transactions and counts the deferred disposal: it must say so'
    )
    assert 'Permute differite NON comprese in questa sezione: 1' in fifo, (
        'RISULTATO FIFO reads sale_lot_matches and excludes it: it must say so'
    )
    assert 'Permute differite escluse dal risultato FIFO: 1' in _section(output, 'RIEPILOGO FINALE'), (
        'a reader who only reads the totals must see it too'
    )
    assert 'swap_lot_transfers' in fifo, \
        'the note must say where the cost basis went, not only that it is absent'


def test_the_annual_summary_moves_no_figure(db_path, monkeypatch):
    """The note is the whole change. Every number stays what it was.

    VENDITE: both disposals, 2.00000000 ETH, EUR 6,200.00 of proceeds.
    RISULTATO FIFO: one match, 1.00000000 ETH, EUR 3,000.00 - 1,000.00 = 2,000.00.
    """
    _deferred_db(db_path, monkeypatch)

    sales = _section(_annual_summary(db_path), 'VENDITE')
    fifo = _section(_annual_summary(db_path), 'RISULTATO FIFO')

    assert re.search(r'Operazioni:\s+2\b', sales), 'VENDITE still counts both disposals'
    assert '6,200.00' in sales, 'VENDITE still totals the swap at its full value'
    assert re.search(r'Match FIFO:\s+1\b', fifo), 'RISULTATO FIFO still has one match'
    assert '2,000.00' in fifo, 'the realized gain must not move'


def test_the_annual_summary_stays_silent_when_nothing_deferred(db_path):
    """A note that fires on every run is a note nobody reads."""
    _realizing_db(db_path)

    output = _annual_summary(db_path)

    assert 'Permute differite' not in output, \
        'under the declared rule the two sections reconcile: no note is due'
    assert re.search(r'Match FIFO:\s+2\b', _section(output, 'RISULTATO FIFO')), \
        'and both disposals realize'


# ---- report 2: the "Sales matched" line of calculators/calculate_fifo.py ----

def _read_driver():
    with open(DRIVER) as f:
        return f.read()


def test_the_driver_reports_deferred_disposals_outside_the_sales_matched_guard():
    """Pins wiring no in-process test can reach, and the one placement that matters.

    `main()` is not drivable from a test: the driver imports the engine as a
    top-level module while the tests import `calculators.crypto_fifo_tracker`,
    so the two hold separate SWAP_REALIZES and patching one does not reach the
    other (recorded in doc/TODO.md from the task 9 review), and no profile in
    config.py declares the rule False. The definition the driver calls is
    tested for real above; what is left here is where it is called.

    The placement is the defect, not a detail: `if stats['sales_matched']:`
    prints nothing at all when every disposal of a cryptocurrency deferred,
    which is exactly the case the reader needs explained.
    """
    source = _read_driver()

    assert 'from calculators.swap_reporting import deferred_disposals' in source, \
        'the driver must use the shared definition, not its own copy of the query'

    call = "deferred = deferred_disposals(tracker.conn, cryptocurrency=crypto)"
    assert call in source, 'the driver must ask per cryptocurrency, as it reports per cryptocurrency'

    guard = "if stats['sales_matched']:"
    note = "if deferred['count']:"
    assert note in source, 'the driver must print the gap when there is one'
    assert source.index(guard) < source.index(note), (
        "the deferred note must come after the statistics block, not inside it"
    )
    # Indentation is the assertion: the per-crypto loop body sits at 12
    # spaces, the sales_matched block's body at 16. A note nested in that
    # block is unreachable for a cryptocurrency whose every disposal deferred,
    # which is the case it exists to explain.
    assert re.search(r'\n            if deferred\[.count.\]:', source), (
        'the note must sit at the per-crypto loop body indentation (12 spaces), '
        'not nested inside the sales_matched block (16)'
    )


# ---- the invariant CLAUDE.md states ----

def test_claude_md_does_not_state_the_invariant_deferral_breaks():
    """"one row per (SELL x consumed lot)" is false once a disposal can defer.

    An unqualified invariant in the file every agent reads first is worse than
    no invariant: it is the sentence an agent would trust instead of checking.
    """
    with open(CLAUDE_MD) as f:
        text = f.read()

    assert 'one row per (SELL × consumed lot)' not in text, \
        'a deferred swap SELL consumes lots and writes no row: the invariant must be qualified'
    assert '(one per consumed lot)' not in text, \
        'the architecture diagram states the same invariant and must be qualified too'
    assert 'swap_lot_transfers' in text, \
        'the table a deferred disposal writes instead must be named where the schema is described'


# ---- report 3: the banner that heads all three reports ----
#
# config.tax_rule_banner() named "the tax rule that produced the figures just
# printed". True for calculate_fifo.py, which prints it right after
# calculating; false for the two reports that read figures out of the
# database, where the rows were written by whatever run last touched them
# under whatever setting was in force then. The figures it heads go on a tax
# return, so the banner has to be able to tell the two apart.

import config  # noqa: E402

TODAYS_LINE = 'Tax rule in force: swap_realizes=True (config.py, profile PT)'


def test_the_banner_no_longer_claims_to_name_the_rule_that_produced_the_figures():
    """The defect was the docstring, and only a reader can be misled by it.

    Nothing executes a docstring, so nothing else can notice it going back to
    a claim the function cannot keep.
    """
    doc = config.tax_rule_banner.__doc__
    assert 'produced the figures just printed' not in doc, \
        'the function cannot know what produced figures it did not calculate'
    assert 'configured' in doc, \
        'it must say what it does name: the setting configured right now'


def test_the_banner_without_a_connection_is_unchanged(db_path):
    """No connection, no claim about data: exactly the line it has always been."""
    assert config.tax_rule_banner() == TODAYS_LINE


def test_the_banner_on_a_clean_default_database_is_the_line_it_was(db_path):
    """The line that heads every report Gabriele reads. No transfers in the
    table means nothing to add — and this is checked, not assumed."""
    _realizing_db(db_path)

    conn = _connect(db_path)
    try:
        assert conn.execute('SELECT COUNT(*) FROM swap_lot_transfers').fetchone()[0] == 0
        assert config.tax_rule_banner(conn) == TODAYS_LINE
    finally:
        conn.close()


def test_the_banner_on_an_uncalculated_database_is_the_line_it_was(db_path):
    """No evidence either way is not a disagreement."""
    _seed_one_plain_and_one_swap_sale(db_path)

    conn = _connect(db_path)
    try:
        assert config.tax_rule_banner(conn) == TODAYS_LINE
    finally:
        conn.close()


def test_the_banner_on_a_database_without_the_swap_tables_is_the_line_it_was(db_path):
    """Reports run on databases older than the migrations."""
    conn = sqlite3.connect(db_path)
    try:
        conn.execute('DROP TABLE swap_lot_transfers')
        conn.commit()
        assert config.tax_rule_banner(conn) == TODAYS_LINE
    finally:
        conn.close()


def test_the_banner_names_the_disagreement_when_the_data_was_born_under_deferral(db_path, monkeypatch):
    """The test that matters, and the scenario task 13 step 7 walks into.

    Run under swap_realizes = False to price the other rule, then put the
    setting back to True. The next report reads figures calculated under
    deferral and, before this, headed them with `swap_realizes=True` on a line
    that claimed to name the rule that produced them.
    """
    _deferred_db(db_path, monkeypatch)  # patches the ENGINE's flag, not config's
    assert config.SWAP_REALIZES is True, 'config must still carry the default here'

    conn = _connect(db_path)
    try:
        banner = config.tax_rule_banner(conn)
    finally:
        conn.close()

    assert banner.startswith(TODAYS_LINE), \
        'the setting is still named first, and named correctly'
    assert 'THE SETTING AND THE DATA DISAGREE' in banner, \
        'the reader must not be able to skim past this'
    assert '1 deferred swap disposal' in banner, \
        'it must say how much of the data is affected'
    assert 'swap_realizes=False' in banner, \
        'and which rule actually produced it'
    assert 'calculators/calculate_fifo.py' in banner, \
        'first thing to do: recalculate under the setting now in force'
    assert 'the rule they were born under' in banner, \
        'second thing to do: read them under the rule that produced them'
    assert 'tax return' in banner, 'and why it matters'


def test_the_banner_names_the_disagreement_in_the_other_direction_too(db_path, monkeypatch):
    """Setting flipped to False, figures still the ones a True run realized.

    The same defect arriving from the other side, and the one you hit the
    moment you flip the setting and read a report before recalculating. Left
    out, it would be a guard that covers one direction of a symmetric problem.
    """
    _realizing_db(db_path)  # swap disposals that realized, written under True
    monkeypatch.setattr(config, 'SWAP_REALIZES', False)

    conn = _connect(db_path)
    try:
        banner = config.tax_rule_banner(conn)
    finally:
        conn.close()

    assert 'swap_realizes=False (config.py, profile PT)' in banner, \
        'the setting is named as configured'
    assert 'THE SETTING AND THE DATA DISAGREE' in banner
    assert 'realized a gain' in banner, 'it must say what the evidence is'
    assert 'swap_realizes=True' in banner, 'and which rule produced it'


def test_the_banner_confirms_the_data_when_the_setting_agrees_with_it(db_path, monkeypatch):
    """Setting False and the transfers to match: no warning, but say so.

    Under the non-default rule a reader has a right to see that the deferral
    actually reached the data, rather than infer it from a setting.
    """
    _deferred_db(db_path, monkeypatch)
    monkeypatch.setattr(config, 'SWAP_REALIZES', False)

    conn = _connect(db_path)
    try:
        banner = config.tax_rule_banner(conn)
    finally:
        conn.close()

    assert 'DISAGREE' not in banner, 'the setting and the data agree here'
    assert '1 deferred swap disposal' in banner
    assert 'consistent with this setting' in banner


# ---- the evidence of the other direction: realized_swap_disposals ----

def test_realized_swap_disposals_counts_only_swap_legs_that_produced_a_match(db_path):
    _realizing_db(db_path)

    conn = _connect(db_path)
    try:
        result = realized_swap_disposals(conn)
        all_matches = conn.execute('SELECT COUNT(*) FROM sale_lot_matches').fetchone()[0]
    finally:
        conn.close()

    assert all_matches == 2, 'the plain SELL and the swap SELL both realized'
    assert result['count'] == 1, 'only the one carrying a swap_group_id is swap evidence'
    assert result['total_value'] == 3200.0


def test_realized_swap_disposals_is_zero_when_the_swap_deferred(db_path, monkeypatch):
    _deferred_db(db_path, monkeypatch)

    conn = _connect(db_path)
    try:
        assert realized_swap_disposals(conn)['count'] == 0
    finally:
        conn.close()


def test_realized_swap_disposals_is_zero_before_the_fifo_runs(db_path):
    _seed_one_plain_and_one_swap_sale(db_path)

    conn = _connect(db_path)
    try:
        assert realized_swap_disposals(conn)['count'] == 0
    finally:
        conn.close()


def test_the_banner_still_warns_on_a_database_that_predates_the_transfers_table(db_path, monkeypatch):
    """Separates the two prerequisite guards, which overlap everywhere else.

    `swap_lot_transfers` is a prerequisite of the *deferred* evidence only.
    Checking it in the shared body instead would answer zero for the realized
    evidence too, and a pre-migration database is exactly where that matters:
    the setting can be flipped to `False` in `config.py` while the engine
    refuses to recalculate without the table, so the figures on record are
    whatever a `True` run left — and the report still prints them.

    Every other test has the table, so none of them can tell the two guards
    apart.
    """
    _realizing_db(db_path)
    conn = _connect(db_path)
    try:
        conn.execute('DROP TABLE swap_lot_transfers')
        conn.commit()
        monkeypatch.setattr(config, 'SWAP_REALIZES', False)
        banner = config.tax_rule_banner(conn)
    finally:
        conn.close()

    assert 'THE SETTING AND THE DATA DISAGREE' in banner, (
        'the setting says deferral, the data was realized under the other rule, '
        'and the missing table is no reason to stay silent about it'
    )
    assert 'realized a gain' in banner


# ---- the third kind of evidence: a lot that inherited its date and cost ----
#
# The first two look only at the leg that GIVES UP. An asset RECEIVED and
# calculated under the other rule leaves no trace in either: its inherited lots
# live in `fifo_lots`, and the moment the give side is recalculated the
# `DELETE FROM swap_lot_transfers` — which runs under `True` as well — removes
# the last standing evidence. Measured below.

def _partially_recalculated_db(db_path, monkeypatch):
    """The state the first two kinds of evidence cannot see.

    A full deferred run, then only the asset GIVEN UP recalculated under the
    declared rule. The give side now realizes, its transfers are deleted, and
    the asset received keeps the lots it inherited: the same cost basis counted
    on both sides, and an acquisition date that exempts a disposal the declared
    rule would tax.

    Built with the engine, twice, exactly as a shell session reaches it —
    `calculate_fifo_lots('ETH')` is how one asset gets recomputed by hand.
    """
    _seed_one_plain_and_one_swap_sale(db_path)
    monkeypatch.setattr('calculators.crypto_fifo_tracker.SWAP_REALIZES', False)
    _run_engine(db_path, 'ETH', 'USDT')
    monkeypatch.setattr('calculators.crypto_fifo_tracker.SWAP_REALIZES', True)
    _run_engine(db_path, 'ETH')  # the give side only
    return db_path


def test_the_partially_recalculated_state_is_invisible_to_the_first_two_evidences(db_path, monkeypatch):
    """Pins why a third kind of evidence was needed, not just that it exists.

    If this test ever fails because the counts stopped being zero, the third
    evidence may have become redundant — check before removing it.
    """
    _partially_recalculated_db(db_path, monkeypatch)

    conn = _connect(db_path)
    try:
        assert conn.execute('SELECT COUNT(*) FROM swap_lot_transfers').fetchone()[0] == 0, \
            'recalculating the give side deletes them, under either rule'
        assert deferred_disposals(conn)['count'] == 0, \
            'the give side realized: nothing looks deferred any more'
        assert realized_swap_disposals(conn)['count'] == 1, \
            'and what it realized agrees with the setting now in force'
        # ...while the asset received still carries deferral in its lots.
        stranded = conn.execute("""
            SELECT l.cryptocurrency, l.purchase_date, l.cost_basis
            FROM fifo_lots l JOIN transactions t ON l.purchase_transaction_id = t.id
            WHERE l.purchase_date <> t.transaction_date
        """).fetchall()
    finally:
        conn.close()

    assert len(stranded) == 1 and stranded[0]['cryptocurrency'] == 'USDT', \
        'the received asset keeps an inherited lot: EUR 1,000 of cost basis and a ' \
        '2020 acquisition date, against a BUY leg dated 2024'


def test_inherited_lots_separates_the_four_states(db_path, monkeypatch):
    """One evidence, four states, measured rather than argued."""
    _seed_one_plain_and_one_swap_sale(db_path)

    conn = _connect(db_path)
    try:
        assert inherited_lots(conn)['count'] == 0, 'uncalculated database'
    finally:
        conn.close()

    monkeypatch.setattr('calculators.crypto_fifo_tracker.SWAP_REALIZES', False)
    _run_engine(db_path, 'ETH', 'USDT')
    conn = _connect(db_path)
    try:
        assert inherited_lots(conn)['count'] == 1, 'clean deferred run'
    finally:
        conn.close()

    monkeypatch.setattr('calculators.crypto_fifo_tracker.SWAP_REALIZES', True)
    _run_engine(db_path, 'ETH')
    conn = _connect(db_path)
    try:
        assert inherited_lots(conn)['count'] == 1, \
            'partial state: the only evidence left, and the one that matters'
    finally:
        conn.close()

    _run_engine(db_path, 'USDT')  # finish the recalculation
    conn = _connect(db_path)
    try:
        assert inherited_lots(conn)['count'] == 0, 'clean True run'
    finally:
        conn.close()


def test_the_banner_shouts_on_the_partially_recalculated_state(db_path, monkeypatch):
    """The blocker. A silent banner here heads figures that double-count a cost
    basis and move a taxable gain into the exempt column of Anexo G1."""
    _partially_recalculated_db(db_path, monkeypatch)
    assert config.SWAP_REALIZES is True

    conn = _connect(db_path)
    try:
        banner = config.tax_rule_banner(conn)
    finally:
        conn.close()

    assert 'THE SETTING AND THE DATA DISAGREE' in banner, (
        'the asset received still holds lots built under deferral; a clean line '
        'here tells the reader the setting and the data agree, which nothing has '
        'established'
    )
    assert '1 lot(s) whose acquisition date and cost were inherited' in banner, \
        'it must name which evidence fired, because the fix differs per evidence'
    assert 'swap_realizes=False' in banner


def test_the_banner_stays_silent_once_the_recalculation_is_finished(db_path, monkeypatch):
    """The partial state is the defect; the finished one is not.

    Without this the warning could fire on every database that has ever seen a
    swap, which is the same as not firing at all.
    """
    _partially_recalculated_db(db_path, monkeypatch)
    _run_engine(db_path, 'USDT')  # the asset received, under the setting in force

    conn = _connect(db_path)
    try:
        assert config.tax_rule_banner(conn) == TODAYS_LINE
    finally:
        conn.close()


# ---- the aggregate notes must not add up quantities of different assets ----

def _two_deferred_swaps_in_different_assets(db_path):
    """0.5 BTC swapped for USDT and 2 ETH swapped for DAI, same day.

    Two deferred disposals whose quantities are not in the same unit — which is
    what the aggregate notes were printing as one eight-decimal figure.
    """
    conn = sqlite3.connect(db_path)
    try:
        _tx(conn, transaction_date='2020-01-10T10:00:00+00:00', transaction_type='BUY',
            cryptocurrency='BTC', amount=1.0, price_per_unit=10000.0, total_value=10000.0,
            fee_currency='EUR', currency='EUR', exchange_name='Test',
            source='seed', imported_at='2026-01-01T00:00:00', record_hash='b1')
        _tx(conn, transaction_date='2020-01-10T10:00:00+00:00', transaction_type='BUY',
            cryptocurrency='ETH', amount=2.0, price_per_unit=1000.0, total_value=2000.0,
            fee_currency='EUR', currency='EUR', exchange_name='Test',
            source='seed', imported_at='2026-01-01T00:00:00', record_hash='b2')
        _tx(conn, transaction_date='2024-06-15T15:00:00+00:00', transaction_type='SELL',
            cryptocurrency='BTC', amount=0.5, price_per_unit=50000.0, total_value=25000.0,
            fee_currency='EUR', currency='EUR', exchange_name='Test',
            source='seed', imported_at='2026-01-01T00:00:00', record_hash='b3',
            swap_group_id='swap-btc')
        _tx(conn, transaction_date='2024-06-15T15:00:00+00:00', transaction_type='BUY',
            cryptocurrency='USDT', amount=25000.0, price_per_unit=1.0, total_value=25000.0,
            fee_currency='EUR', currency='EUR', exchange_name='Test',
            source='seed', imported_at='2026-01-01T00:00:00', record_hash='b4',
            swap_group_id='swap-btc')
        _tx(conn, transaction_date='2024-06-15T15:00:00+00:00', transaction_type='SELL',
            cryptocurrency='ETH', amount=2.0, price_per_unit=1600.0, total_value=3200.0,
            fee_currency='EUR', currency='EUR', exchange_name='Test',
            source='seed', imported_at='2026-01-01T00:00:00', record_hash='b5',
            swap_group_id='swap-eth')
        _tx(conn, transaction_date='2024-06-15T15:00:00+00:00', transaction_type='BUY',
            cryptocurrency='DAI', amount=3200.0, price_per_unit=1.0, total_value=3200.0,
            fee_currency='EUR', currency='EUR', exchange_name='Test',
            source='seed', imported_at='2026-01-01T00:00:00', record_hash='b6',
            swap_group_id='swap-eth')
    finally:
        conn.close()
    return db_path


def test_the_aggregate_notes_do_not_add_up_quantities_of_different_assets(db_path, monkeypatch):
    """0.5 BTC plus 2 ETH is not 2.5 of anything.

    Printed to eight decimals, next to the per-asset quantities the reader is
    trying to reconcile, inside a note that exists to stop a wrong
    reconciliation. Count and EUR only — the model is the totals line.
    """
    _two_deferred_swaps_in_different_assets(db_path)
    monkeypatch.setattr('calculators.crypto_fifo_tracker.SWAP_REALIZES', False)
    _run_engine(db_path, 'BTC', 'ETH', 'USDT', 'DAI')

    output = _annual_summary(db_path)
    sales = _section(output, 'VENDITE')
    fifo = _section(output, 'RISULTATO FIFO')

    assert '2.50000000' not in output, \
        'the sum of a BTC quantity and an ETH quantity must not be printed at all'
    assert 'unità' not in sales + fifo, \
        'no aggregate quantity in the notes: there is no unit it could be in'
    for section in (sales, fifo):
        assert 'differite' in section and ': 2' in section, 'the count stays'
        assert '28,200.00' in section, 'and the EUR total, which is one unit'
    # Per-asset quantities, which ARE in a unit, must be untouched.
    assert '0.50000000' in sales and '2.00000000' in sales, \
        'the per-cryptocurrency rows still carry their own quantities'

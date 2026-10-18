"""Reconciling swap_group_id across the two import paths of one export.

`import_non_btc_legs` drops every BTC row, so a BTC->USDT swap reaches the
database as a BUY leg alone: its SELL leg was written by another importer,
which does not know the `swap_group_id` column. These tests cover the script
that links the two — what it writes, what it refuses to write, and that it
writes nothing at all on either refusal path.

Every figure in this module is invented. The *shape* is the one that matters:
several swaps in the same second, no order id, and two of them disposing of
the same quantity, so a pairing that works here cannot be working by
(instant, quantity).
"""
import os
import sqlite3
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from importers.import_non_btc_legs import import_non_btc_legs  # noqa: E402
from importers.import_utils import compute_record_hash  # noqa: E402
from importers.link_swap_counterparty_legs import (  # noqa: E402
    _swap_leg,
    link_swap_counterparty_legs,
)
from importers.non_btc_valuation import value_row  # noqa: E402

PROJECT_ROOT = os.path.join(os.path.dirname(__file__), '..')
SCRIPT = os.path.join(PROJECT_ROOT, 'importers', 'link_swap_counterparty_legs.py')

EXCHANGE = 'TestVenue'
# The other importer tags its rows with the plain file name; the non-BTC
# importer appends its own suffix to the same name (SOURCE_SUFFIX).
COUNTERPARTY_SOURCE = 'testvenue_trades.csv'
INSTANT = '2024-07-02T12:00:00+00:00'

# Three BTC->USDT swaps in the same second with no order id. Rows 0 and 2
# dispose of the same quantity of BTC and differ only in what they bought,
# so any test that tells them apart can only be passing because the pairing
# is positional.
TRADE_HISTORY = (
    'Date(UTC),Pair,Side,Executed,Amount,Fee,Order No.\n'
    '2024-07-02 12:00:00,BTCUSDT,SELL,0.40000000,24000.000000,0,\n'
    '2024-07-02 12:00:00,BTCUSDT,SELL,0.25000000,15000.000000,0,\n'
    '2024-07-02 12:00:00,BTCUSDT,SELL,0.40000000,24100.000000,0,\n'
)
# The BTC quantities the other importer wrote, in id order — the sequence the
# script checks before it writes anything.
COUNTERPARTY_AMOUNTS = (0.4, 0.25, 0.4)


class _FakeRates:
    """No-network rate source: EUR passes through, one flat crypto price.

    Constructing the real `Rates()` would build an `ECBRates` and a
    `CryptoPrices`, which read CSV caches relative to the working directory
    and may reach for the network (see import_non_btc_legs.Rates). The flat
    1000.0 is arbitrary: no test here asserts a EUR figure, only which rows
    end up sharing which swap_group_id.
    """

    def fiat_to_eur(self, amount, currency, when):
        if currency != 'EUR':
            raise AssertionError(f'unexpected fiat currency in fixture: {currency!r}')
        return amount

    def crypto_to_eur(self, coin, amount, when):
        return amount * 1000.0


@pytest.fixture
def export(tmp_path):
    """The export file, named so that both import paths derive their own source."""
    path = tmp_path / COUNTERPARTY_SOURCE
    path.write_text(TRADE_HISTORY)
    return path


def _import_buy_legs(export, db_path, exchange_name=EXCHANGE):
    """Run the real non-BTC importer: it writes the USDT BUY legs, drops the BTC ones."""
    return import_non_btc_legs(str(export), exchange_name, 'trade_history', db_path,
                               rates=_FakeRates())


def _seed_counterparty_legs(db_path, amounts, when=INSTANT, crypto='BTC',
                            group_ids=None, source=COUNTERPARTY_SOURCE):
    """The SELL legs another importer wrote from the same file: contiguous ids.

    `swap_group_id` is NULL unless `group_ids` says otherwise — an importer
    that predates the column leaves it unset, which is the state this script
    exists to fill in.
    """
    conn = sqlite3.connect(db_path)
    try:
        for i, amount in enumerate(amounts):
            value = amount * 1000.0
            conn.execute(
                """INSERT INTO transactions (transaction_date, transaction_type,
                       exchange_name, cryptocurrency, amount, price_per_unit, total_value,
                       fee_amount, fee_currency, currency, source, imported_at,
                       record_hash, swap_group_id)
                   VALUES (?,'SELL',?,?,?,?,?,0,'EUR','EUR',?,?,?,?)""",
                (when, EXCHANGE, crypto, amount, 1000.0, value, source,
                 '2026-01-01T00:00:00',
                 compute_record_hash(source, when, 'SELL', EXCHANGE, crypto,
                                     amount, value, 0),
                 None if group_ids is None else group_ids[i]))
        conn.commit()
    finally:
        conn.close()


def _link(export, db_path, exchange_name=EXCHANGE, dry_run=False,
          counterparty_source=COUNTERPARTY_SOURCE):
    return link_swap_counterparty_legs(str(export), exchange_name, 'trade_history',
                                       counterparty_source, db_path=db_path,
                                       dry_run=dry_run)


def _legs(db_path, crypto):
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute(
            'SELECT amount, swap_group_id FROM transactions WHERE cryptocurrency = ? '
            'ORDER BY id', (crypto,)).fetchall()
    finally:
        conn.close()


def _snapshot_except_swap_group(db_path):
    """Every column of every row except swap_group_id, in id order."""
    conn = sqlite3.connect(db_path)
    try:
        cols = [r[1] for r in conn.execute('PRAGMA table_info(transactions)')
                if r[1] != 'swap_group_id']
        assert 'swap_group_id' not in cols and 'record_hash' in cols
        return conn.execute(
            f'SELECT {", ".join(cols)} FROM transactions ORDER BY id').fetchall()
    finally:
        conn.close()


# ---------------------------------------------------------------- what it writes

def test_links_each_leg_by_position_not_by_quantity(export, db_path):
    """The three BTC legs get the swap id of the file row in their own position.

    The assertion that carries the weight is the last one: rows 0 and 2
    dispose of the same 0.4 BTC, so the only thing that can tell their swap
    ids apart is the order the rows sit in.
    """
    _import_buy_legs(export, db_path)
    _seed_counterparty_legs(db_path, COUNTERPARTY_AMOUNTS)

    summary = _link(export, db_path)

    assert summary['updated'] == 3
    assert summary['already_linked'] == 0
    assert summary['rows_without_swap'] == 0
    assert summary['dry_run'] is False

    sells, buys = _legs(db_path, 'BTC'), _legs(db_path, 'USDT')
    assert all(row[1] for row in sells), 'a BTC leg was left without a swap_group_id'
    assert [row[1] for row in sells] == [row[1] for row in buys]

    bought_under = {row[1]: row[0] for row in buys}
    assert bought_under[sells[0][1]] == pytest.approx(24000.0)
    assert bought_under[sells[2][1]] == pytest.approx(24100.0)


def test_nothing_but_the_swap_group_id_column_changes(export, db_path):
    """No INSERT, no DELETE, no other column — the record_hash stays valid.

    `compute_record_hash` does not hash swap_group_id, so an updated row
    keeps the hash it was imported with; this test is what makes that a
    checked fact here rather than a claim in a docstring.
    """
    _import_buy_legs(export, db_path)
    _seed_counterparty_legs(db_path, COUNTERPARTY_AMOUNTS)
    before = _snapshot_except_swap_group(db_path)

    _link(export, db_path)

    assert _snapshot_except_swap_group(db_path) == before


def test_a_second_run_changes_nothing(export, db_path):
    """Idempotent: the second run finds every disposal leg already on record."""
    _import_buy_legs(export, db_path)
    _seed_counterparty_legs(db_path, COUNTERPARTY_AMOUNTS)

    _link(export, db_path)
    after_first = _legs(db_path, 'BTC')
    summary = _link(export, db_path)

    assert summary['updated'] == 0
    assert summary['already_linked'] == 3
    assert _legs(db_path, 'BTC') == after_first


def test_dry_run_reports_what_it_would_do_and_writes_nothing(export, db_path, capsys):
    _import_buy_legs(export, db_path)
    _seed_counterparty_legs(db_path, COUNTERPARTY_AMOUNTS)

    summary = _link(export, db_path, dry_run=True)

    assert summary['updated'] == 3
    assert summary['dry_run'] is True
    assert [row[1] for row in _legs(db_path, 'BTC')] == [None, None, None]
    assert 'nothing written' in capsys.readouterr().out


def test_a_swap_whose_disposal_leg_is_already_on_record_is_left_alone(db_path, tmp_path):
    """Neither asset is BTC, so the non-BTC importer kept both legs itself.

    Nothing is seeded from another importer here: if the script looked for a
    counterparty leg for this row it would find none and refuse on the count.
    """
    export = tmp_path / COUNTERPARTY_SOURCE
    export.write_text('Date(UTC),Pair,Side,Executed,Amount,Fee,Order No.\n'
                      '2024-07-02 12:00:00,ETHUSDT,SELL,3.00000000,9000.000000,0,\n')
    _import_buy_legs(export, db_path)

    summary = _link(export, db_path)

    assert summary == {'updated': 0, 'already_linked': 1, 'rows_without_swap': 0,
                       'dry_run': False}


def test_rows_with_no_swap_to_link_are_counted_and_left_alone(db_path, tmp_path):
    """A crypto-to-fiat sale and a transfer produce no swap_group_id at all."""
    export = tmp_path / COUNTERPARTY_SOURCE
    export.write_text('Date(UTC),Pair,Side,Executed,Amount,Fee,Order No.\n'
                      '2024-07-02 12:00:00,USDCEUR,SELL,1000.000000,930.000000,0,\n'
                      '2024-07-03 12:00:00,USDCEUR,BUY,500.000000,465.000000,0,\n')

    summary = _link(export, db_path)

    assert summary == {'updated': 0, 'already_linked': 0, 'rows_without_swap': 2,
                       'dry_run': False}


def test_only_the_named_source_s_disposal_rows_are_candidates(export, db_path):
    """Two distractors at the same instant, each of which would break the count.

    A BUY of BTC under the same source (the engine reads the asset given up
    by swap_group_id on the SELL row, so only a SELL can be the leg looked
    for) and a SELL of BTC under another source (another file's export, not
    this reconciliation's business). Both sit in the same second as the three
    real legs; counting either one in would make the positional pairing
    refuse on the count, so this test passing is what pins both filters.
    """
    _import_buy_legs(export, db_path)
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """INSERT INTO transactions (transaction_date, transaction_type, exchange_name,
                   cryptocurrency, amount, price_per_unit, total_value, fee_amount,
                   fee_currency, currency, source, imported_at, record_hash)
               VALUES (?,'BUY',?,'BTC',0.4,1000.0,400.0,0,'EUR','EUR',?,
                       '2026-01-01T00:00:00','hash-distractor-buy')""",
            (INSTANT, EXCHANGE, COUNTERPARTY_SOURCE))
        conn.commit()
    finally:
        conn.close()
    _seed_counterparty_legs(db_path, COUNTERPARTY_AMOUNTS)
    _seed_counterparty_legs(db_path, (0.4,), source='another_file.csv')

    summary = _link(export, db_path)

    assert summary['updated'] == 3
    conn = sqlite3.connect(db_path)
    try:
        linked = conn.execute(
            "SELECT COUNT(*) FROM transactions WHERE cryptocurrency = 'BTC' "
            "AND transaction_type = 'SELL' AND source = ? AND swap_group_id IS NOT NULL",
            (COUNTERPARTY_SOURCE,)).fetchone()[0]
        distractor_buy = conn.execute(
            "SELECT swap_group_id FROM transactions WHERE record_hash = 'hash-distractor-buy'"
        ).fetchone()
        other_source = conn.execute(
            "SELECT swap_group_id FROM transactions WHERE source = 'another_file.csv'"
        ).fetchall()
    finally:
        conn.close()

    assert linked == 3
    assert distractor_buy == (None,)
    assert [row[0] for row in other_source] == [None]


# ------------------------------------------------------------- what it refuses

def test_a_fee_in_a_third_asset_stops_the_run_even_on_a_row_with_a_fiat_leg(db_path, tmp_path):
    """The fee is applied before the fiat test, exactly as value_row does it.

    An export `import_non_btc_legs.py` refuses must not be an export this
    script walks through quietly: both read the same rows through the same
    valuation helpers. Were the fiat test to come first, this row — a sale
    for EUR with its fee in a third asset — would be stepped over as "no
    swap here" and the run would report success on a file that cannot be
    imported.
    """
    export = tmp_path / COUNTERPARTY_SOURCE
    export.write_text('Date(UTC),Pair,Side,Executed,Amount,Fee,Order No.\n'
                      '2024-07-02 12:00:00,USDCEUR,SELL,1000.000000,930.000000,0.5BNB,\n')

    with pytest.raises(ValueError, match='neither leg') as exc_info:
        _link(export, db_path)

    assert 'row 0' in str(exc_info.value)


def test_an_unknown_shape_is_refused(export, db_path):
    with pytest.raises(ValueError, match='unknown shape'):
        link_swap_counterparty_legs(str(export), EXCHANGE, 'ledger_csv',
                                    COUNTERPARTY_SOURCE, db_path=db_path)



def test_refuses_when_the_candidate_count_differs(export, db_path):
    """One counterparty leg short: the positional pairing is not established.

    The two rows that *are* present match positions 0 and 1 exactly, so the
    quantity-sequence check has nothing to object to — only the count guard
    can speak here.
    """
    _import_buy_legs(export, db_path)
    _seed_counterparty_legs(db_path, (0.4, 0.25))

    with pytest.raises(ValueError, match='leg count mismatch'):
        _link(export, db_path)

    assert [row[1] for row in _legs(db_path, 'BTC')] == [None, None]


def test_refuses_when_the_quantity_sequence_differs(export, db_path):
    """Right count, right multiset, wrong order — the case only order catches.

    (0.4, 0.4, 0.25) against the file's (0.4, 0.25, 0.4): a check on the set
    of quantities, or on their sum, or on their count, passes. Position 1
    does not.
    """
    _import_buy_legs(export, db_path)
    _seed_counterparty_legs(db_path, (0.4, 0.4, 0.25))

    with pytest.raises(ValueError, match='quantity sequence mismatch'):
        _link(export, db_path)

    assert [row[1] for row in _legs(db_path, 'BTC')] == [None, None, None]


def test_refuses_a_row_that_already_carries_a_different_swap_group_id(export, db_path):
    """An id already on the row is never overwritten, whatever its provenance."""
    _import_buy_legs(export, db_path)
    foreign = 'swap-00000000000000ff'
    _seed_counterparty_legs(db_path, COUNTERPARTY_AMOUNTS, group_ids=(None, foreign, None))

    with pytest.raises(ValueError, match='already carries a different swap_group_id'):
        _link(export, db_path)

    assert [row[1] for row in _legs(db_path, 'BTC')] == [None, foreign, None]


def test_refuses_when_no_buy_leg_on_record_matches_the_computed_id(export, db_path):
    """Run with arguments the import did not use, and the ids do not exist.

    `exchange_name` is hashed into the swap id, so importing under
    'OtherVenue' and reconciling under 'TestVenue' computes ids that are in
    no row of the database. Writing them would link the BTC legs to nothing
    and leave the engine with an orphan swap group
    (calculators/crypto_fifo_tracker.py:366-378) instead of a pair.
    """
    _import_buy_legs(export, db_path, exchange_name='OtherVenue')
    _seed_counterparty_legs(db_path, COUNTERPARTY_AMOUNTS)

    with pytest.raises(ValueError, match='no BUY leg on record'):
        _link(export, db_path)

    assert [row[1] for row in _legs(db_path, 'BTC')] == [None, None, None]


def test_an_unparsable_timestamp_in_the_database_names_the_row(export, db_path):
    _import_buy_legs(export, db_path)
    _seed_counterparty_legs(db_path, COUNTERPARTY_AMOUNTS)
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("UPDATE transactions SET transaction_date = 'last tuesday' "
                     "WHERE cryptocurrency = 'BTC' AND amount = 0.25")
        conn.commit()
    finally:
        conn.close()

    with pytest.raises(ValueError, match='unparsable transaction_date on transactions row'):
        _link(export, db_path)


def test_a_refusal_in_a_later_group_leaves_the_earlier_one_unwritten(db_path, tmp_path):
    """One transaction: the first group's update must not survive the second's refusal."""
    export = tmp_path / COUNTERPARTY_SOURCE
    export.write_text('Date(UTC),Pair,Side,Executed,Amount,Fee,Order No.\n'
                      '2024-07-02 12:00:00,BTCUSDT,SELL,0.40000000,24000.000000,0,\n'
                      '2024-08-03 15:30:00,BTCUSDT,SELL,0.10000000,6000.000000,0,\n')
    _import_buy_legs(export, db_path)
    _seed_counterparty_legs(db_path, (0.4,))
    foreign = 'swap-00000000000000ff'
    _seed_counterparty_legs(db_path, (0.1,), when='2024-08-03T15:30:00+00:00',
                            group_ids=(foreign,))

    with pytest.raises(ValueError, match='already carries a different swap_group_id'):
        _link(export, db_path)

    assert [row[1] for row in _legs(db_path, 'BTC')] == [None, foreign]


# ------------------------------------------------------ agreement with the engine

def _row(**kw):
    base = {'when': INSTANT, 'kind': 'TRADE', 'give_asset': 'BTC', 'give_amount': 0.5,
            'take_asset': 'USDT', 'take_amount': 30000.0, 'fee_asset': '',
            'fee_amount': 0.0, 'exchange': EXCHANGE, 'ref': ''}
    base.update(kw)
    return base


AGREEMENT_ROWS = [
    _row(),                                                              # crypto -> crypto
    _row(fee_asset='BTC', fee_amount=0.001),                             # fee on the disposal
    _row(fee_asset='USDT', fee_amount=10.0),                             # fee on the acquisition
    _row(give_asset='EUR', give_amount=20000.0),                         # fiat -> crypto
    _row(take_asset='EUR', take_amount=20000.0),                         # crypto -> fiat
    _row(give_asset='EUR', give_amount=1.0, take_asset='USD', take_amount=1.1),
    _row(kind='TRANSFER_IN', give_asset='', give_amount=0.0),
    _row(kind='TRANSFER_OUT', take_asset='', take_amount=0.0),
    _row(give_amount=0.0, take_amount=0.0),                              # dust
]


@pytest.mark.parametrize('index', range(len(AGREEMENT_ROWS)))
def test_the_swap_leg_agrees_with_value_row(index):
    """Drift guard between this script and the valuation layer it must match.

    `value_row` cannot be called here: for a crypto-to-crypto row it needs a
    rate source, and this script values nothing and must not reach for one.
    So it re-expresses which rows are swaps, and reuses `_swap_id` and
    `_apply_fee` for the two figures that have to agree exactly — the id it
    writes and the quantity it pairs on. This test is what keeps the
    re-expressed branch selection from drifting away from the real one.
    """
    row = AGREEMENT_ROWS[index]
    ref = f'agreement#{index}'
    txs = value_row(row, _FakeRates(), row_ref=ref)
    sells = [tx for tx in txs if tx['swap_group_id'] is not None
             and tx['transaction_type'] == 'SELL']

    leg = _swap_leg(row, ref)

    if not sells:
        assert leg is None
        return
    give_total, group_id = leg
    assert group_id == sells[0]['swap_group_id']
    assert give_total == pytest.approx(sells[0]['amount'])


# ----------------------------------------------------------------------- the CLI

def test_cli_dry_run_reports_without_writing(export, db_path):
    _import_buy_legs(export, db_path)
    _seed_counterparty_legs(db_path, COUNTERPARTY_AMOUNTS)

    r = subprocess.run([sys.executable, SCRIPT, str(export), 'trade_history', EXCHANGE,
                        COUNTERPARTY_SOURCE, '--db', db_path, '--dry-run'],
                       capture_output=True, text=True)

    assert r.returncode == 0, r.stderr
    assert 'nothing written' in r.stdout
    assert [row[1] for row in _legs(db_path, 'BTC')] == [None, None, None]


def test_cli_writes_the_links(export, db_path):
    _import_buy_legs(export, db_path)
    _seed_counterparty_legs(db_path, COUNTERPARTY_AMOUNTS)

    r = subprocess.run([sys.executable, SCRIPT, str(export), 'trade_history', EXCHANGE,
                        COUNTERPARTY_SOURCE, '--db', db_path],
                       capture_output=True, text=True)

    assert r.returncode == 0, r.stderr
    assert all(row[1] for row in _legs(db_path, 'BTC'))


def test_cli_without_arguments_prints_usage_and_fails(db_path):
    r = subprocess.run([sys.executable, SCRIPT], capture_output=True, text=True)

    assert r.returncode == 1
    assert 'Usage:' in r.stdout
    assert 'expected 4 positional arguments' in r.stderr


def test_cli_rejects_an_unknown_option(export, db_path):
    r = subprocess.run([sys.executable, SCRIPT, str(export), 'trade_history', EXCHANGE,
                        COUNTERPARTY_SOURCE, '--db', db_path, '--force'],
                       capture_output=True, text=True)

    assert r.returncode == 1
    assert "unknown option '--force'" in r.stderr

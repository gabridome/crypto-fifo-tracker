"""
Tests for calculators/generate_irs_report.py — fiscal calculations.

Focus: get_daily_sales() must compute purchase_fees from
fifo_lots.purchase_fee_total (allocated proportionally), not from
the indirect formula `cost_basis - amount * purchase_price_per_unit`
which inherits rounding noise from purchase_price_per_unit.
"""

import os
import sys
import sqlite3
import tempfile
import shutil

import pytest

# Project root on sys.path
PROJECT_ROOT = os.path.join(os.path.dirname(__file__), '..')
sys.path.insert(0, os.path.abspath(PROJECT_ROOT))

from calculators.generate_irs_report import get_daily_sales

SCHEMA_FILE = os.path.join(PROJECT_ROOT, 'doc', 'schema.sql')


@pytest.fixture
def tmp_db():
    """Empty schema-initialized DB."""
    tmp_dir = tempfile.mkdtemp()
    path = os.path.join(tmp_dir, 'test.db')
    conn = sqlite3.connect(path)
    with open(SCHEMA_FILE) as f:
        conn.executescript(f.read())
    conn.commit()
    conn.close()
    yield path
    shutil.rmtree(tmp_dir)


def _seed_buy_lot_sales(db_path, *, original_amount, purchase_fee_total,
                       splits, sale_date='2024-06-15',
                       purchase_price_per_unit_in_lot,
                       purchase_price_per_unit_in_match,
                       cost_basis_per_match):
    """
    Seed: 1 BUY transaction → 1 fifo_lot → N sale_lot_matches consuming `splits`.

    The match-level columns (purchase_price_per_unit, cost_basis) can be
    set INDEPENDENTLY from the lot-level fee, to simulate rounding drift.
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()

    # BUY transaction (the source of the lot)
    c.execute("""
        INSERT INTO transactions
            (transaction_date, transaction_type, exchange_name, cryptocurrency,
             amount, price_per_unit, total_value, fee_amount, fee_currency, currency)
        VALUES ('2023-01-10', 'BUY', 'Kraken', 'BTC',
                ?, ?, ?, ?, 'EUR', 'EUR')
    """, (original_amount, purchase_price_per_unit_in_lot,
          original_amount * purchase_price_per_unit_in_lot,
          purchase_fee_total))
    buy_tx_id = c.lastrowid

    # fifo_lot
    c.execute("""
        INSERT INTO fifo_lots
            (purchase_transaction_id, cryptocurrency, purchase_date,
             original_amount, remaining_amount, purchase_price_per_unit,
             cost_basis, purchase_fee_total, exchange_name)
        VALUES (?, 'BTC', '2023-01-10', ?, 0, ?, ?, ?, 'Kraken')
    """, (buy_tx_id, original_amount, purchase_price_per_unit_in_lot,
          original_amount * purchase_price_per_unit_in_lot + purchase_fee_total,
          purchase_fee_total))
    lot_id = c.lastrowid

    # SELL transaction (one for all splits, simplification)
    total_sold = sum(splits)
    c.execute("""
        INSERT INTO transactions
            (transaction_date, transaction_type, exchange_name, cryptocurrency,
             amount, price_per_unit, total_value, fee_amount, fee_currency, currency)
        VALUES (?, 'SELL', 'Kraken', 'BTC', ?, 50000.00, ?, 0, 'EUR', 'EUR')
    """, (sale_date, total_sold, total_sold * 50000.00))
    sale_tx_id = c.lastrowid

    # sale_lot_matches (one per split)
    for amount_sold in splits:
        c.execute("""
            INSERT INTO sale_lot_matches
                (sale_transaction_id, fifo_lot_id, sale_date, purchase_date,
                 cryptocurrency, amount_sold, purchase_price_per_unit,
                 sale_price_per_unit, cost_basis, proceeds, gain_loss,
                 holding_period_days)
            VALUES (?, ?, ?, '2023-01-10', 'BTC',
                    ?, ?, 50000.00, ?,
                    ?, ?, 522)
        """, (sale_tx_id, lot_id, sale_date,
              amount_sold, purchase_price_per_unit_in_match,
              cost_basis_per_match * (amount_sold / sum(splits))
              if sum(splits) > 0 else 0,
              amount_sold * 50000.00,
              amount_sold * 50000.00 - cost_basis_per_match *
              (amount_sold / sum(splits) if sum(splits) > 0 else 0)))

    conn.commit()
    conn.close()


class TestPurchaseFeesProportional:
    """purchase_fees must be allocated proportionally from fifo_lots.purchase_fee_total."""

    def test_full_lot_consumed_purchase_fees_equals_total(self, tmp_db):
        """1 lot of 1.0 BTC with €10 fee, sold entirely (0.4 + 0.6) → purchase_fees = 10.00."""
        _seed_buy_lot_sales(
            tmp_db,
            original_amount=1.0,
            purchase_fee_total=10.00,
            splits=[0.4, 0.6],
            purchase_price_per_unit_in_lot=20000.00,
            purchase_price_per_unit_in_match=20000.00,
            cost_basis_per_match=20010.00,  # 20000 + 10 fee total
        )
        rows = get_daily_sales(tmp_db, 2024)
        assert len(rows) == 1
        assert rows[0]['purchase_fees'] == pytest.approx(10.00, abs=0.001)

    def test_partial_lot_consumed_proportional(self, tmp_db):
        """1 lot of 1.0 BTC with €10 fee, sold only 0.3 → purchase_fees = 3.00."""
        _seed_buy_lot_sales(
            tmp_db,
            original_amount=1.0,
            purchase_fee_total=10.00,
            splits=[0.3],
            purchase_price_per_unit_in_lot=20000.00,
            purchase_price_per_unit_in_match=20000.00,
            cost_basis_per_match=20010.00,
        )
        rows = get_daily_sales(tmp_db, 2024)
        assert len(rows) == 1
        assert rows[0]['purchase_fees'] == pytest.approx(3.00, abs=0.001)

    def test_purchase_fees_robust_to_match_rounding_drift(self, tmp_db):
        """
        REGRESSION: when sale_lot_matches.purchase_price_per_unit is
        rounded differently from the original lot, the OLD formula
        `cost_basis - amount * purchase_price_per_unit` drifts.
        The NEW formula (proportional from fifo_lots.purchase_fee_total)
        must remain correct.

        Lot: 1.0 BTC @ €20000.50 with €10 fee → cost_basis = 20010.50
        Match: amount=1.0, purchase_price_per_unit=20000.00 (rounded down €0.50)
               cost_basis_per_match=20010.50 (truth)

        OLD formula: 20010.50 - (1.0 * 20000.00) = 10.50  ❌ wrong
        NEW formula: 1.0 / 1.0 * 10.00 = 10.00            ✓ correct
        """
        _seed_buy_lot_sales(
            tmp_db,
            original_amount=1.0,
            purchase_fee_total=10.00,
            splits=[1.0],
            purchase_price_per_unit_in_lot=20000.50,
            purchase_price_per_unit_in_match=20000.00,  # drift
            cost_basis_per_match=20010.50,
        )
        rows = get_daily_sales(tmp_db, 2024)
        assert len(rows) == 1
        # Truth from fifo_lots.purchase_fee_total = €10.00
        assert rows[0]['purchase_fees'] == pytest.approx(10.00, abs=0.001), (
            f"Atteso 10.00 da fifo_lots.purchase_fee_total; "
            f"con la formula vecchia darebbe 10.50. "
            f"Got {rows[0]['purchase_fees']}"
        )

    def test_zero_original_amount_no_division_by_zero(self, tmp_db):
        """Edge case: a fifo_lot with original_amount=0 must not crash; purchase_fees=0."""
        conn = sqlite3.connect(tmp_db)
        c = conn.cursor()

        c.execute("""
            INSERT INTO transactions
                (transaction_date, transaction_type, exchange_name, cryptocurrency,
                 amount, price_per_unit, total_value, fee_amount, currency)
            VALUES ('2023-01-10', 'BUY', 'Kraken', 'BTC', 0, 0, 0, 5.0, 'EUR')
        """)
        buy_tx_id = c.lastrowid

        c.execute("""
            INSERT INTO fifo_lots
                (purchase_transaction_id, cryptocurrency, purchase_date,
                 original_amount, remaining_amount, purchase_price_per_unit,
                 cost_basis, purchase_fee_total, exchange_name)
            VALUES (?, 'BTC', '2023-01-10', 0, 0, 0, 5.0, 5.0, 'Kraken')
        """, (buy_tx_id,))
        lot_id = c.lastrowid

        c.execute("""
            INSERT INTO transactions
                (transaction_date, transaction_type, exchange_name, cryptocurrency,
                 amount, price_per_unit, total_value, fee_amount, currency)
            VALUES ('2024-06-15', 'SELL', 'Kraken', 'BTC', 0, 50000, 0, 0, 'EUR')
        """)
        sale_tx_id = c.lastrowid

        c.execute("""
            INSERT INTO sale_lot_matches
                (sale_transaction_id, fifo_lot_id, sale_date, purchase_date,
                 cryptocurrency, amount_sold, purchase_price_per_unit,
                 sale_price_per_unit, cost_basis, proceeds, gain_loss,
                 holding_period_days)
            VALUES (?, ?, '2024-06-15', '2023-01-10', 'BTC',
                    0, 0, 50000, 0, 0, 0, 522)
        """, (sale_tx_id, lot_id))
        conn.commit()
        conn.close()

        rows = get_daily_sales(tmp_db, 2024)
        # Should not raise, purchase_fees defaults to 0 for division-by-zero protection
        assert len(rows) == 1
        assert rows[0]['purchase_fees'] == 0


class TestExemptVsTaxableSplit:
    """Sanity check: exempt and taxable lots are still separated correctly."""

    def test_two_lots_one_exempt_one_taxable_split_into_two_rows(self, tmp_db):
        """Two lots same day same exchange: one >=365d (exempt), one <365d (taxable)."""
        conn = sqlite3.connect(tmp_db)
        c = conn.cursor()
        # BUY 1: Jan 2023 (will be >=365d on 2024-06-15)
        c.execute("""INSERT INTO transactions
            (transaction_date, transaction_type, exchange_name, cryptocurrency,
             amount, price_per_unit, total_value, fee_amount, currency)
            VALUES ('2023-01-10', 'BUY', 'Kraken', 'BTC', 1.0, 20000, 20000, 5.0, 'EUR')""")
        buy1 = c.lastrowid
        # BUY 2: Jan 2024 (will be <365d on 2024-06-15)
        c.execute("""INSERT INTO transactions
            (transaction_date, transaction_type, exchange_name, cryptocurrency,
             amount, price_per_unit, total_value, fee_amount, currency)
            VALUES ('2024-01-10', 'BUY', 'Kraken', 'BTC', 1.0, 30000, 30000, 7.0, 'EUR')""")
        buy2 = c.lastrowid
        # Lots
        c.execute("""INSERT INTO fifo_lots
            (purchase_transaction_id, cryptocurrency, purchase_date,
             original_amount, remaining_amount, purchase_price_per_unit,
             cost_basis, purchase_fee_total, exchange_name)
            VALUES (?, 'BTC', '2023-01-10', 1.0, 0, 20000, 20005, 5.0, 'Kraken')""", (buy1,))
        lot1 = c.lastrowid
        c.execute("""INSERT INTO fifo_lots
            (purchase_transaction_id, cryptocurrency, purchase_date,
             original_amount, remaining_amount, purchase_price_per_unit,
             cost_basis, purchase_fee_total, exchange_name)
            VALUES (?, 'BTC', '2024-01-10', 1.0, 0, 30000, 30007, 7.0, 'Kraken')""", (buy2,))
        lot2 = c.lastrowid
        # SELL same day, both lots
        c.execute("""INSERT INTO transactions
            (transaction_date, transaction_type, exchange_name, cryptocurrency,
             amount, price_per_unit, total_value, fee_amount, currency)
            VALUES ('2024-06-15', 'SELL', 'Kraken', 'BTC', 2.0, 50000, 100000, 0, 'EUR')""")
        sale_tx = c.lastrowid
        # Match exempt
        c.execute("""INSERT INTO sale_lot_matches
            (sale_transaction_id, fifo_lot_id, sale_date, purchase_date,
             cryptocurrency, amount_sold, purchase_price_per_unit,
             sale_price_per_unit, cost_basis, proceeds, gain_loss, holding_period_days)
            VALUES (?, ?, '2024-06-15', '2023-01-10', 'BTC',
                    1.0, 20000, 50000, 20005, 50000, 29995, 522)""",
            (sale_tx, lot1))
        # Match taxable
        c.execute("""INSERT INTO sale_lot_matches
            (sale_transaction_id, fifo_lot_id, sale_date, purchase_date,
             cryptocurrency, amount_sold, purchase_price_per_unit,
             sale_price_per_unit, cost_basis, proceeds, gain_loss, holding_period_days)
            VALUES (?, ?, '2024-06-15', '2024-01-10', 'BTC',
                    1.0, 30000, 50000, 30007, 50000, 19993, 157)""",
            (sale_tx, lot2))
        conn.commit()
        conn.close()

        rows = get_daily_sales(tmp_db, 2024)
        assert len(rows) == 2, f"Atteso 2 righe (exempt + taxable), got {len(rows)}"
        exempt_row = next(r for r in rows if r['is_exempt'])
        taxable_row = next(r for r in rows if not r['is_exempt'])
        assert exempt_row['purchase_fees'] == pytest.approx(5.00, abs=0.001)
        assert taxable_row['purchase_fees'] == pytest.approx(7.00, abs=0.001)

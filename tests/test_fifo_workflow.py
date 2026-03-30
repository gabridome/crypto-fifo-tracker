"""
Crypto FIFO Tracker — Test Suite

Tests the full workflow using the REAL CryptoFIFOTracker engine:
  1. Schema from doc/schema.sql
  2. Test transactions loaded
  3. FIFO calculated via calculators/crypto_fifo_tracker.py
  4. Results verified (lots, matches, holding periods, gain/loss, tax)

Run: pytest tests/ -v
"""

import os
import sys
import sqlite3
import pytest

# Ensure project root is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

TOLERANCE = 0.01


# ============================================================
# Test 1: Schema and infrastructure
# ============================================================

class TestSchemaAndSetup:

    def test_schema_file_exists(self):
        schema = os.path.join(os.path.dirname(__file__), '..', 'doc', 'schema.sql')
        assert os.path.exists(schema), "doc/schema.sql must exist"

    def test_config_importable(self):
        from config import DATABASE_PATH, PROJECT_ROOT
        assert os.path.isabs(DATABASE_PATH), "DATABASE_PATH must be absolute"
        assert os.path.isabs(PROJECT_ROOT), "PROJECT_ROOT must be absolute"

    def test_database_tables(self, db_path):
        conn = sqlite3.connect(db_path)
        tables = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        conn.close()
        assert 'transactions' in tables
        assert 'fifo_lots' in tables
        assert 'sale_lot_matches' in tables

    def test_source_tracking_columns(self, db_path):
        """Schema includes source tracking columns (added March 2026)."""
        conn = sqlite3.connect(db_path)
        cols = [r[1] for r in conn.execute("PRAGMA table_info(transactions)")]
        conn.close()
        assert 'source' in cols
        assert 'imported_at' in cols
        assert 'record_hash' in cols


# ============================================================
# Test 2: Transaction import
# ============================================================

class TestTransactionImport:

    def test_transaction_count(self, db_with_transactions):
        conn = sqlite3.connect(db_with_transactions)
        total = conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
        conn.close()
        assert total == 7

    def test_buy_sell_counts(self, db_with_transactions):
        conn = sqlite3.connect(db_with_transactions)
        buys = conn.execute("SELECT COUNT(*) FROM transactions WHERE transaction_type='BUY'").fetchone()[0]
        sells = conn.execute("SELECT COUNT(*) FROM transactions WHERE transaction_type='SELL'").fetchone()[0]
        conn.close()
        assert buys == 4
        assert sells == 3

    def test_btc_total_bought(self, db_with_transactions):
        conn = sqlite3.connect(db_with_transactions)
        total = conn.execute(
            "SELECT SUM(amount) FROM transactions WHERE transaction_type='BUY' AND cryptocurrency='BTC'"
        ).fetchone()[0]
        conn.close()
        assert abs(total - 0.17) < TOLERANCE

    def test_eth_total_bought(self, db_with_transactions):
        conn = sqlite3.connect(db_with_transactions)
        total = conn.execute(
            "SELECT SUM(amount) FROM transactions WHERE transaction_type='BUY' AND cryptocurrency='ETH'"
        ).fetchone()[0]
        conn.close()
        assert abs(total - 2.0) < TOLERANCE


# ============================================================
# Test 3: FIFO lots (using real engine)
# ============================================================

class TestFIFOLots:

    def test_lot_count(self, db_with_fifo):
        conn = sqlite3.connect(db_with_fifo)
        count = conn.execute("SELECT COUNT(*) FROM fifo_lots").fetchone()[0]
        conn.close()
        assert count == 4, "4 BUY transactions → 4 FIFO lots"

    def test_first_btc_lot_remaining(self, db_with_fifo):
        """First BTC lot: 0.1 bought, 0.03 + 0.02 sold via FIFO → 0.05 remaining."""
        conn = sqlite3.connect(db_with_fifo)
        remaining = conn.execute(
            "SELECT remaining_amount FROM fifo_lots WHERE cryptocurrency='BTC' ORDER BY purchase_date LIMIT 1"
        ).fetchone()[0]
        conn.close()
        assert abs(remaining - 0.05) < TOLERANCE

    def test_second_btc_lot_untouched(self, db_with_fifo):
        """Second BTC lot: 0.05 bought, untouched (FIFO consumed from first lot)."""
        conn = sqlite3.connect(db_with_fifo)
        remaining = conn.execute(
            "SELECT remaining_amount FROM fifo_lots WHERE cryptocurrency='BTC' ORDER BY purchase_date LIMIT 1 OFFSET 1"
        ).fetchone()[0]
        conn.close()
        assert abs(remaining - 0.05) < TOLERANCE

    def test_third_btc_lot_untouched(self, db_with_fifo):
        """Third BTC lot: 0.02 bought, untouched (FIFO used older lot)."""
        conn = sqlite3.connect(db_with_fifo)
        remaining = conn.execute(
            "SELECT remaining_amount FROM fifo_lots WHERE cryptocurrency='BTC' ORDER BY purchase_date LIMIT 1 OFFSET 2"
        ).fetchone()[0]
        conn.close()
        assert abs(remaining - 0.02) < TOLERANCE

    def test_eth_lot_remaining(self, db_with_fifo):
        """ETH lot: 2.0 bought, 1.0 sold → 1.0 remaining."""
        conn = sqlite3.connect(db_with_fifo)
        remaining = conn.execute(
            "SELECT remaining_amount FROM fifo_lots WHERE cryptocurrency='ETH'"
        ).fetchone()[0]
        conn.close()
        assert abs(remaining - 1.0) < TOLERANCE


# ============================================================
# Test 4: Sale-lot matches
# ============================================================

class TestFIFOMatches:

    def test_match_count(self, db_with_fifo):
        conn = sqlite3.connect(db_with_fifo)
        count = conn.execute("SELECT COUNT(*) FROM sale_lot_matches").fetchone()[0]
        conn.close()
        assert count == 3, "3 SELL transactions → 3 matches"

    def test_no_unmatched_sales(self, db_with_fifo):
        conn = sqlite3.connect(db_with_fifo)
        unmatched = conn.execute("""
            SELECT COUNT(*) FROM transactions t
            LEFT JOIN sale_lot_matches slm ON t.id = slm.sale_transaction_id
            WHERE t.transaction_type = 'SELL' AND slm.id IS NULL
        """).fetchone()[0]
        conn.close()
        assert unmatched == 0


# ============================================================
# Test 5: Holding periods
# ============================================================

class TestHoldingPeriods:

    def test_btc_long_term_sale(self, db_with_fifo):
        """BTC sale on 2021-08-05: bought 2020-01-15 → ~568 days (long-term)."""
        conn = sqlite3.connect(db_with_fifo)
        days = conn.execute(
            "SELECT holding_period_days FROM sale_lot_matches WHERE sale_date LIKE '2021-08-05%' AND cryptocurrency='BTC'"
        ).fetchone()[0]
        conn.close()
        assert days >= 365

    def test_eth_long_term_sale(self, db_with_fifo):
        """ETH sale on 2021-11-01: bought 2020-06-10 → ~509 days (long-term)."""
        conn = sqlite3.connect(db_with_fifo)
        days = conn.execute(
            "SELECT holding_period_days FROM sale_lot_matches WHERE sale_date LIKE '2021-11-01%' AND cryptocurrency='ETH'"
        ).fetchone()[0]
        conn.close()
        assert days >= 365

    def test_fifo_uses_oldest_lot(self, db_with_fifo):
        """BTC sale on 2022-02-15: FIFO uses lot from 2020-01-15 (oldest), not 2021-09-12."""
        conn = sqlite3.connect(db_with_fifo)
        row = conn.execute(
            "SELECT purchase_date, holding_period_days FROM sale_lot_matches WHERE sale_date LIKE '2022-02-15%' AND cryptocurrency='BTC'"
        ).fetchone()
        conn.close()
        assert row[0].startswith('2020-01-15'), f"FIFO should use oldest lot, got {row[0]}"
        assert row[1] >= 365, "Should be long-term (from oldest lot)"


# ============================================================
# Test 6: Gain/loss
# ============================================================

class TestGainLoss:

    def test_btc_lt_sale_profitable(self, db_with_fifo):
        """BTC long-term sale: bought @€6500, sold @€32000 → profit."""
        conn = sqlite3.connect(db_with_fifo)
        gl = conn.execute(
            "SELECT gain_loss FROM sale_lot_matches WHERE sale_date LIKE '2021-08-05%' AND cryptocurrency='BTC'"
        ).fetchone()[0]
        conn.close()
        assert gl > 0

    def test_eth_sale_very_profitable(self, db_with_fifo):
        """ETH sale: bought @€210, sold @€3800 → big profit (>€3000)."""
        conn = sqlite3.connect(db_with_fifo)
        gl = conn.execute(
            "SELECT gain_loss FROM sale_lot_matches WHERE sale_date LIKE '2021-11-01%' AND cryptocurrency='ETH'"
        ).fetchone()[0]
        conn.close()
        assert gl > 3000

    def test_fifo_sale_uses_old_cost_basis(self, db_with_fifo):
        """BTC sale via FIFO: uses cost basis from oldest lot (€6500), not recent (€38000)."""
        conn = sqlite3.connect(db_with_fifo)
        row = conn.execute(
            "SELECT purchase_price_per_unit, gain_loss FROM sale_lot_matches WHERE sale_date LIKE '2022-02-15%' AND cryptocurrency='BTC'"
        ).fetchone()
        conn.close()
        assert abs(row[0] - 6500.0) < TOLERANCE, "Should use €6500 cost basis from oldest lot"
        assert row[1] > 0, "Should be profitable (bought @€6500, sold @€35000)"


# ============================================================
# Test 7: Tax classification (PT rules)
# ============================================================

class TestTaxClassification:

    def test_all_sales_exempt(self, db_with_fifo):
        """All 3 sales are long-term (FIFO always uses oldest lots first)."""
        conn = sqlite3.connect(db_with_fifo)
        exempt = conn.execute(
            "SELECT COUNT(*) FROM sale_lot_matches WHERE holding_period_days >= 365"
        ).fetchone()[0]
        taxable = conn.execute(
            "SELECT COUNT(*) FROM sale_lot_matches WHERE holding_period_days < 365"
        ).fetchone()[0]
        conn.close()
        assert exempt == 3
        assert taxable == 0

    def test_total_exempt_gain_positive(self, db_with_fifo):
        conn = sqlite3.connect(db_with_fifo)
        total = conn.execute(
            "SELECT SUM(gain_loss) FROM sale_lot_matches WHERE holding_period_days >= 365"
        ).fetchone()[0]
        conn.close()
        assert total > 0


# ============================================================
# Test 8: Reproducibility
# ============================================================

class TestReproducibility:

    def test_recalculation_deterministic(self, db_with_fifo):
        """Running FIFO twice produces identical results."""
        conn = sqlite3.connect(db_with_fifo)
        result_a = conn.execute(
            "SELECT SUM(gain_loss), COUNT(*), SUM(holding_period_days) FROM sale_lot_matches"
        ).fetchone()
        conn.close()

        # Recalculate
        from calculators.crypto_fifo_tracker import CryptoFIFOTracker
        tracker = CryptoFIFOTracker(db_with_fifo)
        tracker.calculate_fifo_lots('BTC')
        tracker.calculate_fifo_lots('ETH')
        tracker.close()

        conn = sqlite3.connect(db_with_fifo)
        result_b = conn.execute(
            "SELECT SUM(gain_loss), COUNT(*), SUM(holding_period_days) FROM sale_lot_matches"
        ).fetchone()
        conn.close()

        assert abs(result_b[0] - result_a[0]) < TOLERANCE
        assert result_b[1] == result_a[1]
        assert result_b[2] == result_a[2]


# ============================================================
# Test 9: Edge cases
# ============================================================

class TestEdgeCases:

    def test_sell_more_than_available(self, db_path):
        """Selling more than purchased should still work (unmatched warning)."""
        conn = sqlite3.connect(db_path)
        conn.execute(
            """INSERT INTO transactions
               (transaction_date, transaction_type, cryptocurrency, amount, price_per_unit,
                total_value, fee_amount, exchange_name)
               VALUES ('2020-01-01', 'BUY', 'XRP', 100, 0.25, 25.0, 0.1, 'Test')""")
        conn.execute(
            """INSERT INTO transactions
               (transaction_date, transaction_type, cryptocurrency, amount, price_per_unit,
                total_value, fee_amount, exchange_name)
               VALUES ('2020-06-01', 'SELL', 'XRP', 150, 0.50, 75.0, 0.2, 'Test')""")
        conn.commit()
        conn.close()

        from calculators.crypto_fifo_tracker import CryptoFIFOTracker
        tracker = CryptoFIFOTracker(db_path)
        tracker.calculate_fifo_lots('XRP')
        tracker.close()

        conn = sqlite3.connect(db_path)
        matched = conn.execute("SELECT SUM(amount_sold) FROM sale_lot_matches WHERE cryptocurrency='XRP'").fetchone()[0]
        conn.close()
        # Only 100 can be matched (the amount purchased)
        assert abs(matched - 100.0) < TOLERANCE

    def test_zero_fee_transaction(self, db_path):
        """Transactions with zero fees should work correctly."""
        conn = sqlite3.connect(db_path)
        conn.execute(
            """INSERT INTO transactions
               (transaction_date, transaction_type, cryptocurrency, amount, price_per_unit,
                total_value, fee_amount, exchange_name)
               VALUES ('2020-01-01', 'BUY', 'DOT', 10, 5.0, 50.0, 0, 'Test')""")
        conn.execute(
            """INSERT INTO transactions
               (transaction_date, transaction_type, cryptocurrency, amount, price_per_unit,
                total_value, fee_amount, exchange_name)
               VALUES ('2021-06-01', 'SELL', 'DOT', 10, 10.0, 100.0, 0, 'Test')""")
        conn.commit()
        conn.close()

        from calculators.crypto_fifo_tracker import CryptoFIFOTracker
        tracker = CryptoFIFOTracker(db_path)
        tracker.calculate_fifo_lots('DOT')
        tracker.close()

        conn = sqlite3.connect(db_path)
        gl = conn.execute("SELECT gain_loss FROM sale_lot_matches WHERE cryptocurrency='DOT'").fetchone()[0]
        conn.close()
        assert abs(gl - 50.0) < TOLERANCE, "Gain should be €50 (bought €50, sold €100, no fees)"

    def test_dust_amount_lot(self, db_path):
        """Very small remaining amounts (dust) should not cause issues."""
        conn = sqlite3.connect(db_path)
        conn.execute(
            """INSERT INTO transactions
               (transaction_date, transaction_type, cryptocurrency, amount, price_per_unit,
                total_value, fee_amount, exchange_name)
               VALUES ('2020-01-01', 'BUY', 'ADA', 0.00000001, 1.0, 0.00000001, 0, 'Test')""")
        conn.execute(
            """INSERT INTO transactions
               (transaction_date, transaction_type, cryptocurrency, amount, price_per_unit,
                total_value, fee_amount, exchange_name)
               VALUES ('2020-06-01', 'SELL', 'ADA', 0.00000001, 2.0, 0.00000002, 0, 'Test')""")
        conn.commit()
        conn.close()

        from calculators.crypto_fifo_tracker import CryptoFIFOTracker
        tracker = CryptoFIFOTracker(db_path)
        tracker.calculate_fifo_lots('ADA')
        tracker.close()

        conn = sqlite3.connect(db_path)
        remaining = conn.execute(
            "SELECT remaining_amount FROM fifo_lots WHERE cryptocurrency='ADA'"
        ).fetchone()[0]
        conn.close()
        assert remaining < 1e-7, "Dust should be consumed"

    def test_multiple_lots_consumed_in_single_sale(self, db_path):
        """A single sale that spans multiple lots."""
        conn = sqlite3.connect(db_path)
        # Three small buys
        conn.execute(
            """INSERT INTO transactions
               (transaction_date, transaction_type, cryptocurrency, amount, price_per_unit,
                total_value, fee_amount, exchange_name)
               VALUES ('2020-01-01', 'BUY', 'SOL', 1.0, 10.0, 10.0, 0, 'Test')""")
        conn.execute(
            """INSERT INTO transactions
               (transaction_date, transaction_type, cryptocurrency, amount, price_per_unit,
                total_value, fee_amount, exchange_name)
               VALUES ('2020-02-01', 'BUY', 'SOL', 1.0, 20.0, 20.0, 0, 'Test')""")
        conn.execute(
            """INSERT INTO transactions
               (transaction_date, transaction_type, cryptocurrency, amount, price_per_unit,
                total_value, fee_amount, exchange_name)
               VALUES ('2020-03-01', 'BUY', 'SOL', 1.0, 30.0, 30.0, 0, 'Test')""")
        # One big sell consuming all three
        conn.execute(
            """INSERT INTO transactions
               (transaction_date, transaction_type, cryptocurrency, amount, price_per_unit,
                total_value, fee_amount, exchange_name)
               VALUES ('2021-06-01', 'SELL', 'SOL', 2.5, 50.0, 125.0, 0, 'Test')""")
        conn.commit()
        conn.close()

        from calculators.crypto_fifo_tracker import CryptoFIFOTracker
        tracker = CryptoFIFOTracker(db_path)
        tracker.calculate_fifo_lots('SOL')
        tracker.close()

        conn = sqlite3.connect(db_path)
        matches = conn.execute(
            "SELECT COUNT(*) FROM sale_lot_matches WHERE cryptocurrency='SOL'"
        ).fetchone()[0]
        remaining = conn.execute(
            "SELECT SUM(remaining_amount) FROM fifo_lots WHERE cryptocurrency='SOL'"
        ).fetchone()[0]
        conn.close()
        assert matches == 3, "Sale should consume from 3 lots"
        assert abs(remaining - 0.5) < TOLERANCE, "0.5 SOL should remain in third lot"


# ============================================================
# Test 10: File existence
# ============================================================

class TestFileExistence:

    def test_schema_sql_exists(self):
        path = os.path.join(os.path.dirname(__file__), '..', 'doc', 'schema.sql')
        assert os.path.exists(path)

    def test_config_exists(self):
        path = os.path.join(os.path.dirname(__file__), '..', 'config.py')
        assert os.path.exists(path)

    def test_fifo_tracker_exists(self):
        path = os.path.join(os.path.dirname(__file__), '..', 'calculators', 'crypto_fifo_tracker.py')
        assert os.path.exists(path)

    def test_import_utils_exists(self):
        path = os.path.join(os.path.dirname(__file__), '..', 'importers', 'import_utils.py')
        assert os.path.exists(path)

    def test_code_guidelines_exists(self):
        path = os.path.join(os.path.dirname(__file__), '..', 'doc', 'code_guidelines.md')
        assert os.path.exists(path)

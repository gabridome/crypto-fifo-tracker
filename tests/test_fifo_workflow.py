#!/usr/bin/env python3
"""
Crypto FIFO Tracker — Automated Tests

Tests the full workflow with sample data:
  1. Create an empty database
  2. Import sample transactions
  3. Calculate FIFO
  4. Verify results (holding periods, gain/loss, tax classification)

Usage:
    python3 tests/test_fifo_workflow.py

Exit code 0 = all tests passed, 1 = failures found.
"""

import sqlite3
import csv
import os
import sys
import tempfile
import shutil
from datetime import datetime

# ============================================================
# Test configuration
# ============================================================
TOLERANCE = 0.01  # EUR rounding tolerance
SCHEMA_FILE = os.path.join(os.path.dirname(__file__), '..', 'doc', 'schema.sql')
SAMPLE_CSV = os.path.join(os.path.dirname(__file__), '..', 'data', 'sample_transactions.csv')

# ============================================================
# Test data (embedded, independent of sample CSV)
# ============================================================
TEST_TRANSACTIONS = [
    # BTC: buy 0.1 @ €6500 on 2020-01-15
    ("2020-01-15T10:30:00+00:00", "BUY",  "BTC",  0.1,   6500.00,  650.00,  1.50, "EUR", "EUR", "TestExchange", "t001", "Test buy 1"),
    # BTC: buy 0.05 @ €5800 on 2020-03-22
    ("2020-03-22T14:15:00+00:00", "BUY",  "BTC",  0.05,  5800.00,  290.00,  0.75, "EUR", "EUR", "TestExchange", "t002", "Test buy 2"),
    # ETH: buy 2.0 @ €210 on 2020-06-10
    ("2020-06-10T09:00:00+00:00", "BUY",  "ETH",  2.0,   210.00,   420.00,  1.00, "EUR", "EUR", "TestExchange", "t003", "Test ETH buy"),
    # BTC: sell 0.03 @ €32000 on 2021-08-05 (long-term, from lot t001)
    ("2021-08-05T16:45:00+00:00", "SELL", "BTC",  0.03,  32000.00, 960.00,  2.40, "EUR", "EUR", "TestExchange", "t004", "LT BTC sale"),
    # BTC: buy 0.02 @ €38000 on 2021-09-12
    ("2021-09-12T11:20:00+00:00", "BUY",  "BTC",  0.02,  38000.00, 760.00,  1.90, "EUR", "EUR", "TestExchange", "t005", "Test buy 3"),
    # ETH: sell 1.0 @ €3800 on 2021-11-01 (long-term, from lot t003)
    ("2021-11-01T08:00:00+00:00", "SELL", "ETH",  1.0,   3800.00,  3800.00, 9.50, "EUR", "EUR", "TestExchange", "t006", "LT ETH sale"),
    # BTC: sell 0.02 @ €35000 on 2022-02-15 (short-term, from lot t005)
    ("2022-02-15T13:30:00+00:00", "SELL", "BTC",  0.02,  35000.00, 700.00,  1.75, "EUR", "EUR", "TestExchange", "t007", "ST BTC sale"),
]

# ============================================================
# Helper functions
# ============================================================
passed = 0
failed = 0

def ok(msg):
    global passed
    passed += 1
    print(f"  ✓ {msg}")

def fail(msg):
    global failed
    failed += 1
    print(f"  ✗ {msg}")

def assert_equal(actual, expected, msg, tolerance=None):
    if tolerance and isinstance(actual, (int, float)) and isinstance(expected, (int, float)):
        if abs(actual - expected) <= tolerance:
            ok(f"{msg}: {actual}")
        else:
            fail(f"{msg}: expected {expected}, got {actual} (diff={abs(actual-expected):.4f})")
    else:
        if actual == expected:
            ok(f"{msg}: {actual}")
        else:
            fail(f"{msg}: expected {expected}, got {actual}")

def assert_true(condition, msg):
    if condition:
        ok(msg)
    else:
        fail(msg)

def create_test_db(db_path):
    """Create a fresh database with the schema."""
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    c.executescript("""
    CREATE TABLE IF NOT EXISTS transactions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        transaction_date TEXT NOT NULL,
        transaction_type TEXT NOT NULL CHECK(transaction_type IN ('BUY', 'SELL', 'DEPOSIT', 'WITHDRAWAL')),
        exchange_name TEXT NOT NULL,
        cryptocurrency TEXT NOT NULL,
        amount REAL NOT NULL,
        price_per_unit REAL,
        total_value REAL,
        fee_amount REAL DEFAULT 0,
        fee_currency TEXT DEFAULT 'EUR',
        currency TEXT DEFAULT 'EUR',
        transaction_id TEXT,
        notes TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS fifo_lots (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        purchase_transaction_id INTEGER NOT NULL,
        cryptocurrency TEXT NOT NULL,
        purchase_date TEXT NOT NULL,
        original_amount REAL NOT NULL,
        remaining_amount REAL NOT NULL,
        purchase_price_per_unit REAL NOT NULL,
        cost_basis REAL NOT NULL,
        purchase_fee_total REAL DEFAULT 0,
        exchange_name TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (purchase_transaction_id) REFERENCES transactions(id)
    );
    CREATE TABLE IF NOT EXISTS sale_lot_matches (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        sale_transaction_id INTEGER NOT NULL,
        fifo_lot_id INTEGER NOT NULL,
        sale_date TEXT NOT NULL,
        purchase_date TEXT NOT NULL,
        cryptocurrency TEXT NOT NULL,
        amount_sold REAL NOT NULL,
        purchase_price_per_unit REAL NOT NULL,
        sale_price_per_unit REAL NOT NULL,
        cost_basis REAL NOT NULL,
        proceeds REAL NOT NULL,
        gain_loss REAL NOT NULL,
        holding_period_days INTEGER NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (sale_transaction_id) REFERENCES transactions(id),
        FOREIGN KEY (fifo_lot_id) REFERENCES fifo_lots(id)
    );
    CREATE INDEX IF NOT EXISTS idx_transactions_date ON transactions(transaction_date);
    CREATE INDEX IF NOT EXISTS idx_transactions_crypto ON transactions(cryptocurrency);
    CREATE INDEX IF NOT EXISTS idx_fifo_lots_crypto ON fifo_lots(cryptocurrency);
    CREATE INDEX IF NOT EXISTS idx_sale_matches_sale_date ON sale_lot_matches(sale_date);
    """)
    conn.commit()
    return conn

def insert_test_data(conn):
    """Insert test transactions."""
    c = conn.cursor()
    for t in TEST_TRANSACTIONS:
        c.execute("""INSERT INTO transactions 
            (transaction_date, transaction_type, cryptocurrency, amount, price_per_unit,
             total_value, fee_amount, fee_currency, currency, exchange_name, transaction_id, notes)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""", t)
    conn.commit()

def calculate_fifo(conn):
    """Simplified FIFO calculation for testing."""
    c = conn.cursor()
    
    # Clear existing FIFO data
    c.execute("DELETE FROM sale_lot_matches")
    c.execute("DELETE FROM fifo_lots")
    conn.commit()
    
    # Get all cryptocurrencies
    cryptos = [row[0] for row in c.execute(
        "SELECT DISTINCT cryptocurrency FROM transactions WHERE transaction_type IN ('BUY','SELL')")]
    
    for crypto in cryptos:
        # Get all transactions for this crypto, ordered by date
        rows = c.execute("""
            SELECT id, transaction_date, transaction_type, amount, price_per_unit, 
                   total_value, fee_amount, exchange_name
            FROM transactions
            WHERE cryptocurrency = ? AND transaction_type IN ('BUY', 'SELL')
            ORDER BY transaction_date, id
        """, (crypto,)).fetchall()
        
        lots = []  # list of [lot_id, purchase_tx_id, date, original, remaining, price, cost_basis, fee, exchange]
        
        for row in rows:
            tx_id, tx_date, tx_type, amount, price, total_value, fee, exchange = row
            
            if tx_type == 'BUY':
                cost_basis = (total_value or 0) + (fee or 0)
                c.execute("""INSERT INTO fifo_lots 
                    (purchase_transaction_id, cryptocurrency, purchase_date, original_amount,
                     remaining_amount, purchase_price_per_unit, cost_basis, purchase_fee_total, exchange_name)
                    VALUES (?,?,?,?,?,?,?,?,?)""",
                    (tx_id, crypto, tx_date, amount, amount, price, cost_basis, fee or 0, exchange))
                lot_id = c.lastrowid
                lots.append([lot_id, tx_id, tx_date, amount, amount, price, cost_basis, fee or 0, exchange])
            
            elif tx_type == 'SELL':
                remaining_to_sell = amount
                sale_price = price
                sale_fee = fee or 0
                # Proportional fee per unit sold
                fee_per_unit_sold = sale_fee / amount if amount > 0 else 0
                
                for lot in lots:
                    if remaining_to_sell <= 0.00000001:
                        break
                    if lot[4] <= 0.00000001:  # remaining_amount
                        continue
                    
                    # How much to consume from this lot
                    consumed = min(remaining_to_sell, lot[4])
                    
                    # Cost basis proportional
                    lot_cost_per_unit = lot[6] / lot[3]  # total cost_basis / original_amount
                    consumed_cost = consumed * lot_cost_per_unit
                    
                    # Proceeds proportional (minus proportional fee)
                    consumed_proceeds = consumed * sale_price - consumed * fee_per_unit_sold
                    
                    # Holding period
                    purchase_dt = datetime.fromisoformat(lot[2].replace('+00:00', '+00:00').split('T')[0])
                    sale_dt = datetime.fromisoformat(tx_date.split('T')[0])
                    holding_days = (sale_dt - purchase_dt).days
                    
                    gain_loss = consumed_proceeds - consumed_cost
                    
                    c.execute("""INSERT INTO sale_lot_matches
                        (sale_transaction_id, fifo_lot_id, sale_date, purchase_date,
                         cryptocurrency, amount_sold, purchase_price_per_unit, sale_price_per_unit,
                         cost_basis, proceeds, gain_loss, holding_period_days)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (tx_id, lot[0], tx_date, lot[2], crypto, consumed,
                         lot[5], sale_price, consumed_cost, consumed_proceeds, gain_loss, holding_days))
                    
                    # Update lot remaining
                    lot[4] -= consumed
                    c.execute("UPDATE fifo_lots SET remaining_amount = ? WHERE id = ?", (lot[4], lot[0]))
                    
                    remaining_to_sell -= consumed
        
    conn.commit()


# ============================================================
# Tests
# ============================================================

def test_database_creation(conn):
    """Test 1: Database has correct tables."""
    print("\n[Test 1] Database creation")
    c = conn.cursor()
    tables = [row[0] for row in c.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
    assert_equal(len(tables), 3, "Table count")
    assert_true('transactions' in tables, "transactions table exists")
    assert_true('fifo_lots' in tables, "fifo_lots table exists")
    assert_true('sale_lot_matches' in tables, "sale_lot_matches table exists")


def test_import(conn):
    """Test 2: Transactions imported correctly."""
    print("\n[Test 2] Transaction import")
    c = conn.cursor()
    
    total = c.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
    assert_equal(total, 7, "Total transaction count")
    
    buys = c.execute("SELECT COUNT(*) FROM transactions WHERE transaction_type='BUY'").fetchone()[0]
    assert_equal(buys, 4, "BUY count")
    
    sells = c.execute("SELECT COUNT(*) FROM transactions WHERE transaction_type='SELL'").fetchone()[0]
    assert_equal(sells, 3, "SELL count")
    
    btc_buys = c.execute(
        "SELECT SUM(amount) FROM transactions WHERE transaction_type='BUY' AND cryptocurrency='BTC'"
    ).fetchone()[0]
    assert_equal(btc_buys, 0.17, "BTC total bought", tolerance=TOLERANCE)
    
    eth_buys = c.execute(
        "SELECT SUM(amount) FROM transactions WHERE transaction_type='BUY' AND cryptocurrency='ETH'"
    ).fetchone()[0]
    assert_equal(eth_buys, 2.0, "ETH total bought", tolerance=TOLERANCE)


def test_fifo_lots(conn):
    """Test 3: FIFO lots created correctly."""
    print("\n[Test 3] FIFO lots")
    c = conn.cursor()
    
    lot_count = c.execute("SELECT COUNT(*) FROM fifo_lots").fetchone()[0]
    assert_equal(lot_count, 4, "FIFO lot count (4 BUY transactions)")
    
    # First BTC lot: bought 0.1, sold 0.03 (t004) + 0.02 (t007 via FIFO) → remaining 0.05
    lot1 = c.execute("""
        SELECT remaining_amount FROM fifo_lots 
        WHERE cryptocurrency='BTC' ORDER BY purchase_date LIMIT 1
    """).fetchone()[0]
    assert_equal(lot1, 0.05, "First BTC lot remaining (0.1 - 0.03 - 0.02)", tolerance=TOLERANCE)
    
    # Second BTC lot: bought 0.05, untouched → remaining 0.05
    lot2 = c.execute("""
        SELECT remaining_amount FROM fifo_lots 
        WHERE cryptocurrency='BTC' ORDER BY purchase_date LIMIT 1 OFFSET 1
    """).fetchone()[0]
    assert_equal(lot2, 0.05, "Second BTC lot remaining (untouched)", tolerance=TOLERANCE)
    
    # Third BTC lot (t005): bought 0.02, untouched (FIFO consumed from first lot) → remaining 0.02
    lot3 = c.execute("""
        SELECT remaining_amount FROM fifo_lots 
        WHERE cryptocurrency='BTC' ORDER BY purchase_date LIMIT 1 OFFSET 2
    """).fetchone()[0]
    assert_equal(lot3, 0.02, "Third BTC lot remaining (untouched, FIFO used older lot)", tolerance=TOLERANCE)
    
    # ETH lot: bought 2.0, sold 1.0 → remaining 1.0
    eth_lot = c.execute("""
        SELECT remaining_amount FROM fifo_lots WHERE cryptocurrency='ETH'
    """).fetchone()[0]
    assert_equal(eth_lot, 1.0, "ETH lot remaining (2.0 - 1.0)", tolerance=TOLERANCE)


def test_fifo_matches(conn):
    """Test 4: Sale-lot matches are correct."""
    print("\n[Test 4] Sale-lot matches")
    c = conn.cursor()
    
    match_count = c.execute("SELECT COUNT(*) FROM sale_lot_matches").fetchone()[0]
    assert_equal(match_count, 3, "Match count (3 SELL transactions)")
    
    # All sales should be fully matched
    unmatched = c.execute("""
        SELECT COUNT(*) FROM transactions t
        LEFT JOIN sale_lot_matches slm ON t.id = slm.sale_transaction_id
        WHERE t.transaction_type = 'SELL' AND slm.id IS NULL
    """).fetchone()[0]
    assert_equal(unmatched, 0, "Unmatched sales")


def test_holding_periods(conn):
    """Test 5: Holding periods calculated correctly."""
    print("\n[Test 5] Holding periods")
    c = conn.cursor()
    
    # Sale t004: bought 2020-01-15, sold 2021-08-05 → ~568 days (long-term)
    match1 = c.execute("""
        SELECT holding_period_days FROM sale_lot_matches
        WHERE sale_date LIKE '2021-08-05%' AND cryptocurrency='BTC'
    """).fetchone()
    assert_true(match1 is not None, "BTC long-term sale match found")
    assert_true(match1[0] >= 365, f"BTC long-term holding: {match1[0]} days ≥365")
    
    # Sale t006: bought 2020-06-10, sold 2021-11-01 → ~509 days (long-term)
    match2 = c.execute("""
        SELECT holding_period_days FROM sale_lot_matches
        WHERE sale_date LIKE '2021-11-01%' AND cryptocurrency='ETH'
    """).fetchone()
    assert_true(match2 is not None, "ETH long-term sale match found")
    assert_true(match2[0] >= 365, f"ETH long-term holding: {match2[0]} days ≥365")
    
    # Sale t007: FIFO matches to oldest lot (2020-01-15), sold 2022-02-15 → ~762 days (LONG-term!)
    # This is the key insight: even though t005 was bought recently, FIFO uses the OLDEST lot
    match3 = c.execute("""
        SELECT holding_period_days FROM sale_lot_matches
        WHERE sale_date LIKE '2022-02-15%' AND cryptocurrency='BTC'
    """).fetchone()
    assert_true(match3 is not None, "BTC third sale match found")
    assert_true(match3[0] >= 365, f"BTC FIFO sale from oldest lot: {match3[0]} days ≥365 (long-term!)")


def test_gain_loss(conn):
    """Test 6: Gain/loss calculated correctly."""
    print("\n[Test 6] Gain/loss")
    c = conn.cursor()
    
    # Sale t004: 0.03 BTC, bought @ €6500 + fees, sold @ €32000 - fees → profit
    match1 = c.execute("""
        SELECT gain_loss FROM sale_lot_matches
        WHERE sale_date LIKE '2021-08-05%' AND cryptocurrency='BTC'
    """).fetchone()
    assert_true(match1[0] > 0, f"BTC long-term sale is profitable: €{match1[0]:.2f}")
    
    # Sale t006: 1.0 ETH, bought @ €210 + fees, sold @ €3800 - fees → big profit
    match2 = c.execute("""
        SELECT gain_loss FROM sale_lot_matches
        WHERE sale_date LIKE '2021-11-01%' AND cryptocurrency='ETH'
    """).fetchone()
    assert_true(match2[0] > 3000, f"ETH long-term sale is very profitable: €{match2[0]:.2f}")
    
    # Sale t007: 0.02 BTC from FIFO lot at €6500, sold @ €35000 → profit (FIFO used oldest lot!)
    match3 = c.execute("""
        SELECT gain_loss FROM sale_lot_matches
        WHERE sale_date LIKE '2022-02-15%' AND cryptocurrency='BTC'
    """).fetchone()
    assert_true(match3[0] > 0, f"BTC FIFO sale profitable (old lot @ €6500, sold @ €35000): €{match3[0]:.2f}")


def test_tax_classification(conn):
    """Test 7: Tax classification (exempt vs taxable)."""
    print("\n[Test 7] Tax classification (PT rules: ≥365 days = exempt)")
    c = conn.cursor()
    
    # All 3 sales are long-term (FIFO always consumes oldest lots first)
    exempt = c.execute("""
        SELECT COUNT(*) FROM sale_lot_matches WHERE holding_period_days >= 365
    """).fetchone()[0]
    assert_equal(exempt, 3, "Exempt operations (all long-term due to FIFO)")
    
    taxable = c.execute("""
        SELECT COUNT(*) FROM sale_lot_matches WHERE holding_period_days < 365
    """).fetchone()[0]
    assert_equal(taxable, 0, "Taxable operations (none — FIFO used oldest lots)")
    
    exempt_gain = c.execute("""
        SELECT SUM(gain_loss) FROM sale_lot_matches WHERE holding_period_days >= 365
    """).fetchone()[0]
    assert_true(exempt_gain > 0, f"Exempt total gain: €{exempt_gain:.2f}")


def test_fifo_order(conn):
    """Test 8: FIFO consumes oldest lot first."""
    print("\n[Test 8] FIFO order (oldest lot first)")
    c = conn.cursor()
    
    # The BTC sale on 2022-02-15 should match to the lot from 2021-09-12 (third lot)
    # NOT from 2020-01-15 (first lot) because the first lot still has remaining
    # Actually: sale is 0.02 BTC. The third BTC lot (t005) is also 0.02 BTC.
    # But FIFO should consume from the OLDEST lot first.
    # Lot 1 (2020-01-15): remaining = 0.1 - 0.03 = 0.07 → FIFO takes from here first
    
    match = c.execute("""
        SELECT purchase_date, purchase_price_per_unit FROM sale_lot_matches
        WHERE sale_date LIKE '2022-02-15%' AND cryptocurrency='BTC'
    """).fetchone()
    
    # The 2022-02-15 sale of 0.02 BTC: FIFO should use lot from 2020-01-15 (oldest with remaining)
    assert_true(match[0].startswith('2020-01-15'), 
                f"FIFO used oldest lot: purchase_date={match[0]}")
    assert_equal(match[1], 6500.0, "Purchase price from oldest lot (€6500)", tolerance=TOLERANCE)


def test_reproducibility(conn, db_path):
    """Test 9: FIFO is deterministic (same input → same output)."""
    print("\n[Test 9] Reproducibility")
    c = conn.cursor()
    
    # Get results hash
    result_a = c.execute(
        "SELECT SUM(gain_loss), COUNT(*), SUM(holding_period_days) FROM sale_lot_matches"
    ).fetchone()
    
    # Recalculate
    calculate_fifo(conn)
    
    result_b = c.execute(
        "SELECT SUM(gain_loss), COUNT(*), SUM(holding_period_days) FROM sale_lot_matches"
    ).fetchone()
    
    assert_equal(result_b[0], result_a[0], "Total gain/loss unchanged after recalculation", tolerance=TOLERANCE)
    assert_equal(result_b[1], result_a[1], "Match count unchanged")
    assert_equal(result_b[2], result_a[2], "Total holding days unchanged")


# ============================================================
# Main
# ============================================================

def main():
    global passed, failed
    
    print("=" * 60)
    print("  Crypto FIFO Tracker — Automated Tests")
    print("=" * 60)
    
    # Create temp database
    tmp_dir = tempfile.mkdtemp()
    db_path = os.path.join(tmp_dir, 'test_fifo.db')
    
    try:
        # Setup
        conn = create_test_db(db_path)
        insert_test_data(conn)
        calculate_fifo(conn)
        
        # Run tests
        test_database_creation(conn)
        test_import(conn)
        test_fifo_lots(conn)
        test_fifo_matches(conn)
        test_holding_periods(conn)
        test_gain_loss(conn)
        test_tax_classification(conn)
        test_fifo_order(conn)
        test_reproducibility(conn, db_path)
        
        conn.close()
    finally:
        shutil.rmtree(tmp_dir)
    
    # Summary
    total = passed + failed
    print(f"\n{'=' * 60}")
    if failed == 0:
        print(f"  ✓ All {total} tests passed!")
    else:
        print(f"  ✗ {failed}/{total} tests FAILED")
    print(f"{'=' * 60}")
    
    return 0 if failed == 0 else 1


if __name__ == '__main__':
    sys.exit(main())

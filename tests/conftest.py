"""Shared fixtures for pytest."""

import os
import sqlite3
import tempfile
import shutil
import pytest

SCHEMA_FILE = os.path.join(os.path.dirname(__file__), '..', 'doc', 'schema.sql')


@pytest.fixture
def db_path():
    """Create a temporary database from doc/schema.sql, yield its path, then clean up."""
    tmp_dir = tempfile.mkdtemp()
    path = os.path.join(tmp_dir, 'test_fifo.db')

    assert os.path.exists(SCHEMA_FILE), f"Schema file not found: {SCHEMA_FILE}"

    conn = sqlite3.connect(path)
    with open(SCHEMA_FILE) as f:
        conn.executescript(f.read())
    conn.commit()
    conn.close()

    yield path

    shutil.rmtree(tmp_dir)


# Standard test transactions covering:
# - Multiple cryptos (BTC, ETH)
# - Multiple buys and sells
# - Long-term holds (>365 days)
# - FIFO ordering (oldest lot consumed first)
TEST_TRANSACTIONS = [
    # (date, type, crypto, amount, price, total_value, fee, fee_cur, cur, exchange, tx_id, notes)
    ("2020-01-15T10:30:00+00:00", "BUY",  "BTC", 0.1,   6500.00,  650.00,  1.50, "EUR", "EUR", "TestExchange", "t001", "Test buy 1"),
    ("2020-03-22T14:15:00+00:00", "BUY",  "BTC", 0.05,  5800.00,  290.00,  0.75, "EUR", "EUR", "TestExchange", "t002", "Test buy 2"),
    ("2020-06-10T09:00:00+00:00", "BUY",  "ETH", 2.0,   210.00,   420.00,  1.00, "EUR", "EUR", "TestExchange", "t003", "Test ETH buy"),
    ("2021-08-05T16:45:00+00:00", "SELL", "BTC", 0.03,  32000.00, 960.00,  2.40, "EUR", "EUR", "TestExchange", "t004", "LT BTC sale"),
    ("2021-09-12T11:20:00+00:00", "BUY",  "BTC", 0.02,  38000.00, 760.00,  1.90, "EUR", "EUR", "TestExchange", "t005", "Test buy 3"),
    ("2021-11-01T08:00:00+00:00", "SELL", "ETH", 1.0,   3800.00,  3800.00, 9.50, "EUR", "EUR", "TestExchange", "t006", "LT ETH sale"),
    ("2022-02-15T13:30:00+00:00", "SELL", "BTC", 0.02,  35000.00, 700.00,  1.75, "EUR", "EUR", "TestExchange", "t007", "ST BTC sale"),
]


@pytest.fixture
def db_with_transactions(db_path):
    """Database with test transactions loaded."""
    conn = sqlite3.connect(db_path)
    for t in TEST_TRANSACTIONS:
        conn.execute(
            """INSERT INTO transactions
               (transaction_date, transaction_type, cryptocurrency, amount, price_per_unit,
                total_value, fee_amount, fee_currency, currency, exchange_name, transaction_id, notes)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""", t)
    conn.commit()
    conn.close()
    return db_path


@pytest.fixture
def db_with_fifo(db_with_transactions):
    """Database with transactions and FIFO calculated using the real engine."""
    import sys
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
    from calculators.crypto_fifo_tracker import CryptoFIFOTracker

    tracker = CryptoFIFOTracker(db_with_transactions)
    tracker.calculate_fifo_lots('BTC')
    tracker.calculate_fifo_lots('ETH')
    tracker.close()
    return db_with_transactions

-- ============================================================
-- Crypto FIFO Tracker — Database Schema
-- SQLite3
--
-- Usage:
--   sqlite3 data/crypto_fifo.db < doc/schema.sql
--
-- Or automatically via: ./setup.sh
-- ============================================================

-- Main transactions table
-- All BUY, SELL, DEPOSIT, WITHDRAWAL from every exchange
CREATE TABLE IF NOT EXISTS transactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    transaction_date TEXT NOT NULL,                -- ISO 8601 (YYYY-MM-DDTHH:MM:SS+00:00)
    transaction_type TEXT NOT NULL                 -- BUY, SELL, DEPOSIT, WITHDRAWAL
        CHECK(transaction_type IN ('BUY', 'SELL', 'DEPOSIT', 'WITHDRAWAL')),
    exchange_name TEXT NOT NULL,                   -- Binance, Coinbase, Kraken, etc.
    cryptocurrency TEXT NOT NULL,                  -- BTC, ETH, USDC, etc.
    amount REAL NOT NULL,                          -- Quantity of crypto
    price_per_unit REAL,                           -- Price per unit in EUR
    total_value REAL,                              -- Gross value BEFORE fees (amount × price)
    fee_amount REAL DEFAULT 0,                     -- Fee amount (separate from total_value)
    fee_currency TEXT DEFAULT 'EUR',               -- Fee currency
    currency TEXT DEFAULT 'EUR',                   -- Transaction currency
    transaction_id TEXT,                           -- Exchange-specific transaction ID
    notes TEXT,                                    -- Free text (e.g. "Swap BTC→ETH")
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- FIFO lots table
-- Each BUY creates one lot; lots are consumed by SELL in chronological order
CREATE TABLE IF NOT EXISTS fifo_lots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    purchase_transaction_id INTEGER NOT NULL,      -- FK to transactions.id
    cryptocurrency TEXT NOT NULL,
    purchase_date TEXT NOT NULL,
    original_amount REAL NOT NULL,                 -- Original quantity purchased
    remaining_amount REAL NOT NULL,                -- Quantity still available for FIFO
    purchase_price_per_unit REAL NOT NULL,         -- Price per unit at purchase (EUR)
    cost_basis REAL NOT NULL,                      -- Total cost (value + fees)
    purchase_fee_total REAL DEFAULT 0,             -- Total fee on this purchase
    exchange_name TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (purchase_transaction_id) REFERENCES transactions(id)
);

-- Sale-to-lot matches
-- Each SELL is matched to one or more FIFO lots (oldest first)
CREATE TABLE IF NOT EXISTS sale_lot_matches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sale_transaction_id INTEGER NOT NULL,          -- FK to transactions.id (the SELL)
    fifo_lot_id INTEGER NOT NULL,                  -- FK to fifo_lots.id (the consumed lot)
    sale_date TEXT NOT NULL,
    purchase_date TEXT NOT NULL,
    cryptocurrency TEXT NOT NULL,
    amount_sold REAL NOT NULL,                     -- Quantity consumed from this lot
    purchase_price_per_unit REAL NOT NULL,
    sale_price_per_unit REAL NOT NULL,
    cost_basis REAL NOT NULL,                      -- Proportional cost for amount_sold
    proceeds REAL NOT NULL,                        -- Proportional proceeds for amount_sold
    gain_loss REAL NOT NULL,                       -- proceeds - cost_basis
    holding_period_days INTEGER NOT NULL,          -- sale_date - purchase_date (in days)
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (sale_transaction_id) REFERENCES transactions(id),
    FOREIGN KEY (fifo_lot_id) REFERENCES fifo_lots(id)
);

-- ============================================================
-- Indexes (for query performance)
-- ============================================================
CREATE INDEX IF NOT EXISTS idx_transactions_date ON transactions(transaction_date);
CREATE INDEX IF NOT EXISTS idx_transactions_crypto ON transactions(cryptocurrency);
CREATE INDEX IF NOT EXISTS idx_transactions_exchange ON transactions(exchange_name);
CREATE INDEX IF NOT EXISTS idx_transactions_type ON transactions(transaction_type);
CREATE INDEX IF NOT EXISTS idx_fifo_lots_crypto ON fifo_lots(cryptocurrency);
CREATE INDEX IF NOT EXISTS idx_fifo_lots_remaining ON fifo_lots(remaining_amount);
CREATE INDEX IF NOT EXISTS idx_sale_matches_crypto ON sale_lot_matches(cryptocurrency);
CREATE INDEX IF NOT EXISTS idx_sale_matches_sale_date ON sale_lot_matches(sale_date);

-- ============================================================
-- Views (convenience queries)
-- ============================================================

-- Summary per exchange / crypto / type
CREATE VIEW IF NOT EXISTS v_exchange_summary AS
SELECT exchange_name, cryptocurrency, transaction_type,
       COUNT(*) as transaction_count,
       SUM(amount) as total_amount,
       SUM(total_value) as total_value,
       SUM(fee_amount) as total_fees
FROM transactions
GROUP BY exchange_name, cryptocurrency, transaction_type;

-- Current FIFO balances (remaining holdings)
CREATE VIEW IF NOT EXISTS v_fifo_balances AS
SELECT cryptocurrency, COUNT(*) as lot_count,
       SUM(remaining_amount) as total_remaining,
       SUM(cost_basis * (remaining_amount / original_amount)) as remaining_cost_basis
FROM fifo_lots WHERE remaining_amount > 0
GROUP BY cryptocurrency;

-- Annual gains summary
CREATE VIEW IF NOT EXISTS v_annual_gains AS
SELECT strftime('%Y', sale_date) as year, cryptocurrency,
       COUNT(*) as sale_count, SUM(amount_sold) as total_sold,
       SUM(proceeds) as total_proceeds, SUM(cost_basis) as total_cost_basis,
       SUM(gain_loss) as total_gain_loss,
       SUM(CASE WHEN holding_period_days >= 365 THEN 1 ELSE 0 END) as long_term_count,
       SUM(CASE WHEN holding_period_days < 365 THEN 1 ELSE 0 END) as short_term_count
FROM sale_lot_matches GROUP BY year, cryptocurrency;

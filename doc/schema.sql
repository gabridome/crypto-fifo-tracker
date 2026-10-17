-- Crypto FIFO Tracker Database Schema
-- SQLite3
-- Last updated: 2026-03-03

-- Main transactions table
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
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    -- Source tracking (added March 2026)
    source TEXT,              -- CSV filename or 'web_manual_entry'
    imported_at TEXT,         -- ISO timestamp of import
    record_hash TEXT,         -- SHA256 for dedup and audit
    swap_group_id TEXT                             -- both legs of one swap share this
);

-- FIFO lots table
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

-- Sale to lot matches table
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

-- Swap lot transfers table: lot inheritance across a deferred crypto-to-crypto
-- swap. The acquisition date and cost of the asset given up travel to the
-- asset received; the gain does not. One row per lot slice moved.
-- CHECKs on the TEXT columns: NOT NULL alone lets the empty string through,
-- and task 9's own fallback (swap_takes.get(group, '')) can produce exactly
-- that for to_cryptocurrency when a swap's BUY leg never imported — a row
-- that inserts silently, consumes a lot, and realizes no gain for it.
CREATE TABLE IF NOT EXISTS swap_lot_transfers (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    swap_group_id        TEXT NOT NULL CHECK (swap_group_id <> ''),
    source_fifo_lot_id   INTEGER NOT NULL,
    from_cryptocurrency  TEXT NOT NULL CHECK (from_cryptocurrency <> ''),
    to_cryptocurrency    TEXT NOT NULL CHECK (to_cryptocurrency <> ''),
    swap_date            TEXT NOT NULL,
    source_purchase_date TEXT NOT NULL,
    amount_from          REAL NOT NULL CHECK (amount_from > 0),
    cost_basis           REAL NOT NULL CHECK (cost_basis >= 0),
    -- TEXT + explicit default, not TIMESTAMP DEFAULT CURRENT_TIMESTAMP like
    -- the rest of this file: TIMESTAMP has NUMERIC affinity in SQLite, so a
    -- stored ISO datetime string would be subject to silent coercion. TEXT
    -- says what it actually is. Deliberate — do not "uniform" it back.
    created_at           TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (source_fifo_lot_id) REFERENCES fifo_lots(id)
);

-- Indexes for performance
CREATE INDEX IF NOT EXISTS idx_transactions_date ON transactions(transaction_date);
CREATE INDEX IF NOT EXISTS idx_transactions_crypto ON transactions(cryptocurrency);
CREATE INDEX IF NOT EXISTS idx_transactions_exchange ON transactions(exchange_name);
CREATE INDEX IF NOT EXISTS idx_transactions_type ON transactions(transaction_type);
-- Source tracking indexes
CREATE INDEX IF NOT EXISTS idx_transactions_source ON transactions(source);
CREATE INDEX IF NOT EXISTS idx_transactions_hash ON transactions(record_hash);
CREATE INDEX IF NOT EXISTS idx_transactions_imported_at ON transactions(imported_at);
CREATE INDEX IF NOT EXISTS idx_transactions_swap_group ON transactions(swap_group_id);
CREATE INDEX IF NOT EXISTS idx_fifo_lots_crypto ON fifo_lots(cryptocurrency);
CREATE INDEX IF NOT EXISTS idx_fifo_lots_remaining ON fifo_lots(remaining_amount);
CREATE INDEX IF NOT EXISTS idx_sale_matches_crypto ON sale_lot_matches(cryptocurrency);
CREATE INDEX IF NOT EXISTS idx_sale_matches_sale_date ON sale_lot_matches(sale_date);
CREATE INDEX IF NOT EXISTS idx_swap_lot_transfers_group
    ON swap_lot_transfers(swap_group_id);
-- Targets swap_lot_transfers.from_cryptocurrency, not to_cryptocurrency:
-- the DELETE in task 9 filters on the give side and runs on every engine
-- pass for every crypto; the take side is already covered by the group index.
CREATE INDEX IF NOT EXISTS idx_swap_lot_transfers_from
    ON swap_lot_transfers(from_cryptocurrency);
-- The only idempotency invariant expressible here: one lot moves into one
-- swap group's inheritance at most once. A swap group is exactly one SELL
-- (see importers/non_btc_valuation.py:_swap_id), and FIFO consumption for
-- one SELL visits each lot once, so this cannot be violated by a correct
-- run — only by a bug re-running consumption for the same group.
CREATE UNIQUE INDEX IF NOT EXISTS idx_swap_lot_transfers_unique_group_lot
    ON swap_lot_transfers(swap_group_id, source_fifo_lot_id);

-- View: Transaction summary by exchange
CREATE VIEW IF NOT EXISTS v_exchange_summary AS
SELECT 
    exchange_name,
    cryptocurrency,
    transaction_type,
    COUNT(*) as transaction_count,
    SUM(amount) as total_amount,
    SUM(total_value) as total_value,
    SUM(fee_amount) as total_fees
FROM transactions
GROUP BY exchange_name, cryptocurrency, transaction_type;

-- View: FIFO remaining balances
CREATE VIEW IF NOT EXISTS v_fifo_balances AS
SELECT 
    cryptocurrency,
    COUNT(*) as lot_count,
    SUM(remaining_amount) as total_remaining,
    SUM(cost_basis * (remaining_amount / original_amount)) as remaining_cost_basis
FROM fifo_lots
WHERE remaining_amount > 0
GROUP BY cryptocurrency;

-- View: Annual gains summary
CREATE VIEW IF NOT EXISTS v_annual_gains AS
SELECT 
    strftime('%Y', sale_date) as year,
    cryptocurrency,
    COUNT(*) as sale_count,
    SUM(amount_sold) as total_sold,
    SUM(proceeds) as total_proceeds,
    SUM(cost_basis) as total_cost_basis,
    SUM(gain_loss) as total_gain_loss,
    SUM(CASE WHEN holding_period_days >= 365 THEN 1 ELSE 0 END) as long_term_count,
    SUM(CASE WHEN holding_period_days < 365 THEN 1 ELSE 0 END) as short_term_count
FROM sale_lot_matches
GROUP BY year, cryptocurrency;

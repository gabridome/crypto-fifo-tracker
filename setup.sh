#!/usr/bin/env bash
#
# Crypto FIFO Tracker — Project Setup
#
# Creates directory structure, virtual environment, installs dependencies,
# and initializes an empty SQLite database with the correct schema.
#
# Usage:
#   chmod +x setup.sh
#   ./setup.sh              # default setup
#   ./setup.sh --no-venv    # skip virtual environment creation
#
# Requirements: Python 3.11+ and pip
#
# License: MIT
# ============================================================

set -e  # exit on error

# ---- Colors ------------------------------------------------
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

ok()   { echo -e "${GREEN}✓${NC} $1"; }
warn() { echo -e "${YELLOW}⚠${NC} $1"; }
err()  { echo -e "${RED}✗${NC} $1"; }
info() { echo -e "${CYAN}→${NC} $1"; }

# ---- Parse arguments ---------------------------------------
SKIP_VENV=false
for arg in "$@"; do
    case $arg in
        --no-venv) SKIP_VENV=true ;;
        --help|-h)
            echo "Usage: ./setup.sh [--no-venv]"
            echo "  --no-venv   Skip virtual environment creation"
            exit 0 ;;
    esac
done

# ---- Header ------------------------------------------------
echo ""
echo "============================================================"
echo "  Crypto FIFO Tracker — Setup"
echo "============================================================"
echo ""

# ---- Check Python ------------------------------------------
info "Checking Python..."

if command -v python3 &> /dev/null; then
    PYTHON=python3
elif command -v python &> /dev/null; then
    PYTHON=python
else
    err "Python not found. Please install Python 3.11+ first."
    echo "  macOS:   brew install python@3.13"
    echo "  Ubuntu:  sudo apt install python3 python3-pip python3-venv"
    echo "  Windows: https://www.python.org/downloads/"
    exit 1
fi

PYVER=$($PYTHON --version 2>&1 | awk '{print $2}')
PYMAJOR=$(echo "$PYVER" | cut -d. -f1)
PYMINOR=$(echo "$PYVER" | cut -d. -f2)

if [ "$PYMAJOR" -lt 3 ] || ([ "$PYMAJOR" -eq 3 ] && [ "$PYMINOR" -lt 11 ]); then
    err "Python $PYVER found, but 3.11+ is required."
    exit 1
fi

ok "Python $PYVER"

# ---- Create directories -----------------------------------
info "Creating directory structure..."

DIRS=(
    "calculators"
    "importers"
    "data"
    "data/reports"
    "data/backups"
    "data/supporting_documents"
    "doc"
    "doc/pt"
    "doc/en"
    "tests"
)

for dir in "${DIRS[@]}"; do
    mkdir -p "$dir"
done

ok "Directories created"

# ---- Virtual environment -----------------------------------
if [ "$SKIP_VENV" = false ]; then
    if [ -d "venv" ]; then
        warn "Virtual environment already exists (venv/)"
    else
        info "Creating virtual environment..."
        $PYTHON -m venv venv
        ok "Virtual environment created"
    fi

    info "Activating virtual environment..."
    source venv/bin/activate
    ok "Activated ($(which python))"

    info "Upgrading pip..."
    pip install --upgrade pip --quiet
    ok "pip upgraded"
else
    warn "Skipping virtual environment (--no-venv)"
fi

# ---- Install dependencies ----------------------------------
info "Installing Python packages..."

if [ -f "requirements.txt" ]; then
    pip install -r requirements.txt --quiet
    ok "Packages installed from requirements.txt"
else
    pip install pandas pytz openpyxl requests --quiet
    ok "Packages installed (pandas, pytz, openpyxl, requests)"
fi

# ---- Verify SQLite -----------------------------------------
info "Checking SQLite..."
$PYTHON -c "import sqlite3; print(f'SQLite {sqlite3.sqlite_version}')" 2>/dev/null
ok "SQLite built into Python"

# ---- Initialize database -----------------------------------
DB_FILE="data/crypto_fifo.db"
SCHEMA_FILE="doc/schema.sql"

if [ -f "$DB_FILE" ]; then
    warn "Database already exists ($DB_FILE) — skipping initialization"
    info "To reinitialize: rm $DB_FILE && ./setup.sh"
else
    if [ -f "$SCHEMA_FILE" ]; then
        info "Initializing database from $SCHEMA_FILE..."
        sqlite3 "$DB_FILE" < "$SCHEMA_FILE"
        ok "Database created from schema"
    else
        info "Initializing database with embedded schema..."
        $PYTHON -c "
import sqlite3

conn = sqlite3.connect('$DB_FILE')
c = conn.cursor()

c.executescript('''
-- Main transactions table
CREATE TABLE IF NOT EXISTS transactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    transaction_date TEXT NOT NULL,
    transaction_type TEXT NOT NULL CHECK(transaction_type IN (\"BUY\", \"SELL\", \"DEPOSIT\", \"WITHDRAWAL\")),
    exchange_name TEXT NOT NULL,
    cryptocurrency TEXT NOT NULL,
    amount REAL NOT NULL,
    price_per_unit REAL,
    total_value REAL,
    fee_amount REAL DEFAULT 0,
    fee_currency TEXT DEFAULT \"EUR\",
    currency TEXT DEFAULT \"EUR\",
    transaction_id TEXT,
    notes TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
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

-- Indexes
CREATE INDEX IF NOT EXISTS idx_transactions_date ON transactions(transaction_date);
CREATE INDEX IF NOT EXISTS idx_transactions_crypto ON transactions(cryptocurrency);
CREATE INDEX IF NOT EXISTS idx_transactions_exchange ON transactions(exchange_name);
CREATE INDEX IF NOT EXISTS idx_transactions_type ON transactions(transaction_type);
CREATE INDEX IF NOT EXISTS idx_fifo_lots_crypto ON fifo_lots(cryptocurrency);
CREATE INDEX IF NOT EXISTS idx_fifo_lots_remaining ON fifo_lots(remaining_amount);
CREATE INDEX IF NOT EXISTS idx_sale_matches_crypto ON sale_lot_matches(cryptocurrency);
CREATE INDEX IF NOT EXISTS idx_sale_matches_sale_date ON sale_lot_matches(sale_date);

-- Views
CREATE VIEW IF NOT EXISTS v_exchange_summary AS
SELECT exchange_name, cryptocurrency, transaction_type,
       COUNT(*) as transaction_count,
       SUM(amount) as total_amount,
       SUM(total_value) as total_value,
       SUM(fee_amount) as total_fees
FROM transactions
GROUP BY exchange_name, cryptocurrency, transaction_type;

CREATE VIEW IF NOT EXISTS v_fifo_balances AS
SELECT cryptocurrency, COUNT(*) as lot_count,
       SUM(remaining_amount) as total_remaining,
       SUM(cost_basis * (remaining_amount / original_amount)) as remaining_cost_basis
FROM fifo_lots WHERE remaining_amount > 0
GROUP BY cryptocurrency;

CREATE VIEW IF NOT EXISTS v_annual_gains AS
SELECT strftime(\"%Y\", sale_date) as year, cryptocurrency,
       COUNT(*) as sale_count, SUM(amount_sold) as total_sold,
       SUM(proceeds) as total_proceeds, SUM(cost_basis) as total_cost_basis,
       SUM(gain_loss) as total_gain_loss,
       SUM(CASE WHEN holding_period_days >= 365 THEN 1 ELSE 0 END) as long_term_count,
       SUM(CASE WHEN holding_period_days < 365 THEN 1 ELSE 0 END) as short_term_count
FROM sale_lot_matches GROUP BY year, cryptocurrency;
''')

conn.commit()
conn.close()
print('Done')
"
        ok "Database created with schema"
    fi

    # Show table info
    TABLES=$(sqlite3 "$DB_FILE" "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name;" | tr '\n' ', ' | sed 's/,$//')
    info "Tables: $TABLES"
fi

# ---- Create __init__.py files ------------------------------
touch calculators/__init__.py 2>/dev/null || true
touch importers/__init__.py 2>/dev/null || true
touch tests/__init__.py 2>/dev/null || true

# ---- Summary -----------------------------------------------
echo ""
echo "============================================================"
echo -e "  ${GREEN}Setup complete!${NC}"
echo "============================================================"
echo ""
echo "  Database:    $DB_FILE"
echo "  Python:      $PYVER"
if [ "$SKIP_VENV" = false ]; then
echo "  Venv:        venv/ (activate with: source venv/bin/activate)"
fi
echo ""
echo "  Next steps:"
echo "    1. Place your exchange CSV files in data/"
echo "    2. Update data/eurusd.csv with ECB rates"
echo "    3. Run importers:  python3 importers/import_EXCHANGE.py"
echo "    4. Calculate FIFO: python3 calculators/calculate_fifo.py"
echo "    5. Generate report: python3 calculators/generate_irs_report.py YYYY"
echo ""
echo "  Configuration: edit config.py to change country/tax rules"
echo "  Documentation: see doc/ folder"
echo ""

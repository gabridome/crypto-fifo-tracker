# macOS Setup Guide — Crypto FIFO Tracker with SQLite

Complete system setup on macOS. SQLite database (single file, no server needed).

---

## Requirements

- **macOS** (any recent version)
- **Python 3.11+** (SQLite is built into Python)
- **~30 minutes** for initial setup
- **CSV files** downloaded from exchanges

---

## Step 1 — Install Python (5 minutes)

### Check if Python is already installed

Open **Terminal** (`Cmd + Space`, type "Terminal"):

```bash
python3 --version
```

If it shows `Python 3.11.x` or higher, skip to Step 2.

### Install Python

**Option A: Homebrew (recommended)**

```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
brew install python@3.13
```

**Option B: python.org**

1. Download from https://www.python.org/downloads/macos/
2. Install the `.pkg`
3. Verify: `python3 --version`

---

## Step 2 — Create the project structure (2 minutes)

```bash
mkdir ~/crypto_project
cd ~/crypto_project

mkdir -p calculators
mkdir -p importers
mkdir -p data
mkdir -p reports
mkdir -p doc
mkdir -p backups
mkdir -p supporting_documents
mkdir -p deprecated
```

Resulting structure:

```
~/crypto_project/
├── calculators/        ← FIFO library and calculation scripts
├── importers/          ← One importer per exchange
├── data/               ← Exchange CSV files + ECB rates
├── reports/            ← Generated reports
├── doc/                ← Documentation
├── backups/            ← Database backups
├── supporting_documents/ ← Invoices, account statements
├── deprecated/         ← Obsolete scripts (archive)
└── crypto_fifo.db      ← Database (created automatically)
```

---

## Step 3 — Install Python packages (3 minutes)

### Create a virtual environment

```bash
cd ~/crypto_project
python3 -m venv venv
source venv/bin/activate
```

You should see `(venv)` in your terminal prompt.

### Install packages

```bash
pip install pandas pytz openpyxl requests
```

Packages used:
- **pandas** — CSV reading and data manipulation
- **pytz** — timezone handling
- **openpyxl** — Excel file generation (.xlsx)
- **requests** — API calls (CryptoCompare for historical prices)

SQLite is built into Python — no separate installation needed.

---

## Step 4 — Place the Python files

Copy the project files into the correct directories:

```
~/crypto_project/
├── calculators/
│   ├── crypto_fifo_tracker.py      ← Main library
│   ├── calculate_fifo.py           ← FIFO calculation script
│   ├── generate_irs_report.py      ← IRS report generation (Excel)
│   └── update_fifo_schema.py       ← DB schema update
├── importers/
│   ├── ecb_rates.py                ← USD→EUR conversion
│   ├── import_binance_with_fees.py
│   ├── import_coinbase_prime.py
│   ├── import_coinbase_standalone.py
│   ├── import_bitstamp_with_fees.py
│   ├── import_bitfinex_ecb.py
│   ├── import_kraken_with_fees.py
│   ├── import_mtgox_with_fees.py
│   ├── import_trt_with_fees.py
│   ├── import_wirex.py
│   ├── import_revolut.py
│   ├── import_binance_card.py
│   ├── import_standard_csv.py
│   └── verify_exchange_import.py
└── reports/
    ├── generate_reports.py
    ├── generate_annual_summary.py
    └── yearly_balance.py
```

---

## Step 5 — Initialize the database (1 minute)

The database is created automatically on first use:

```bash
cd ~/crypto_project
source venv/bin/activate

python3 -c "
from calculators.crypto_fifo_tracker import CryptoFIFOTracker
tracker = CryptoFIFOTracker('crypto_fifo.db')
print('✓ Database created!')
tracker.close()
"
```

You should now see the file `crypto_fifo.db`:

```bash
ls -lh crypto_fifo.db
```

**Note**: if importers can't find the database, it's because they must be run from the project root directory (`~/crypto_project`).

---

## Step 6 — Download data from exchanges (10 minutes)

Download CSVs from each exchange and save them in `data/`:

| Exchange | Where to download | Resulting file |
|----------|------------------|----------------|
| Coinbase | Settings → Activity → Generate Report | `data/coinbase_history.csv` |
| Coinbase Prime | Activity → Orders → Download CSV | `data/coinbaseprime_orders.csv` |
| Binance | My Orders → Trade History → Export | `data/binance_trade_history_all.csv` |
| Bitstamp | Transaction History → Export | `data/bitstamp_history.csv` |
| Kraken | History → Export Ledgers | `data/kraken_ledgers.csv` |
| Bitfinex | Reports → Export | `data/bitfinex_trades.csv` |
| Wirex | Transaction History → Download | `data/wirex_YYYY.csv` |
| Revolut | Crypto → Statement → CSV | `data/revolut_crypto.csv` |

For Mt.Gox, TRT and other closed exchanges: use archived CSV files.

Also download the **ECB rates** and save as `data/eurusd.csv`.

More information on how to download exchange logs can be found in `how_reports.md`.

---

## Step 7 — Import data (10-20 minutes)

All scripts must be run from the project root directory:

```bash
cd ~/crypto_project
source venv/bin/activate
```

### Import each exchange

```bash
# Historical exchanges
python3 importers/import_mtgox_with_fees.py
python3 importers/import_bitstamp_with_fees.py
python3 importers/import_trt_with_fees.py
python3 importers/import_bitfinex_ecb.py
python3 importers/import_kraken_with_fees.py

# Recent exchanges
python3 importers/import_coinbase_standalone.py
python3 importers/import_coinbase_prime.py
python3 importers/import_binance_with_fees.py
python3 importers/import_wirex.py
python3 importers/import_revolut.py
python3 importers/import_binance_card.py

# Manual transactions (OTC, inheritance, etc.)
python3 importers/import_standard_csv.py data/otc.csv "OTC"
```

Each importer asks whether to delete existing data (DELETE option). For first-time imports, always select "1" (DELETE + reimport).

### Verify each import

```bash
python3 importers/verify_exchange_import.py "Binance"
python3 importers/verify_exchange_import.py "Coinbase"
python3 importers/verify_exchange_import.py "Coinbase Prime"
# etc.
```

---

## Step 8 — Calculate FIFO (2-5 minutes)

```bash
python3 calculators/calculate_fifo.py
```

Expected output:
```
CRYPTO FIFO TRACKER - FIFO CALCULATION
...
✓ BTC FIFO complete in XX.X seconds
Matching Statistics:
  Sales matched: X,XXX
  Long-term (≥1 year): X,XXX
  Short-term (<1 year): XXX
```

---

## Step 9 — Generate reports (1 minute)

### IRS report (for tax filing)

```bash
python3 calculators/generate_irs_report.py YYYY
```

Generates: `reports/IRS_Crypto_FIFO_YYYY.xlsx`

### Annual summary (console output)

```bash
python3 reports/generate_annual_summary.py YYYY
```

### Open the report

```bash
open reports/IRS_Crypto_FIFO_YYYY.xlsx
```

---

## Step 10 — Verify the results

### Check the database

```bash
sqlite3 crypto_fifo.db
```

```sql
-- How many transactions?
SELECT COUNT(*) FROM transactions;

-- Per exchange?
SELECT exchange_name, COUNT(*) FROM transactions GROUP BY exchange_name;

-- FIFO results?
SELECT strftime('%Y', sale_date) as year, 
       SUM(gain_loss) as gain_loss,
       SUM(CASE WHEN holding_period_days >= 365 THEN gain_loss ELSE 0 END) as exempt
FROM sale_lot_matches GROUP BY year;

.exit
```

More useful queries can be found in `useful_queries.md`.

### Install a graphical viewer (optional)

```bash
brew install --cask db-browser-for-sqlite
open -a "DB Browser for SQLite" crypto_fifo.db
```

---

## Quick reference commands

```bash
# Activate the virtual environment
cd ~/crypto_project
source venv/bin/activate

# Import
python3 importers/import_EXCHANGE.py

# Verify import
python3 importers/verify_exchange_import.py "Exchange Name"

# FIFO
python3 calculators/calculate_fifo.py

# IRS report
python3 calculators/generate_irs_report.py YYYY

# Annual summary
python3 reports/generate_annual_summary.py YYYY

# Backup database
cp crypto_fifo.db backups/crypto_fifo.db.backup_$(date +%Y%m%d)

# Quick query
sqlite3 crypto_fifo.db "SELECT COUNT(*) FROM transactions;"

# Deactivate the virtual environment
deactivate
```

---

## Troubleshooting

### "command not found: python3"

```bash
brew install python@3.13
```

### "No module named 'pandas'"

```bash
source venv/bin/activate   # is the venv active?
pip install pandas pytz openpyxl requests
```

### "Database file not found"

Make sure you run scripts from the project root directory:
```bash
cd ~/crypto_project
python3 calculators/calculate_fifo.py
```

### Wrong CSV encoding

```bash
# From ISO-8859-1 to UTF-8
iconv -f ISO-8859-1 -t UTF-8 input.csv > output.csv

# From UTF-16 to UTF-8 (typical for Wirex)
iconv -f UTF-16LE -t UTF-8 input.csv > output.csv
```

### Database locked

```bash
# Check if another process is using it
lsof crypto_fifo.db
# Close the other process and try again
```

---

## Annual update

When a new year of data arrives:

1. Update `data/eurusd.csv` with the new ECB rates
2. Add new data to the existing CSVs in `data/`
3. Reimport the modified exchanges (DELETE + reimport)
4. Verify with `verify_exchange_import.py`
5. Recalculate FIFO: `python3 calculators/calculate_fifo.py`
6. Generate report: `python3 calculators/generate_irs_report.py YYYY`
7. Backup: `cp crypto_fifo.db backups/crypto_fifo.db.backup_$(date +%Y%m%d)`

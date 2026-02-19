# CLAUDE.md — Project Knowledge Base

> This file is written by Claude for Claude. It contains everything needed
> to understand, maintain, and reconstruct this project from scratch.

## What this project is

An open-source FIFO (First In, First Out) capital gains tracker for cryptocurrency,
designed for Portuguese IRS tax filing. It imports transactions from multiple exchanges,
calculates FIFO gains/losses, classifies them as exempt (≥365 days) or taxable (<365 days),
and generates Excel reports matching the official Autoridade Tributária form layout.

**License**: MIT. **Language**: Python 3.11+. **Database**: SQLite3.
**Primary user**: Portuguese crypto taxpayers.
**Architecture**: multi-country ready via `config.py` profiles.

## Directory structure and the data/ separation principle

The core design principle is a clean separation between **code** (tracked in git,
updatable) and **personal data** (gitignored, private, backed up separately).

```
crypto-fifo-tracker/
├── config.py                    ← Country/tax configuration, DATABASE_PATH
├── setup.sh                     ← Automated setup for new users
├── .gitignore                   ← Ignores data/ except data/sample_*.csv
├── LICENSE                      ← MIT
├── CONTRIBUTING.md
├── CHANGELOG.md
├── README.md                    ← Bilingual landing page (EN/PT)
├── CLAUDE.md                    ← This file
│
├── calculators/                 ← FIFO engine and report generation
│   ├── __init__.py
│   ├── crypto_fifo_tracker.py   ← Core FIFO library (the heart of the project)
│   ├── calculate_fifo.py        ← FIFO calculation entry point
│   ├── generate_irs_report.py   ← Excel report (Anexo G1 + Anexo J sheets)
│   └── update_fifo_schema.py    ← DB schema migrations
│
├── importers/                   ← One script per exchange
│   ├── __init__.py
│   ├── ecb_rates.py             ← USD→EUR conversion via ECB CSV
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
│   ├── import_standard_csv.py   ← Generic CSV importer
│   └── verify_exchange_import.py
│
├── reports/                     ← Report generation scripts (CODE, not output)
│   ├── generate_annual_summary.py
│   ├── generate_reports.py
│   └── yearly_balance.py
│
├── data/                        ← ALL personal data (gitignored except samples)
│   ├── crypto_fifo.db           ← SQLite database
│   ├── eurusd.csv               ← ECB historical EUR/USD rates
│   ├── sample_transactions.csv  ← Fake data for testing (TRACKED in git)
│   ├── *.csv                    ← User's exchange export files
│   ├── reports/                 ← Generated .xlsx output
│   ├── backups/                 ← Database snapshots
│   └── supporting_documents/    ← Exchange statements, invoices
│
├── doc/
│   ├── schema.sql               ← Database DDL (3 tables, indexes, views)
│   ├── en/                      ← English documentation
│   │   ├── quickstart.md
│   │   ├── crypto_fifo_guide.md
│   │   ├── audit_trail_guide.md
│   │   ├── crypto_to_crypto_guide.md
│   │   ├── macos_sqlite_setup_guide.md
│   │   └── how_reports.md       ← How to download CSV from each exchange
│   └── pt/                      ← Portuguese documentation
│       ├── README.md
│       ├── quickstart.md
│       ├── crypto_fifo_guide.md
│       ├── audit_trail_guide.md
│       ├── crypto_to_crypto_guide.md
│       ├── macos_sqlite_setup_guide.md
│       ├── how_reports.md
│       └── recursos_fiscais.md  ← Portuguese tax legislation, deadlines, penalties
│
└── tests/
    ├── __init__.py
    └── test_fifo_workflow.py    ← 33 automated tests
```

**Why `data/` contains everything personal**: One gitignore rule (`data/` with
`!data/sample_*.csv` exception) keeps all private financial data out of the repo.
The user backs up one directory. Reports, backups, supporting documents — all inside.

**Why report generators are NOT inside `data/`**: Scripts like
`reports/generate_annual_summary.py` are code — they need to be tracked in git
and updated. Only their output (`.xlsx` files) goes into `data/reports/`.

## Database schema

SQLite3. Three tables, all values in EUR:

### transactions
Every BUY, SELL, DEPOSIT, WITHDRAWAL from every exchange.
```sql
id, transaction_date (ISO 8601), transaction_type, exchange_name,
cryptocurrency, amount, price_per_unit, total_value (gross, BEFORE fees),
fee_amount (separate), fee_currency, currency, transaction_id, notes, created_at
```
- `total_value` = amount × price_per_unit (gross, no fee deducted)
- `fee_amount` is always separate, never subtracted from total_value
- `transaction_type` CHECK constraint: BUY, SELL, DEPOSIT, WITHDRAWAL

### fifo_lots
Each BUY creates one lot. Lots are consumed by SELLs in chronological order.
```sql
id, purchase_transaction_id (FK), cryptocurrency, purchase_date,
original_amount, remaining_amount, purchase_price_per_unit,
cost_basis (total_value + fee), purchase_fee_total, exchange_name, created_at
```
- `cost_basis` = total_value + fee_amount (fee-inclusive purchase cost)
- `remaining_amount` decreases as SELLs consume the lot via FIFO

### sale_lot_matches
Each SELL is matched to one or more FIFO lots (oldest first). This is the
table that determines tax liability.
```sql
id, sale_transaction_id (FK), fifo_lot_id (FK), sale_date, purchase_date,
cryptocurrency, amount_sold, purchase_price_per_unit, sale_price_per_unit,
cost_basis, proceeds, gain_loss, holding_period_days, created_at
```
- `gain_loss` = proceeds - cost_basis
- `holding_period_days` = sale_date - purchase_date (determines exempt vs taxable)
- A single SELL can produce multiple rows here if it consumes multiple lots

### Views
- `v_exchange_summary` — per exchange/crypto/type aggregation
- `v_fifo_balances` — current remaining holdings
- `v_annual_gains` — yearly gains with long-term/short-term counts

## FIFO algorithm

The FIFO calculation is the core of the project. The logic:

1. For each cryptocurrency separately:
2. Get all BUY and SELL transactions ordered by date, then by id
3. For each BUY: create a fifo_lot with remaining_amount = original_amount
4. For each SELL: consume lots starting from the **oldest** with remaining > 0
5. For each lot consumed:
   - Calculate proportional cost_basis: `(amount_consumed / original_amount) * lot.cost_basis`
   - Calculate proceeds: `amount_consumed * sale_price - proportional_sale_fee`
   - Calculate gain_loss: `proceeds - cost_basis`
   - Calculate holding_period_days: `(sale_date - purchase_date).days`
   - Write a row to sale_lot_matches
   - Decrease the lot's remaining_amount

**Critical details**:
- Fees are INCLUDED in cost_basis for buys (increases cost, reduces gain)
- Sale fees are proportionally distributed across matched lots
- One SELL can split across multiple lots (partial lot consumption)
- FIFO order is strictly chronological by purchase_date
- This means a recent BUY won't be consumed before older BUYs with remaining amounts

**Key FIFO insight** (tested in test 8): If you bought 0.1 BTC in 2020 and 0.02 BTC
in 2021, then sell 0.02 BTC in 2022 — FIFO uses the 2020 lot (oldest with remaining),
NOT the 2021 lot. This makes the holding period 762 days (exempt), not 156 days (taxable).

## Portuguese tax rules

Encoded in `config.py` under the "PT" country profile.

**Legal basis**: Art.º 10.º CIRS, n.º 1, alínea k), n.º 17-19. Lei 24-D/2022 (OE 2023).

- Holdings ≥ 365 days: **exempt** from capital gains tax (but MUST be declared)
- Holdings < 365 days: taxed at **28%** flat rate
- FIFO method is **mandatory** (not optional)
- All values must be in **EUR** (ECB rate for USD-denominated exchanges)
- Tax authority requires **daily aggregation** per exchange per tax status

**Where to declare**:
- Exempt gains (≥365d): **Anexo G1, Quadro 07**
- Taxable gains (<365d) via foreign platforms: **Anexo J, Quadro 9.4A**
- Taxable gains via domestic platforms: **Anexo G, Quadro 18A**
- Filing period: April 1 — June 30 of the following year
- Data retention obligation: **7 years**

**IRS report structure** (generate_irs_report.py output):
- Sheet 1: `Anexo G1 - Quadro 7` — exempt sales, matches official AT form layout exactly
- Sheet 2: `Anexo J - Quadro 9.4A` — taxable sales, matches official AT form layout
- Sheet 3: `Resumo YYYY` — internal summary with breakdown by category and exchange
- Sheet 4: `Detail` — full daily aggregation archive

The Excel headers use Portuguese fiscal terminology (REALIZACAO, AQUISICAO, etc.)
because they mirror the official AT forms. Console output and internal labels are in English.

## Exchange country codes

Used in Anexo J for identifying foreign platforms. Defined in both `config.py`
(EXCHANGE_COUNTRIES dict) and `generate_irs_report.py` (EXCHANGE_COUNTRY dict
with numeric codes for the AT form):

| Exchange | Country | AT Code |
|----------|---------|---------|
| Binance  | Cayman Islands (MT for EU entity) | 136 |
| Bitstamp | UK | 826 |
| Bitfinex | British Virgin Islands | 092 |
| Coinbase | US | 840 |
| Kraken   | US | 840 |
| Mt.Gox   | Japan | 392 |
| TRT (TheRockTrading) | Italy | 380 |
| Wirex    | UK | 826 |
| Revolut  | Lithuania | 440 |

## ECB rate conversion

Some exchanges denominate in USD. `importers/ecb_rates.py` converts USD→EUR
using official ECB daily rates from `data/eurusd.csv`.

The CSV format is a simple date,rate file. If a rate is missing for a specific date
(weekend/holiday), the system falls back to the nearest previous business day.

**Warning system**: If the eurusd.csv file is more than 30 days old, scripts emit
a warning to update it. ECB rates can be downloaded from:
https://www.ecb.europa.eu/stats/eurofxref/eurofxref-hist.zip

## Importer pattern

Each exchange has a dedicated importer in `importers/`. The general pattern:

```python
from config import DATABASE_PATH
DB_PATH = DATABASE_PATH

# 1. Read CSV exported from exchange
# 2. Parse exchange-specific column names and formats
# 3. Filter for relevant trades (e.g., BTCEUR pairs)
# 4. Show user what will be imported (counts, date range)
# 5. Ask for confirmation before modifying DB
# 6. DELETE existing data for this exchange (idempotent re-import)
# 7. INSERT new transactions
# 8. Verify with SELECT counts
```

All importers use `from config import DATABASE_PATH` for the DB path.
All scripts are run from the project root: `python3 importers/import_EXCHANGE.py`

## Configuration system

`config.py` is the single source of truth. It provides:
- `DATABASE_PATH` — all scripts import this instead of hardcoding the path
- `EXEMPT_HOLDING_DAYS` — 365 for Portugal
- `SHORT_TERM_RATE` — 0.28 for Portugal
- `EXCHANGE_COUNTRIES` — ISO codes per exchange
- Country profiles with tax rules, form names, ECB settings

To add a new country: add a profile to COUNTRY_PROFILES, set `COUNTRY = "XX"`.

Environment variable overrides: `FIFO_COUNTRY`, `FIFO_DB`.

## Test suite

`tests/test_fifo_workflow.py` — 33 tests, runs standalone with no dependencies
except Python stdlib + sqlite3. Uses embedded test data (7 transactions: 4 BUY, 3 SELL).

**What it tests**:
1. Database creation (3 tables exist)
2. Transaction import (7 rows)
3. FIFO lot creation (4 lots, correct remaining amounts)
4. Sale-lot matching (3 matches, no unmatched sales)
5. Holding period calculation (≥365 days for long-term)
6. Gain/loss correctness (all profitable given test prices)
7. Tax classification (3 exempt, 0 taxable — FIFO uses oldest lots)
8. FIFO order verification (oldest lot consumed first, not most recent)
9. Reproducibility (deterministic: recalculate → same results)

**Run**: `python3 tests/test_fifo_workflow.py` — exit code 0 = all pass.

The test includes its own simplified FIFO calculator (not dependent on
`crypto_fifo_tracker.py`) to validate the algorithm independently.

## Complete user workflow

```bash
# 1. Setup (first time only)
./setup.sh
source venv/bin/activate

# 2. Place exchange CSVs in data/
cp ~/Downloads/binance_export.csv data/

# 3. Update ECB rates if needed
# Download from ECB, place in data/eurusd.csv

# 4. Import each exchange
python3 importers/import_binance_with_fees.py
python3 importers/import_coinbase_prime.py
# ... one per exchange

# 5. Verify imports
python3 importers/verify_exchange_import.py

# 6. Calculate FIFO
python3 calculators/calculate_fifo.py

# 7. Generate IRS report
python3 calculators/generate_irs_report.py 2024
# Output: data/reports/IRS_Crypto_FIFO_2024.xlsx

# 8. Generate annual summary (console)
python3 reports/generate_annual_summary.py 2024

# 9. Backup
cp data/crypto_fifo.db data/backups/crypto_fifo.db.backup_$(date +%Y%m%d)
```

## Documentation

All documentation exists in two languages:
- `doc/en/` — English (primary, for international audience)
- `doc/pt/` — Portuguese (for Portuguese taxpayers, includes fiscal terminology)

The root `README.md` is bilingual (EN section + PT section with link to `doc/pt/README.md`).

Key documents:
- `quickstart.md` — workflow in 5 steps
- `crypto_fifo_guide.md` — comprehensive: CSV import, FIFO, reports, verification queries
- `audit_trail_guide.md` — compliance, data integrity, 7-year retention, backup strategy
- `crypto_to_crypto_guide.md` — swaps (BTC→ETH), stablecoins, tax interpretation divergence
- `recursos_fiscais.md` (PT only) — legislation references, AT links, deadlines, penalties

## Dependencies

```
pandas          ← CSV processing in importers
openpyxl        ← Excel report generation
pytz            ← Timezone handling
requests        ← API calls (CryptoCompare for historical prices)
```

SQLite3 is built into Python. No external database server needed.

## Key design decisions and their rationale

1. **SQLite over MySQL/Postgres**: Zero setup, single file, portable, good enough
   for personal tax data volumes. Originally MySQL, migrated in early development.

2. **data/ consolidation**: DB, CSVs, reports, backups all in one directory.
   One gitignore rule. One backup target. Clean public repo.

3. **Separate fee tracking**: `fee_amount` is a separate column, never subtracted
   from `total_value`. This allows accurate reconstruction of gross values for
   the AT forms (which require gross values + fees separately).

4. **Daily aggregation**: Portuguese AT requires aggregation by day per exchange
   per tax status. The report generator groups sale_lot_matches accordingly.

5. **Idempotent importers**: Each importer DELETEs existing data for that exchange
   before inserting. Safe to re-run. Prevents duplicates.

6. **English code, bilingual docs**: All Python output, variable names, comments
   in English. Documentation in EN + PT. Console messages in English.
   Excel report headers in Portuguese (because they mirror official AT forms).

7. **CryptoCompare over CoinGecko**: CryptoCompare free tier has full historical
   data back to coin origin. CoinGecko free tier limited to 365 days.

## Crypto-to-crypto interpretation

Portuguese tax law is ambiguous on crypto-to-crypto swaps (e.g., BTC→ETH).
Two interpretations exist:

- **Conservative**: Each swap is two taxable events (SELL BTC + BUY ETH)
- **Progressive**: Swap is a non-taxable exchange, cost basis carries over

The documentation recommends recording both operations (SELL + BUY) to be safe,
and notes the divergence. This is documented in `crypto_to_crypto_guide.md`.

## Known issues and future work

- Report generators in `reports/` directory could be consolidated into `calculators/`
- `config.py` EXCHANGE_COUNTRIES uses ISO 2-letter codes; `generate_irs_report.py`
  EXCHANGE_COUNTRY uses numeric AT codes — these could be unified
- No automated rate fetching yet (user must manually download ECB CSV)
- No web UI (command-line only)
- Test suite has its own FIFO calculator; ideally it would test `crypto_fifo_tracker.py` directly

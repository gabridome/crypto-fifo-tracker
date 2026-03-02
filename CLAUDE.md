# CLAUDE.md — Project Knowledge Base

> This file is written by Claude for Claude. It contains everything needed
> to understand, maintain, and reconstruct this project from scratch.
> Last updated: 2026-03-02.

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
├── web/                         ← Flask web application (NEW)
│   ├── app.py                   ← Main application (~1700 lines)
│   └── templates/
│       ├── base.html            ← Layout with sidebar navigation
│       ├── collect.html         ← Step 1: upload/manage CSV files
│       ├── import_data.html     ← Step 2: import with exchange grouping
│       ├── status.html          ← Step 3: CSV↔DB comparison dashboard
│       ├── fifo.html            ← Step 4: FIFO calculation
│       ├── reports.html         ← Step 5: Excel report generation
│       └── manual.html          ← Step 6: manual transaction entry
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
│   ├── import_utils.py          ← Shared: compute_record_hash(), post_import_update() (NEW)
│   ├── ecb_rates.py             ← USD→EUR conversion via ECB CSV
│   ├── import_standard_csv.py   ← Generic CSV importer (updated with source tracking)
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
│   └── verify_exchange_import.py
│
├── migrate_add_source_tracking.py   ← DB migration: source, imported_at, record_hash (NEW)
├── backfill_source_hash.py          ← Backfill existing records with source/hash (NEW)
│
├── reports/                     ← Report generation scripts (CODE, not output)
│   ├── generate_annual_summary.py
│   ├── generate_reports.py
│   └── yearly_balance.py
│
├── data/                        ← ALL personal data (gitignored except samples)
│   ├── crypto_fifo.db           ← SQLite database
│   ├── eurusd.csv               ← ECB historical EUR/USD rates
│   ├── template_manual_transactions.csv  ← Template for manual/OTC imports (NEW)
│   ├── sample_transactions.csv  ← Fake data for testing (TRACKED in git)
│   ├── *.csv                    ← User's exchange export files
│   ├── reports/                 ← Generated .xlsx output
│   ├── backups/                 ← Database snapshots
│   └── supporting_documents/    ← Exchange statements, invoices
│
├── doc/
│   ├── schema.sql               ← Database DDL
│   ├── en/                      ← English documentation (7 guides)
│   └── pt/                      ← Portuguese documentation (8 guides incl. recursos_fiscais)
│
└── tests/
    ├── __init__.py
    └── test_fifo_workflow.py    ← 33 automated tests
```

## Web interface (NEW — March 2026)

A wizard-style local Flask application that guides the user through the entire workflow.
Replaces the old `demoweb.py` prototype.

### Running

```bash
python3 web/app.py
# Open http://127.0.0.1:5002
```

Port is 5002 (not 5000 — macOS ControlCenter occupies 5000).

### Architecture

`web/app.py` (~1700 lines) is a self-contained Flask app. It uses `subprocess.run()`
to call existing importer/calculator scripts, so no importers need to be rewritten
as Flask views. The web app is a thin orchestration layer.

### Visual design

Dark industrial/brutalist theme. CSS variables for consistent palette.
Sidebar navigation with step completion indicators (○ empty, ◐ partial, ● complete).
Status bar in sidebar shows DB connection and ECB rate file status.

### Pages and their roles

**① Collect** (`/collect`): Upload CSV files or see what's in data/.
Per-exchange download instructions (Binance, Coinbase, Bitstamp, etc.).
Template download for manual/OTC transactions.
ECB eurusd.csv freshness warning if stale or missing.

**② Import** (`/import`): Files grouped by exchange. Multi-file exchanges
(Wirex 2023+2024+2025, Coinbase monthly files) shown as groups with
"Merge & import" button. Column mapping documentation per exchange.
Standard CSV files imported file-by-file with DELETE-by-source.
Exchange-specific importers use merge+swap for multi-file.

**③ Status** (`/status`): Symmetric CSV↔DB comparison. Same metrics
(BUY count, SELL count, dates, values, fees) extracted from both
the CSV file and the database, displayed side by side.
Color-coded delta column (✓ match, yellow close, red mismatch).
Record-level matching with row-by-row diagnosis of unmatched records.
ECB rate file panel at top: coverage dates, gaps vs USD-exchange CSVs.

**④ FIFO** (`/fifo`): Per-year gain/loss breakdown, current holdings,
auto-backup before calculation. Calls `calculators/calculate_fifo.py`.

**⑤ Reports** (`/reports`): Per-exchange statistics, IRS Excel generation
by year, download existing reports. Calls `calculators/generate_irs_report.py`.

**⑥ Manual Entry** (`/manual`): Form for OTC, gifts, inheritance, airdrops.
Records source as `web_manual_entry` with computed hash.

### Key data structures in app.py

**EXCHANGE_PATTERNS**: List of (regex, exchange_name, importer_script) tuples
for detecting exchange from CSV filename. Covers all exchanges + manual sources.

**EXCHANGE_FIELD_MAP**: Per-exchange documentation of CSV column → DB field mapping,
including column names, type value mapping, and notes. Displayed on Import page.

**CSV_PARSE_RULES**: Per-exchange parsing rules (date_col, type_col, amount_col, etc.)
for deep-parsing CSV files to extract aggregate statistics for Status comparison.

**USD_EXCHANGES**: Set of exchanges whose data is in USD ('Bitfinex', 'Coinbase Prime',
'Kraken', 'Mt.Gox') — used by `check_eurusd()` to validate coverage.

### Import flow

Standard CSV (OTC/manual): `import_standard_csv.py <filepath> <exchange_name>`
→ DELETE by source (file-level) → INSERT with source/imported_at/record_hash.
Each file imported independently, no merge needed.

Exchange-specific (single file): `python3 importers/import_EXCHANGE.py`
→ importer does DELETE by exchange → INSERT
→ `post_import_source_update()` backfills source/hash on NULL records.

Exchange-specific (multi-file): files merged into temp CSV → swap with original
→ run importer → restore original → `post_import_source_update()`.

## Source tracking system (NEW — March 2026)

Three columns added to `transactions` table:

| Column | Type | Purpose |
|--------|------|---------|
| `source` | TEXT | CSV filename that originated this record |
| `imported_at` | TEXT | ISO timestamp of when record was imported |
| `record_hash` | TEXT | SHA256 hash for dedup, audit, rollback |

### record_hash computation

```python
SHA256(f"{source}|{date}|{type}|{exchange}|{crypto}|{amount:.8f}|{value:.2f}|{fee:.2f}")
```

Same inputs always produce same hash. Used for:
- **Deduplication**: on APPEND import, skip records whose hash already exists in DB
- **Audit**: verify DB record matches CSV row by recomputing hash
- **Incremental import** (future): import only new rows from updated CSV

### Rollback by source

```sql
-- Remove all records from a specific file
DELETE FROM transactions WHERE source = 'wirex_2024.csv';

-- Remove everything imported today
DELETE FROM transactions WHERE imported_at >= '2026-03-02';
```

### Migration scripts

- `migrate_add_source_tracking.py`: ALTER TABLE + indexes. Safe to run multiple times.
  Creates automatic backup before migration.
- `backfill_source_hash.py`: One-time backfill for existing records. Infers source
  from exchange_name → CSV file mapping. Computes hash for all records.
  Multi-file exchanges get `[file1+file2+file3]` as source.

### Shared utilities

`importers/import_utils.py` provides:
- `compute_record_hash()` — deterministic SHA256
- `delete_by_source()` — surgical DELETE by source file
- `post_import_update()` — backfill source/hash after exchange-specific importers

## Database schema

SQLite3. Three tables, all values in EUR.

### transactions
Every BUY, SELL, DEPOSIT, WITHDRAWAL from every exchange.
```sql
id, transaction_date (ISO 8601), transaction_type, exchange_name,
cryptocurrency, amount, price_per_unit, total_value (gross, BEFORE fees),
fee_amount (separate), fee_currency, currency, transaction_id, notes, created_at,
source, imported_at, record_hash
```
- `total_value` = amount × price_per_unit (gross, no fee deducted)
- `fee_amount` is always separate, never subtracted from total_value
- `transaction_type` CHECK constraint: BUY, SELL, DEPOSIT, WITHDRAWAL
- `source` = CSV filename or 'web_manual_entry' or 'manual_entry'
- `record_hash` = SHA256 for dedup and audit

### fifo_lots
Each BUY creates one lot. Lots are consumed by SELLs in chronological order.
```sql
id, purchase_transaction_id (FK), cryptocurrency, purchase_date,
original_amount, remaining_amount, purchase_price_per_unit,
cost_basis (total_value + fee), purchase_fee_total, exchange_name, created_at
```

### sale_lot_matches
Each SELL is matched to one or more FIFO lots (oldest first).
```sql
id, sale_transaction_id (FK), fifo_lot_id (FK), sale_date, purchase_date,
cryptocurrency, amount_sold, purchase_price_per_unit, sale_price_per_unit,
cost_basis, proceeds, gain_loss, holding_period_days, created_at
```

### Indexes
```sql
idx_transactions_source      ON transactions(source)
idx_transactions_hash        ON transactions(record_hash)
idx_transactions_imported_at ON transactions(imported_at)
```

## FIFO algorithm

1. For each cryptocurrency separately:
2. Get all BUY and SELL ordered by date, then by id
3. For each BUY: create fifo_lot with remaining_amount = original_amount
4. For each SELL: consume lots starting from oldest with remaining > 0
5. For each lot consumed: proportional cost_basis, proceeds, gain_loss, holding_period_days

**Critical**: Fees INCLUDED in cost_basis for buys. Sale fees proportionally
distributed. FIFO order strictly chronological.

## Portuguese tax rules

Config: `config.py`. Legal basis: Art.º 10.º CIRS, Lei 24-D/2022.

- Holdings ≥ 365 days: **exempt** (must still declare in Anexo G1, Quadro 07)
- Holdings < 365 days: taxed at **28%** flat (Anexo J, Quadro 9.4A for foreign)
- FIFO mandatory, all values EUR, daily aggregation per exchange per tax status
- Filing: April 1 — June 30. Data retention: 7 years.

## ECB rate conversion

USD→EUR via `data/eurusd.csv`. USD exchanges: Bitfinex, Coinbase Prime, Kraken, Mt.Gox.
Web app checks coverage vs CSV date ranges, warns if stale (>30d) or gaps exist.

## Exchange CSV files and their importers

| Exchange | Importer | Format | Multi-file |
|----------|----------|--------|------------|
| Binance | import_binance_with_fees.py | exchange-specific | No |
| Binance Card | import_binance_card.py | exchange-specific | No |
| Binance OTC | import_standard_csv.py | standard | No |
| Coinbase | import_coinbase_standalone.py | exchange-specific | Yes (monthly) |
| Coinbase Prime | import_coinbase_prime.py | exchange-specific | No |
| Bitstamp | import_bitstamp_with_fees.py | exchange-specific | No |
| Bitfinex | import_bitfinex_ecb.py (USD→EUR) | exchange-specific | No |
| Kraken | import_kraken_with_fees.py | exchange-specific | No |
| Mt.Gox | import_mtgox_with_fees.py | exchange-specific | No |
| TRT | import_trt_with_fees.py | exchange-specific | No |
| Wirex | import_wirex.py | exchange-specific | Yes (yearly) |
| Revolut | import_revolut.py | exchange-specific | No |
| changely | import_standard_csv.py | standard | No |
| Coinpal | import_standard_csv.py | standard | No |
| GDTRE | import_standard_csv.py | standard | No |
| Inheritance | import_standard_csv.py | standard | No |
| OTC | import_standard_csv.py | standard | No |

Standard CSV template: `data/template_manual_transactions.csv`.
Required: transaction_date, transaction_type, exchange_name, cryptocurrency, amount, total_value.

## Configuration

`config.py`: DATABASE_PATH, EXEMPT_HOLDING_DAYS (365), SHORT_TERM_RATE (0.28),
EXCHANGE_COUNTRIES. Env overrides: FIFO_COUNTRY, FIFO_DB.

## Test suite

`tests/test_fifo_workflow.py` — 33 tests. Run: `python3 tests/test_fifo_workflow.py`

## Dependencies

```
flask, pandas, openpyxl, pytz, requests
```

## Known issues and TODO

- [ ] Exchange-specific importers still DELETE by exchange_name, not by source.
      `post_import_source_update()` is the bridge. Gradually migrate each importer
      to use `import_utils.py` directly for native source tracking.
- [ ] `generate_irs_report.py` line 612 path bug: creates `data/data/reports`.
      Fix: `os.path.join(project_dir, 'reports')` not `os.path.join(project_dir, 'data', 'reports')`.
- [ ] Report generators in `reports/` could be consolidated into `calculators/`
- [ ] Exchange country code formats not unified between config.py and generate_irs_report.py
- [ ] No automated ECB rate fetching
- [ ] Web status page record matching could use record_hash once all importers set it natively
- [ ] Test suite uses embedded FIFO calculator — ideally test crypto_fifo_tracker.py directly

## Development workflow

```bash
git checkout -b feature/NAME

# Web app (hot reload)
python3 web/app.py

# Test routes
python3 -c "
from web.app import app; app.config['TESTING']=True; c=app.test_client()
for r in ['/','/collect','/import','/status','/fifo','/reports','/manual']:
    print(f'{r:15s} → {c.get(r,follow_redirects=True).status_code}')
"

# FIFO tests
python3 tests/test_fifo_workflow.py

# Merge
git checkout main && git merge --no-ff feature/NAME
```

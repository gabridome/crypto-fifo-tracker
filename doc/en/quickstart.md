# Quickstart — Crypto FIFO Tracker

Quick guide to import data, calculate FIFO and generate the IRS report.

---

## Prerequisites

- macOS with Python 3.11+
- Active virtual environment
- CSV files downloaded from exchanges in the `data/` folder
- Up-to-date `data/eurusd.csv` with ECB rates

```bash
cd ~/crypto_project
source venv/bin/activate
pip install pandas pytz openpyxl
```

---

## Complete workflow

### 1. Import transactions

Each exchange has a dedicated importer in `importers/`. Each importer:
- Asks whether to delete existing data for that exchange (DELETE option)
- Reads the CSV from the `data/` folder
- Inserts transactions into the `transactions` table
- For USD exchanges (Coinbase Prime, Bitstamp, Bitfinex): converts to EUR via `ecb_rates.py`

```bash
# Import one exchange at a time
python3 importers/import_binance_with_fees.py
python3 importers/import_coinbase_prime.py
python3 importers/import_bitstamp_with_fees.py
python3 importers/import_bitfinex_ecb.py
python3 importers/import_kraken_with_fees.py
python3 importers/import_mtgox_with_fees.py
python3 importers/import_trt_with_fees.py
python3 importers/import_wirex.py
python3 importers/import_revolut.py
python3 importers/import_coinbase_standalone.py
python3 importers/import_binance_card.py

# For standard-format generic transactions:
python3 importers/import_standard_csv.py data/file.csv "Exchange Name"
```

**Reimport strategy**: importers use DELETE + reimport. All data for the exchange is deleted and reimported from the full CSV. No risk of duplicates.

**Verify** after each import:
```bash
python3 importers/verify_exchange_import.py "Binance"
python3 importers/verify_exchange_import.py "Coinbase Prime"
```

### 2. Calculate FIFO

FIFO calculation always starts **from the very first transaction** and processes the entire history. It is not possible to calculate only one year.

```bash
python3 calculators/calculate_fifo.py
```

This populates two tables:
- `fifo_lots` — purchase lots with remaining quantity
- `sale_lot_matches` — each sale matched to the oldest purchase lot

Estimated time: ~2 minutes for tens of thousands of transactions.

### 3. Generate reports

**IRS report** (Excel with Anexo G1, Anexo J, Summary sheets):
```bash
python3 calculators/generate_irs_report.py YYYY
```
Generates: `reports/IRS_Crypto_FIFO_YYYY.xlsx`

**Annual summary** (console output with purchases/sales/FIFO/holdings):
```bash
python3 reports/generate_annual_summary.py YYYY
```

**Legacy reports** (CSV per crypto):
```bash
python3 reports/generate_reports.py
```

---

## Adding a new year of data

When new transactions arrive:

1. **Update the CSVs** — add new rows to existing CSV files in `data/` (one file per exchange with all history, not separate files per year)
2. **Update ECB rates** — download updated `data/eurusd.csv`
3. **Reimport modified exchanges** — using DELETE + reimport
4. **Verify** — `verify_exchange_import.py`
5. **Recalculate FIFO** — `calculate_fifo.py` (recalculates everything)
6. **Generate reports** — for each needed year

```bash
# Example: new Coinbase Prime data
python3 importers/import_coinbase_prime.py     # option 1 = DELETE + reimport
python3 importers/verify_exchange_import.py "Coinbase Prime"
python3 calculators/calculate_fifo.py
python3 calculators/generate_irs_report.py YYYY
```

---

## Backups

**Before critical operations**, back up the database:

```bash
cp crypto_fifo.db backups/crypto_fifo.db.backup_$(date +%Y%m%d)
```

The `crypto_fifo.db` database is the **permanent source of truth**. It should never be recreated from scratch — only updated and recalculated.

---

## Useful queries

```bash
sqlite3 crypto_fifo.db
```

```sql
-- Transactions per exchange
SELECT exchange_name, COUNT(*) as n, 
       SUM(CASE WHEN transaction_type='BUY' THEN 1 ELSE 0 END) as buys,
       SUM(CASE WHEN transaction_type='SELL' THEN 1 ELSE 0 END) as sells
FROM transactions GROUP BY exchange_name;

-- Annual gain/loss summary
SELECT strftime('%Y', sale_date) as year, cryptocurrency,
       COUNT(*) as operations, SUM(gain_loss) as gain_loss,
       SUM(CASE WHEN holding_period_days >= 365 THEN gain_loss ELSE 0 END) as exempt,
       SUM(CASE WHEN holding_period_days < 365 THEN gain_loss ELSE 0 END) as taxable
FROM sale_lot_matches GROUP BY year, cryptocurrency;

-- Open FIFO lots
SELECT cryptocurrency, COUNT(*) as lots, SUM(remaining_amount) as quantity
FROM fifo_lots WHERE remaining_amount > 0.00000001
GROUP BY cryptocurrency;
```

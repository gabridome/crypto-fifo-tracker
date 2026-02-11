# Complete Guide — Import, FIFO and IRS Report Generation

---

## Overview

The system imports transactions from multiple exchanges into a SQLite database (`crypto_fifo.db`), calculates global FIFO and generates reports for Portuguese IRS tax filing. All values are in EUR.

**Supported exchanges**: Mt.Gox, Bitstamp, TRT (TheRockTrading), Bitfinex, Kraken, Binance, Binance Card, Coinbase, Coinbase Prime, Wirex, Revolut, GDTRE, Coinpal.

**Goal**: prove the origin of sales via FIFO, separating exempt capital gains (holding ≥365 days, Anexo G1) from taxable ones (<365 days, Anexo J).

---

## Strategy: CSV Import (not API)

Why CSV and not API:
- No rate limits or pagination issues
- Complete historical data in one file
- Works with defunct exchanges (Mt.Gox)
- Faster for high volumes
- Full control over data validation

---

## CSV file structure

Each exchange has its own file in `data/`:

```
data/
├── eurusd.csv                  ← ECB rates (update before each import)
├── binance_trade_history_all.csv
├── coinbaseprime_orders.csv
├── coinbase_history.csv
├── bitstamp_history.csv
├── bitfinex_trades.csv
├── kraken_ledgers.csv
├── mtgox.csv
├── trt.csv
├── wirex_YYYY.csv              ← One file per period
├── revolut_crypto.csv
├── binance_card.csv
├── coinpal.csv
├── gdtre.csv
└── inheritance_YYYY.csv        ← Manual/OTC transactions
```

**Rule**: one CSV file per exchange with **the entire history** (not separate files per year). When new data arrives, append it to the existing file.

---

## Phase 1 — Preparation

### Check the CSV before importing

```python
import pandas as pd
df = pd.read_csv('data/bitstamp_history.csv', nrows=5)
print(df.columns.tolist())
print(df.head())
```

Check:
1. Date format
2. Column names (each exchange is different)
3. Currency (EUR or USD?)
4. Encoding (UTF-8, not UTF-16 or ISO-8859)

### Update ECB rates

The `data/eurusd.csv` file contains historical EUR/USD exchange rates from the ECB. It must be updated before importing exchanges that operate in USD (Coinbase Prime, Bitstamp, Bitfinex).

If the file is not up to date, `ecb_rates.py` will silently use the last available rate — but it will print a warning:

```
⚠️  ECB rate for 2026-01-15: nearest rate is 2025-12-31 (-15d) — UPDATE eurusd.csv!
```

---

## Phase 2 — Exchange import

Each importer is in `importers/` and follows the same pattern:

1. Asks whether to delete existing data for that exchange (**DELETE + reimport**)
2. Reads the CSV with the exchange-specific column mapping
3. Converts to EUR if necessary (via `ecb_rates.py`)
4. Inserts into the `transactions` table

### Recommended order

Import historical exchanges (oldest purchases) first, then recent ones:

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
python3 importers/import_binance_card.py
python3 importers/import_wirex.py
python3 importers/import_revolut.py

# Manual/OTC transactions
python3 importers/import_standard_csv.py data/inheritance_YYYY.csv "Inheritance"
python3 importers/import_standard_csv.py data/otc_YYYY.csv "OTC"
```

### Verify after each import

```bash
python3 importers/verify_exchange_import.py "Binance"
python3 importers/verify_exchange_import.py "Coinbase Prime"
```

The script compares BUY/SELL totals, quantities and fees.

### Fee handling

Critical rule in the database:
- `total_value` = gross amount (BEFORE fees)
- `fee_amount` = fee stored separately
- For a BUY: cost_basis = total_value + fee_amount
- For a SELL: proceeds = total_value − fee_amount

Each importer handles this logic specifically for its exchange format.

---

## Phase 3 — FIFO calculation

```bash
python3 calculators/calculate_fifo.py
```

**What it does:**
1. Takes **all** transactions from the very first one
2. For each BUY: creates a FIFO lot with available quantity
3. For each SELL: matches to the oldest lot with remaining quantity
4. Calculates holding period, cost basis, gain/loss
5. Saves everything in `fifo_lots` and `sale_lot_matches`

**Time**: ~2 minutes for tens of thousands of transactions.

**Important**: FIFO always recalculates **everything** (deletes fifo_lots and sale_lot_matches for each crypto and recalculates). It is not possible to calculate only one year because lots derive from the complete chain of purchases.

---

## Phase 4 — Report generation

### IRS report (for tax filing)

```bash
python3 calculators/generate_irs_report.py YYYY
```

Generates: `reports/IRS_Crypto_FIFO_YYYY.xlsx` with 4 sheets:

| Sheet | Content |
|-------|---------|
| Resumo | Summary: total exempt, total taxable, estimated tax |
| Anexo G1 Quadro 07 | Exempt sales (holding ≥365 days) — daily aggregation per exchange |
| Anexo J Quadro 9.4A | Taxable sales (<365 days) on foreign platforms |
| Dettaglio | Daily aggregations with underlying operation counts |

Daily aggregation groups by date + exchange + tax status (exempt/taxable), as required by the AT. Exempt and taxable sales **never mix** even if they occur on the same day on the same exchange.

### Annual summary (console)

```bash
python3 reports/generate_annual_summary.py YYYY
```

Shows: purchases, sales, FIFO result (exempt/taxable), detail per exchange, remaining holdings.

---

## Verification queries

### Sale origin by purchase year

```sql
SELECT 
    strftime('%Y', purchase_date) as purchase_year,
    COUNT(DISTINCT sale_transaction_id) as num_sales,
    SUM(amount_sold) as btc_sold,
    ROUND(AVG(holding_period_days) / 365.25, 1) as avg_holding_years,
    SUM(CASE WHEN holding_period_days >= 365 THEN amount_sold ELSE 0 END) as btc_exempt
FROM sale_lot_matches slm
JOIN transactions t ON slm.sale_transaction_id = t.id
WHERE strftime('%Y', slm.sale_date) = ?
AND t.cryptocurrency = 'BTC'
GROUP BY purchase_year
ORDER BY purchase_year;
```

### Detailed proof chain

```sql
SELECT 
    slm.sale_date,
    slm.purchase_date,
    slm.amount_sold,
    slm.holding_period_days,
    t_purchase.exchange_name as purchase_exchange,
    t_sale.exchange_name as sale_exchange,
    slm.purchase_price_per_unit as purchase_price,
    slm.sale_price_per_unit as sale_price,
    slm.gain_loss
FROM sale_lot_matches slm
JOIN transactions t_sale ON slm.sale_transaction_id = t_sale.id
JOIN fifo_lots fl ON slm.fifo_lot_id = fl.id
JOIN transactions t_purchase ON fl.purchase_transaction_id = t_purchase.id
WHERE strftime('%Y', slm.sale_date) = ?
AND slm.holding_period_days >= 365
ORDER BY slm.sale_date, slm.purchase_date
LIMIT 100;
```

### Unmatched sales (should be none)

```sql
SELECT 
    t.transaction_date,
    t.exchange_name,
    t.amount as sold,
    COALESCE(SUM(slm.amount_sold), 0) as matched,
    t.amount - COALESCE(SUM(slm.amount_sold), 0) as unmatched
FROM transactions t
LEFT JOIN sale_lot_matches slm ON t.id = slm.sale_transaction_id
WHERE t.transaction_type = 'SELL'
AND t.cryptocurrency = 'BTC'
GROUP BY t.id
HAVING unmatched > 0.00000001;
```

If unmatched sales exist: purchase transactions are missing, or BTC transfers were not tracked.

---

## Common issues and solutions

### CSV with non-UTF-8 encoding

```bash
# Convert to UTF-8
iconv -f ISO-8859-1 -t UTF-8 input.csv > output.csv
# Or from UTF-16 (typical for Wirex)
iconv -f UTF-16LE -t UTF-8 wirex.csv > wirex_utf8.csv
```

### Missing prices in historical data

If a historical exchange had no prices in the CSV, use CryptoCompare API (free tier, full history back to coin origin):

```python
import requests, time

CRYPTOCOMPARE_API_KEY = "YOUR_KEY"  # free from cryptocompare.com

def get_historical_price(coin, currency, timestamp):
    url = "https://min-api.cryptocompare.com/data/v2/histoday"
    params = {"fsym": coin, "tsym": currency, "limit": 1, "toTs": timestamp}
    headers = {"authorization": f"Apikey {CRYPTOCOMPARE_API_KEY}"}
    response = requests.get(url, headers=headers, params=params)
    return response.json()["Data"]["Data"][-1]["close"]

# Usage: pass a UNIX timestamp for the date you need
from datetime import datetime
ts = int(datetime(2014, 5, 21).timestamp())
price_eur = get_historical_price("BTC", "EUR", ts)
time.sleep(0.5)  # respect rate limits
```

### Duplicates

Importers use DELETE + reimport: they delete all data for the exchange and reimport the full CSV. No risk of duplicates.

---

## Best practices

1. **Keep original CSVs** — never delete source files
2. **Backup before FIFO** — `cp crypto_fifo.db backups/crypto_fifo.db.backup_$(date +%Y%m%d)`
3. **Verify every import** — use `verify_exchange_import.py`
4. **Update `eurusd.csv`** — before importing USD exchanges
5. **Never recreate the database** — it's the permanent source of truth
6. **Keep database + reports for 7+ years** — tax obligation

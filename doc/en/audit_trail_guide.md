# Compliance and Audit Trail

Documentation for fiscal traceability and defence in case of tax audit.

---

## Principles

The system provides verifiable evidence of:
- Origin of every transaction (exchange, CSV file, import date)
- FIFO methodology with purchase→sale proof chain
- Exact holding period for each match
- USD→EUR conversion via official ECB rates
- Data integrity via hashes and backups

---

## What to keep for each tax year

### 1. Database and reports

```
backups/crypto_fifo.db.backup_YYYYMMDD   ← Database snapshot after FIFO
reports/IRS_Crypto_FIFO_YYYY.xlsx        ← IRS report with Anexo G1 and Anexo J
```

### 2. Source CSV files

All original CSV files downloaded from exchanges, kept in `data/`. Never modify original files — if cleanup is needed, create a copy.

### 3. Exchange rates

The `data/eurusd.csv` file with historical ECB rates used for USD→EUR conversions. This is the ECB's official reference.

### 4. Supporting documents

```
supporting_documents/
├── exchange_statements/    ← Exchange account statements
├── price_evidence/         ← Screenshots/JSON for reconstructed missing prices
└── other/                  ← Invoices, contracts for OTC transactions
```

---

## Data integrity verification

### Import consistency check

After each import, verify:

```bash
python3 importers/verify_exchange_import.py "Exchange Name"
```

The script checks:
- Number of BUY and SELL transactions
- Total BTC quantity
- Total fees
- Price consistency

### FIFO consistency check

```sql
-- All sales must be matched
SELECT COUNT(*) as unmatched_sales
FROM transactions t
LEFT JOIN sale_lot_matches slm ON t.id = slm.sale_transaction_id
WHERE t.transaction_type = 'SELL'
AND t.cryptocurrency = 'BTC'
AND slm.id IS NULL;
-- Expected result: 0

-- No negative amounts
SELECT COUNT(*) FROM transactions WHERE amount < 0;
-- Expected result: 0

-- All transactions have valid dates
SELECT COUNT(*) FROM transactions WHERE transaction_date IS NULL OR transaction_date = '';
-- Expected result: 0
```

### Reproducibility verification

FIFO calculation is deterministic: with the same data in `transactions`, `calculate_fifo.py` always produces the same results in `sale_lot_matches`. To verify:

```bash
# Pre-calculation backup
cp crypto_fifo.db backups/crypto_fifo.db.pre_fifo

# Calculate FIFO
python3 calculators/calculate_fifo.py

# Snapshot results
sqlite3 crypto_fifo.db "SELECT SUM(gain_loss), COUNT(*) FROM sale_lot_matches" > checksum_a.txt

# Recalculate FIFO
python3 calculators/calculate_fifo.py

# Compare
sqlite3 crypto_fifo.db "SELECT SUM(gain_loss), COUNT(*) FROM sale_lot_matches" > checksum_b.txt
diff checksum_a.txt checksum_b.txt
# Expected result: no difference
```

---

## Change traceability

### Backup before every critical operation

```bash
# Before importing new data
cp crypto_fifo.db backups/crypto_fifo.db.backup_before_import_$(date +%Y%m%d)

# Before recalculating FIFO
cp crypto_fifo.db backups/crypto_fifo.db.backup_before_fifo_$(date +%Y%m%d)
```

### Database hash

```bash
# Calculate SHA-256 of the database after generating the final report
shasum -a 256 crypto_fifo.db > backups/crypto_fifo.db.sha256
```

### Source CSV file hashes

```bash
# Create hashes for all CSVs
shasum -a 256 data/*.csv > backups/csv_hashes.sha256
```

---

## Audit queries

### Transactions per exchange and type

```sql
SELECT 
    exchange_name,
    transaction_type,
    cryptocurrency,
    COUNT(*) as n,
    SUM(amount) as quantity,
    SUM(total_value) as eur_value,
    SUM(fee_amount) as fees
FROM transactions
GROUP BY exchange_name, transaction_type, cryptocurrency
ORDER BY exchange_name, transaction_type;
```

### Annual summary by tax classification

```sql
SELECT 
    strftime('%Y', sale_date) as year,
    cryptocurrency,
    COUNT(*) as operations,
    SUM(amount_sold) as quantity_sold,
    SUM(gain_loss) as total_gain_loss,
    SUM(CASE WHEN holding_period_days >= 365 THEN gain_loss ELSE 0 END) as exempt,
    SUM(CASE WHEN holding_period_days < 365 THEN gain_loss ELSE 0 END) as taxable
FROM sale_lot_matches
GROUP BY year, cryptocurrency
ORDER BY year, cryptocurrency;
```

### Sale origin (which exchange and year was the crypto originally bought)

```sql
SELECT 
    t_purchase.exchange_name as purchase_exchange,
    strftime('%Y', slm.purchase_date) as purchase_year,
    COUNT(*) as matches,
    SUM(slm.amount_sold) as quantity,
    SUM(slm.gain_loss) as gain_loss
FROM sale_lot_matches slm
JOIN fifo_lots fl ON slm.fifo_lot_id = fl.id
JOIN transactions t_purchase ON fl.purchase_transaction_id = t_purchase.id
WHERE strftime('%Y', slm.sale_date) = ?
GROUP BY purchase_exchange, purchase_year
ORDER BY purchase_year, purchase_exchange;
```

### Verify ECB rates used

For USD transactions, ECB rates are baked in at import time. They can be verified by comparing `price_per_unit` (in EUR) against the original USD price in the CSV and the ECB rate for that day.

---

## Audit Trail Web Page

The `/audit` page in the web interface provides visual, per-row traceability
for the entire IRS report. Access it at `http://127.0.0.1:5002/audit`.

### How to use

1. Select the tax year
2. Each row in the table corresponds to one line in the IRS report
   (aggregated by day + exchange + exempt/taxable, same as Anexo G1/J)
3. Click any row to expand and see every `sale_lot_match` in that group
4. Each match shows two cards side by side:
   - **SELL** — sale date, price, proceeds, fee, source CSV file, record hash, import timestamp
   - **BUY (FIFO Lot)** — purchase date, price, cost basis, fee, source CSV file, record hash, import timestamp
5. The calculation line shows: `Proceeds - Cost basis = Gain/Loss`

### Printing as PDF

Click **Print / PDF** (or `Cmd+P`). The print layout:
- Hides the sidebar and navigation
- Expands all detail rows automatically (no clicking needed)
- Uses black-on-white for readability
- Includes a header with year, totals, and generation timestamp

This PDF serves as supporting documentation if the Autoridade Tributaria
requests proof for any specific line in your declaration.

### What the hash proves

The `record_hash` (SHA-256) shown for each transaction is computed from:
`source|date|type|exchange|crypto|amount|value|fee`

If you still have the original CSV file, you can verify that the hash
matches by recomputing it. This proves the database record was not
altered after import.

---

## What to present to tax authorities

### Basic documentation

1. **IRS report** (`IRS_Crypto_FIFO_YYYY.xlsx`) — Summary + Anexo G1 + Anexo J sheets
2. **Annual summary** — output from `generate_annual_summary.py`
3. **Original CSV files** from exchanges
4. **ECB rates** (`data/eurusd.csv`)

### In case of audit

Additional documentation:
1. **Audit Trail PDF** — print the `/audit` page for the relevant year (shows full FIFO proof chain per IRS row)
2. **FIFO proof chain** — query showing each sale matched to its original purchase
2. **Account statements** from exchanges
3. **Database hash** at the time of filing
4. **Database schema** (`doc/schema.sql`)
5. **Column mapping** (`doc/IMPORT_MAPPING.md`)

### Methodology explanation

The FIFO (First In, First Out) method is mandatory under Portuguese tax law. For each cryptocurrency sale, the system automatically identifies the oldest available purchase and calculates:

- **Holding period** = sale date − purchase date (in days)
- **Cost basis** = original purchase price + fees
- **Proceeds** = sale price − fees
- **Gain/loss** = proceeds − cost basis
- **Classification** = exempt if holding ≥365 days, taxable otherwise

All values are in EUR. For exchanges operating in USD, conversion uses the ECB (European Central Bank) rate for the transaction date.

---

## Data retention

Retention obligation: **minimum 7 years** from the filing date.

Keep:
- [ ] Database `crypto_fifo.db` (with dated backup)
- [ ] Source CSV files from exchanges
- [ ] ECB rates (`data/eurusd.csv`)
- [ ] Generated IRS reports (`.xlsx`)
- [ ] SHA-256 hashes of database and CSVs
- [ ] Supporting documents (statements, invoices)

Recommendation: archive everything on at least 2 different storage media.

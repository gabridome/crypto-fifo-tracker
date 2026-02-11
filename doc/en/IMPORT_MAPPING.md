# CRYPTO IMPORT MAPPING - COMPLETE DOCUMENTATION

**Generated:** 2024-12-10
**Database:** crypto_fifo.db
**Target Schema:** transactions table

---

## DATABASE SCHEMA

```sql
CREATE TABLE transactions (
    id INTEGER PRIMARY KEY,
    transaction_date TEXT NOT NULL,      -- ISO format with timezone
    transaction_type TEXT NOT NULL,      -- BUY, SELL, DEPOSIT, WITHDRAWAL
    exchange_name TEXT NOT NULL,         -- Exchange identifier
    cryptocurrency TEXT NOT NULL,        -- BTC, USDC, etc
    amount REAL NOT NULL,                -- Quantity of crypto
    price_per_unit REAL,                 -- Price in EUR per unit
    total_value REAL,                    -- Total EUR value (BEFORE fees)
    fee_amount REAL,                     -- Fee in EUR (separate)
    fee_currency TEXT,                   -- Usually EUR
    currency TEXT,                       -- Always EUR for Portugal
    transaction_id TEXT,                 -- Unique ID from exchange
    notes TEXT                           -- Optional description
);
```

**CRITICAL FEE HANDLING:**
- `total_value` = gross amount (BEFORE fees)
- `fee_amount` = fee separately stored
- BUY: cost_basis = total_value + fee_amount
- SELL: proceeds = total_value - fee_amount

---

## EXCHANGE MAPPINGS

### 1. COINBASE ✅ (COMPLETED)

**File:** `data/coinbase_history.csv`
**Importer:** `importers/import_coinbase.py`
**Status:** ✅ Imported (52,036 transactions, €55,817.92 fees)

**Source Format:**
```csv
Timestamp,Transaction Type,Asset,Quantity Transacted,EUR Spot Price at Transaction,EUR Subtotal,EUR Total (inclusive of fees and/or spread),EUR Fees and/or Spread,Notes
```

**Mapping:**
```
Timestamp                → transaction_date (parse + add timezone)
Transaction Type         → transaction_type (map: "Buy"→BUY, "Sell"→SELL, "Receive"→DEPOSIT, "Send"→WITHDRAWAL)
Asset                    → cryptocurrency
Quantity Transacted      → amount
EUR Spot Price          → price_per_unit
EUR Subtotal            → total_value (BEFORE fees)
EUR Fees and/or Spread  → fee_amount
"EUR"                   → fee_currency, currency
"Coinbase"              → exchange_name
```

**Special Cases:**
- Deposits/Withdrawals: price = 0, total_value = 0
- Some have no fee (legitimate, not error)

---

### 2. BINANCE (SPOT TRADING) ✅ (COMPLETED)

**File:** `data/binance_trade_history_2024.csv`
**Importer:** `importers/import_binance.py`
**Status:** ✅ Imported (4,905 transactions, €6,015.22 fees)

**Source Format:**
```csv
Date(UTC),Pair,Side,Price,Executed,Amount,Fee
```

**Example:**
```
2024-04-30 00:46:44,BTCEUR,SELL,60000.00,0.01526BTC,915.60EUR,0.9156EUR
```

**Mapping:**
```
Date(UTC)    → transaction_date (parse as UTC)
Side         → transaction_type (SELL only in 2024)
Executed     → amount (parse "0.01526BTC" → 0.01526)
Price        → price_per_unit (as float)
Amount       → total_value (parse "915.60EUR" → 915.60, BEFORE fee)
Fee          → fee_amount (parse "0.9156EUR" → 0.9156)
"EUR"        → fee_currency, currency
"Binance"    → exchange_name
```

**Special Cases:**
- Filter: ONLY Pair == "BTCEUR" (ignore BTCUSDT, BTCBUSD)
- Format: Amounts have currency suffix ("0.01526BTC", "915.60EUR")

---

### 3. MT.GOX ✅ (COMPLETED)

**File:** `data/mtgox.csv`
**Importer:** `importers/import_mtgox.py`
**Status:** ✅ Imported (740 transactions, €259.77 fees)

**Source Format:**
```csv
ID,Date,Type,Value,Currency,Info,Bitcoins,Bitcoin_Fee,Money,Money_Fee_Rate
```

**Example:**
```
1,2012-11-02 19:42:58,buy_btc,in,EUR,,1.00000000,0.00600000,8.25,102.73
```

**Mapping:**
```
Date                    → transaction_date (parse + UTC)
Type                    → transaction_type (buy_btc→BUY, sell_btc→SELL)
Bitcoins - Bitcoin_Fee  → amount (NET BTC received after fees!)
Money / (Bitcoins - Bitcoin_Fee) → price_per_unit
Money                   → total_value (in EUR/USD/JPY, convert to EUR)
Bitcoin_Fee * price     → fee_amount (convert BTC fee to EUR)
Currency + Money_Fee_Rate → convert to EUR if needed
"EUR"                   → fee_currency, currency (after conversion)
"Mt.Gox"                → exchange_name
ID                      → transaction_id (for deduplication)
```

**Special Cases:**
- **CRITICAL:** BTC amount = NET (Bitcoins - Bitcoin_Fee)
  - Mt.Gox gave you NET BTC in your wallet
  - Fee was already deducted
- Multi-currency: EUR, USD, JPY
  - EUR: use as-is
  - JPY: divide by Money_Fee_Rate (or ~102.73)
  - USD: divide by 1.28
- Deduplication: Group by ID, keep first occurrence
- SELL transactions: Bitcoin_Fee = 0 (fee was on BUY side)

---

### 4. BITSTAMP ✅ (COMPLETED)

**File:** `data/bitstamp_history.csv`
**Importer:** `importers/import_bitstamp.py`
**Status:** ✅ Imported (3,823 transactions, €1,784.96 fees)

**Source Format:**
```csv
Type,Datetime,Account,Amount,Value,Rate,Fee,Sub Type
```

**Example:**
```
Market,2012-11-18 16:55:00,Main Account,4.00000000 BTC,45.52 USD,11.38 USD,0.23 USD,Sell
```

**Mapping:**
```
Datetime                → transaction_date (parse + UTC)
Sub Type                → transaction_type (Buy→BUY, Sell→SELL)
Amount                  → amount (parse "4.00000000 BTC" → 4.0)
Value / Amount          → price_per_unit (in USD, convert to EUR)
Value                   → total_value (parse "45.52 USD" → 45.52, convert to EUR)
Fee                     → fee_amount (parse "0.23 USD" → 0.23, convert to EUR)
"EUR"                   → fee_currency, currency (after conversion)
"Bitstamp"              → exchange_name
```

**Special Cases:**
- Currency: USD → EUR conversion (÷ 1.28)
- Filter: Type == "Market" only (ignore Deposit, Withdrawal)
- Some fees = 0 (legitimate)

---

### 5. TRT (TheRockTrading) ✅ (COMPLETED)

**File:** `data/trt.csv`
**Importer:** `importers/import_trt.py`
**Status:** ✅ Imported (173 transactions, €158.19 fees)

**Source Format (Multi-line):**
```csv
EUR,paid_commission,12/08/2013 19:12:58,390,BTC:EUR,ID_2621708,closed
BTC,acquired_currency_from_fund,12/08/2013 19:12:58,250000000,BTC:EUR,ID_2621708,closed
EUR,bought_currency_from_fund,12/08/2013 19:12:58,18487,BTC:EUR,ID_2621708,closed
```

**Mapping (BUY pattern - 3 lines per trade):**
```
Line 1 (EUR, paid_commission):    → fee_amount (cents/100)
Line 2 (BTC, acquired_currency):  → amount (satoshi/100000000)
Line 3 (EUR, bought_currency):    → total_value (cents/100)
Timestamp from any line           → transaction_date
"BUY"                             → transaction_type
Price = total_value / amount      → price_per_unit
"TRT"                             → exchange_name
```

**Mapping (SELL pattern - 4 lines per trade):**
```
Line 1 (EUR, paid_commission):    → fee_amount
Line 2 (BTC, released_currency):  → amount
Line 3 (EUR, sold_currency):      → total_value
Line 4: skip
```

**Special Cases:**
- **CRITICAL:** Multi-line records grouped by timestamp
- Units: satoshi → BTC (÷ 100,000,000), cents → EUR (÷ 100)
- Trade direction determined by operation type
- Must reconstruct complete trades from 3-4 line groups

---

### 6. KRAKEN ✅ (COMPLETED)

**File:** `data/kraken_ledgers.csv`
**Importer:** `importers/import_kraken.py`
**Status:** ✅ Imported (377 transactions, €735.49 fees)

**Source Format (Paired rows):**
```csv
txid,refid,time,type,subtype,aclass,asset,amount,fee,balance
```

**Example (SELL):**
```
SELL pair with refid TKZYRQ-N3NJQ-U22GGX:
Row 1: type=trade, asset=XXBT, amount=-2.0, fee=0
Row 2: type=trade, asset=ZEUR, amount=4540.16, fee=7.26
```

**Mapping:**
```
Pair BTC row + EUR row by refid  → one transaction
BTC amount sign                   → transaction_type (negative=SELL, positive=BUY)
abs(BTC amount)                   → amount
EUR amount / abs(BTC amount)      → price_per_unit
abs(EUR amount)                   → total_value
EUR row fee                       → fee_amount
time                              → transaction_date
refid                             → transaction_id
"Kraken"                          → exchange_name
```

**Special Cases:**
- **CRITICAL:** Each trade = 2 rows (BTC + EUR) linked by refid
- Fee always on EUR row (BTC row fee = 0)
- Sign of BTC amount determines BUY/SELL
- Must pair rows before creating transaction

---

### 7. BINANCE CARD 🔴 (TODO)

**File:** `data/binance_card.csv`
**Importer:** `importers/import_binance_card.py` (TO CREATE)
**Status:** ❌ Not imported yet (859 SELL, 3.86 BTC)

**Source Format (Paired rows):**
```csv
id,datetime_tz_CET,type,label,market_model_type,order_type,sent_amount,sent_currency,sent_value_EUR,sent_address,received_amount,received_currency,received_value_EUR,received_address,fee_amount,fee_currency,fee_value_EUR,differenza
```

**Example:**
```
Row 1: type=Sell, sent_amount=0.00035, sent_currency=BTC, received_amount=8.83, received_currency=EUR, differenza=0.2915
Row 2: type=Send, label=Payment, sent_amount=8.83, sent_currency=EUR
```

**Mapping (use Sell row only, ignore Send):**
```
datetime_tz_CET         → transaction_date (parse CET timezone)
"SELL"                  → transaction_type
sent_amount             → amount (BTC sold)
received_amount/sent_amount → price_per_unit
received_amount         → total_value (EUR received BEFORE fee)
differenza              → fee_amount (difference = fee)
"EUR"                   → fee_currency, currency
id                      → transaction_id
"Binance Card"          → exchange_name
```

**Special Cases:**
- Each purchase = 2 rows (Sell + Send)
- ONLY import Sell rows (type == "Sell")
- Ignore Send/Payment rows
- Fee = "differenza" column
- 859 transactions (2023-2024)

---

### 8. WIREX 🔴 (TODO)

**Files:** 
- `data/wirex_2023.csv` (UTF-8 converted)
- `data/wirex_2024.csv` (UTF-8 converted)
- `data/wirex_2025.csv` (UTF-8 converted)

**Importer:** `importers/import_wirex.py` (TO CREATE)
**Status:** ❌ Not imported yet (209 SELL total: 100+59+50)

**Source Format:**
```csv
Completed Date;Type;Description;Amount;Account Currency;Rate;Foreign Amount;Foreign Currency;Balance;Related Entity ID
```

**Example:**
```
10-01-2024 11:36:28;Card Payment;Card 5644 : OF,London,GBR;-0.000833;BTC;;;;0.452125;
```

**Mapping:**
```
Completed Date          → transaction_date (parse DD-MM-YYYY HH:MM:SS + UTC)
"SELL"                  → transaction_type (all Card Payments are sells)
abs(Amount)             → amount (remove negative sign)
Rate (if present)       → price_per_unit (EUR/BTC rate)
Rate * Amount           → total_value (calculate EUR value)
0                       → fee_amount (no separate fee column)
"EUR"                   → fee_currency, currency
Related Entity ID       → transaction_id
"Wirex"                 → exchange_name
Description             → notes (merchant info)
```

**Special Cases:**
- **CRITICAL:** Files are UTF-16LE, MUST convert to UTF-8 first!
- Delimiter: semicolon `;` not comma
- All Card Payments are SELL transactions
- Amount is negative (remove sign)
- If Rate column empty, calculate from Foreign Amount/Amount
- 3 separate files (2023, 2024, 2025)

---

### 9. REVOLUT 🔴 (TODO)

**File:** `data/revolut_crypto.csv`
**Importer:** `importers/import_revolut.py` (TO CREATE)
**Status:** ❌ Not imported yet (12 transactions: 6 BUY + 6 SELL)

**Source Format:**
```csv
Symbol,Type,Quantity,Price,Value,Fees,Date
```

**Example:**
```
BTC,Buy,0.15099065,€6622.93,€1000.00,€0.00,Jun 7 2018 9:10:51 AM
BTC,Sell,0.01,€6809.58,€68.09,€0.00,Jul 31 2018 8:14:36 AM
```

**Mapping:**
```
Date                    → transaction_date (parse "Jun 7 2018 9:10:51 AM" + UTC)
Type                    → transaction_type (Buy→BUY, Sell→SELL)
Symbol                  → cryptocurrency
Quantity                → amount
Price (parse €6622.93)  → price_per_unit (remove € symbol)
Value (parse €1000.00)  → total_value (remove € symbol)
Fees (parse €0.00)      → fee_amount (remove € symbol)
"EUR"                   → fee_currency, currency
"Revolut"               → exchange_name
```

**Special Cases:**
- Currency symbols with € prefix
- Date format: "Jun 7 2018 9:10:51 AM"
- Very small dataset (12 transactions)
- 2018 only

---

### 10. BITFINEX 🔴 (TODO)

**File:** `data/bitfinex_trades.csv`
**Importer:** `importers/import_bitfinex.py` (TO CREATE)
**Status:** ❌ Not imported yet (180 transactions: 119 BUY + 60 SELL + 1 BTC/EUR)

**Source Format:**
```csv
#,PAIR,AMOUNT,PRICE,FEE,FEE PERC,FEE CURRENCY,DATE,ORDER ID
```

**Example:**
```
183031541,BTC/EUR,1.3,7500,-0.001105,0.09%,BTC,2018-02-01 13:20:43,5525114532
27988722,BTC/USD,0.028,974.39,-0.000028,0.10%,BTC,2017-03-26 17:35:52,2166553981
```

**Mapping:**
```
DATE                    → transaction_date (parse + UTC)
AMOUNT sign             → transaction_type (positive=BUY, negative=SELL)
abs(AMOUNT)             → amount
PRICE                   → price_per_unit (in USD or EUR)
PRICE * abs(AMOUNT)     → total_value (convert USD to EUR if needed)
abs(FEE)                → fee_amount (convert to EUR: fee in BTC * price)
"EUR"                   → fee_currency, currency (after conversion)
#                       → transaction_id
"Bitfinex"              → exchange_name
PAIR                    → notes (for reference)
```

**Special Cases:**
- **Multi-pair:** BTC/USD (179), BTC/EUR (1), ignore altcoins
- **USD conversion:** Use 1.28 rate (2014-2018 average)
- **Fee in BTC:** Convert to EUR: abs(FEE) * PRICE * (1/1.28 if USD)
- Amount sign determines direction (pos=BUY, neg=SELL)
- 2014-2018 historical data

---

### 11. MANUAL OTC TRANSACTIONS ✅ (READY)

**Files (Standard format):**
- `data/gdtre.csv` - Family office (1 BUY, 9.245 BTC)
- `data/coinpal.csv` - First purchase (1 BUY, 40 BTC)
- `data/binance_otc_2024.csv` - OTC sale (1 SELL, 30 BTC)

**Importer:** `importers/import_standard_csv.py` (GENERIC, already created)
**Status:** ✅ Ready to import (already in standard format)

**Format (Standard):**
```csv
transaction_date,transaction_type,cryptocurrency,amount,price_per_unit,total_value,fee_amount,fee_currency,currency,exchange_name,transaction_id,notes
```

**Mapping:**
```
Direct 1:1 mapping - no transformation needed
```

**Usage:**
```bash
python3 importers/import_standard_csv.py data/gdtre.csv GDTRE
python3 importers/import_standard_csv.py data/coinpal.csv Coinpal
python3 importers/import_standard_csv.py data/binance_otc_2024.csv "Binance OTC"
```

**Special Cases:**
- Coinpal: USD → EUR conversion already done in file
- All ready to import immediately

---

## IMPORT ORDER (PRIORITY)

### Phase 1: CRITICAL (Huge impact on cost basis/Anexo G1)
1. ✅ **Coinbase** - 52,036 tx (done)
2. ✅ **Binance** - 4,905 tx (done)
3. 🔴 **Coinpal** - 40 BTC @ $1.01 (2011) → LOWEST cost basis ever
4. 🔴 **Binance OTC** - 30 BTC @ €62,802 (2024) → LARGEST 2024 sale
5. 🔴 **GDTRE** - 9.245 BTC @ €11,870 (2020)

### Phase 2: HISTORICAL (Impact cost basis)
6. ✅ **Mt.Gox** - 740 tx (done)
7. ✅ **Bitstamp** - 3,823 tx (done)
8. ✅ **TRT** - 173 tx (done)
9. ✅ **Kraken** - 377 tx (done)
10. 🔴 **Bitfinex** - 180 tx (2014-2018 historical)

### Phase 3: CARD TRANSACTIONS (Many small sells)
11. 🔴 **Binance Card** - 859 SELL
12. 🔴 **Wirex** - 209 SELL

### Phase 4: MINOR
13. 🔴 **Revolut** - 12 tx only

---

## VERIFICATION CHECKLIST

After each import, run:
```bash
python3 reports/verify_exchange_import.py <ExchangeName>
```

**Checks:**
- ✅ Transaction counts by type
- ✅ Fee statistics (count, total, avg)
- ✅ BTC and EUR totals
- ✅ Date range
- ✅ Sample transactions
- ⚠️ Missing fees warning
- ⚠️ Zero amounts warning
- ⚠️ Price range sanity check

---

## COMMON ISSUES & SOLUTIONS

### Issue 1: UTF-16 Encoding (Wirex)
**Symptom:** grep doesn't find text, file appears empty
**Solution:** 
```bash
iconv -f UTF-16LE -t UTF-8 original.csv > fixed.csv
```

### Issue 2: Multi-line Records (TRT, Kraken)
**Symptom:** Row count doesn't match transaction count
**Solution:** Group rows by timestamp/refid before processing

### Issue 3: Multi-currency (Mt.Gox, Bitstamp, Bitfinex)
**Symptom:** Values in USD/JPY instead of EUR
**Solution:** Apply conversion rates (USD: 1.28, JPY: 102.73)

### Issue 4: Fee Currency Mismatch
**Symptom:** Fee in BTC but need EUR
**Solution:** fee_eur = fee_btc * price_per_btc * usd_to_eur_rate

### Issue 5: NET vs GROSS Amounts (Mt.Gox)
**Symptom:** Balance doesn't match after import
**Solution:** Use NET amount (Bitcoins - Bitcoin_Fee) for Mt.Gox

### Issue 6: Paired Transactions (Binance Card, Kraken)
**Symptom:** Duplicate entries or wrong counts
**Solution:** Only import Sell rows (Binance Card) or pair by refid (Kraken)

---

## DATABASE VALIDATION

After all imports complete:

```sql
-- Total transactions
SELECT COUNT(*) FROM transactions;

-- By exchange
SELECT exchange_name, COUNT(*) FROM transactions GROUP BY exchange_name;

-- By type
SELECT transaction_type, COUNT(*) FROM transactions GROUP BY transaction_type;

-- Missing fees
SELECT COUNT(*) FROM transactions 
WHERE transaction_type IN ('BUY','SELL') 
AND (fee_amount IS NULL OR fee_amount = 0);

-- Fee totals by exchange
SELECT exchange_name, SUM(fee_amount) as total_fees 
FROM transactions 
GROUP BY exchange_name 
ORDER BY total_fees DESC;

-- BTC balance check
SELECT 
    SUM(CASE WHEN transaction_type = 'BUY' THEN amount ELSE 0 END) as bought,
    SUM(CASE WHEN transaction_type = 'SELL' THEN amount ELSE 0 END) as sold,
    SUM(CASE WHEN transaction_type = 'DEPOSIT' THEN amount ELSE 0 END) as deposited,
    SUM(CASE WHEN transaction_type = 'WITHDRAWAL' THEN amount ELSE 0 END) as withdrawn
FROM transactions 
WHERE cryptocurrency = 'BTC';
```

---

## FINAL CHECKLIST

Before FIFO recalculation:

- [ ] All exchanges imported
- [ ] All transactions verified
- [ ] Fee amounts present (except where legitimately 0)
- [ ] No negative amounts
- [ ] No negative prices
- [ ] Date ranges correct
- [ ] BTC balance reasonable
- [ ] Backup database created

Then run:
```bash
python3 calculators/calculate_fifo.py
python3 reports/generate_anexo_g1_excel.py
```

---

**END OF MAPPING DOCUMENTATION**

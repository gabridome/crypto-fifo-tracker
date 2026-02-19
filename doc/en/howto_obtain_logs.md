# How to Download Data from Exchanges

## Coinbase Prime

After login, go to **Activity → Orders → Download CSV**.

The report contains columns: order_id, status, product, side, type, limit_price, average_filled_price, etc.
Values are in USD — the `import_coinbase_prime.py` importer converts to EUR via `ecb_rates.py`.

## Binance

Go to **My Orders → Trade History** → export icon (second row).

Direct URL: `https://www.binance.com/en/my/orders/exchange/usertrade`

Format:
```csv
"Date(UTC)","Pair","Side","Price","Executed","Amount","Fee"
"YYYY-MM-DD HH:MM:SS","BTCEUR","BUY","XXXXX.XX","X.XXXXXBTC","XXXX.XXXXXXXEUR","X.XXXXXXXXXBTC"
```

Quantities include a currency suffix (e.g. "0.02776BTC", "1731.98EUR"). The importer strips them automatically.

## Coinbase

**Settings → Activity → Generate Report**. Select CSV format.

Format:
```csv
Timestamp,Transaction Type,Asset,Quantity Transacted,EUR Spot Price at Transaction,EUR Subtotal,EUR Total (inclusive of fees and/or spread),EUR Fees and/or Spread,Notes
```

## Bitstamp

**Transaction History → Export**. Values are in USD — converted to EUR during import.

## Kraken

**History → Export → Ledgers**. Format with columns: txid, refid, time, type, subtype, aclass, asset, amount, fee, balance.

## Bitfinex

**Reports → Trade History → Export CSV**. Values are in USD — converted to EUR during import.

## Wirex

**Transaction History → Download**. Note: the file may be in UTF-16 encoding. Convert first:
```bash
iconv -f UTF-16LE -t UTF-8 wirex.csv > wirex_utf8.csv
```

## Revolut

**Crypto → Statement → Download CSV**.

## TRT (TheRockTrading)

Use the archived CSV file. Proprietary format with possible multi-line records.

## Mt.Gox

Use the archived CSV file. Prices may be in USD or JPY — the importer handles the conversion.

## Manual transactions (OTC, inheritance, etc.)

Create a CSV with the standard format:
```csv
transaction_date,transaction_type,cryptocurrency,amount,price_per_unit,total_value,fee_amount,fee_currency,currency,exchange_name,transaction_id,notes
YYYY-MM-DDTHH:MM:SS+00:00,BUY,BTC,X.XX,XXXXX.XX,XXXXX.XX,XX.XX,EUR,EUR,OTC,manual_001,Description
```

Import with:
```bash
python3 importers/import_standard_csv.py data/file.csv "Exchange Name"
```

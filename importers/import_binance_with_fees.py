"""
Import Binance Trade History with Fees
New format: binance_trade_history_all.csv

Format:
"Date(UTC)","Pair","Side","Price","Executed","Amount","Fee"
"2024-10-18 01:29:50","BTCEUR","SELL","62391.49","0.02776BTC","1731.9877624EUR","1.73198776EUR"
"""

import pandas as pd
import sqlite3
from datetime import datetime

DB_PATH = 'data/crypto_fifo.db'
BINANCE_FILE = 'data/binance_trade_history_all.csv'

print("="*80)
print("IMPORTING BINANCE TRADE HISTORY WITH FEES")
print("="*80)

# Read file
df = pd.read_csv(BINANCE_FILE)
print(f"\nLoaded {len(df):,} rows from {BINANCE_FILE}")
print(f"Columns: {df.columns.tolist()}")

# Parse columns
def parse_crypto_amount(value):
    """Parse '0.02776BTC' or '1731.9877624EUR' or '157.978248USDT' -> float"""
    if pd.isna(value):
        return 0
    # Remove all currency symbols
    value_str = str(value).replace('BTC', '').replace('EUR', '').replace('USDT', '').replace('BUSD', '').strip()
    try:
        return float(value_str)
    except:
        return 0

# Filter BTC trades only
df_btc = df[df['Pair'] == 'BTCEUR'].copy()
print(f"\nBTC/EUR trades: {len(df_btc):,}")

# Show skipped pairs
other_pairs = df[df['Pair'] != 'BTCEUR']['Pair'].value_counts()
if len(other_pairs) > 0:
    print(f"\n⚠️  Skipped pairs (not BTCEUR):")
    for pair, count in other_pairs.items():
        print(f"    {pair}: {count:,} trades")

df_btc['date_parsed'] = pd.to_datetime(df_btc['Date(UTC)'])
df_btc['btc_amount'] = df_btc['Executed'].apply(parse_crypto_amount)
df_btc['eur_amount'] = df_btc['Amount'].apply(parse_crypto_amount)
df_btc['fee_eur'] = df_btc['Fee'].apply(parse_crypto_amount)
df_btc['price_per_btc'] = df_btc['Price'].astype(float)


# Separate BUY and SELL
df_buy = df_btc[df_btc['Side'] == 'BUY'].copy()
df_sell = df_btc[df_btc['Side'] == 'SELL'].copy()

print(f"  BUY:  {len(df_buy):,} trades")
print(f"  SELL: {len(df_sell):,} trades")

# Check fee consistency
print(f"\nFee analysis:")
print(f"  Total fees (all): €{df_btc['fee_eur'].sum():,.2f}")
print(f"  Average fee: €{df_btc['fee_eur'].mean():.2f}")
print(f"  Fee as % of amount: {(df_btc['fee_eur'].sum() / df_btc['eur_amount'].sum() * 100):.4f}%")

# Connect to database
conn = sqlite3.connect(DB_PATH)
cursor = conn.cursor()

# Check current Binance data
cursor.execute("""
    SELECT COUNT(*), MIN(transaction_date), MAX(transaction_date)
    FROM transactions
    WHERE exchange_name = 'Binance'
    AND cryptocurrency = 'BTC'
""")
current_data = cursor.fetchone()
print(f"\nCurrent Binance BTC data in DB:")
print(f"  Transactions: {current_data[0]:,}")
print(f"  Date range: {current_data[1]} to {current_data[2]}")

# Prepare transactions for insert
transactions_to_insert = []

# Process BUY transactions
for _, row in df_buy.iterrows():
    transactions_to_insert.append({
        'date': row['date_parsed'].isoformat(),
        'type': 'BUY',
        'exchange': 'Binance',
        'crypto': 'BTC',
        'amount': row['btc_amount'],
        'price': row['price_per_btc'],
        'total': row['eur_amount'],
        'fee': row['fee_eur']
    })

# Process SELL transactions
for _, row in df_sell.iterrows():
    transactions_to_insert.append({
        'date': row['date_parsed'].isoformat(),
        'type': 'SELL',
        'exchange': 'Binance',
        'crypto': 'BTC',
        'amount': row['btc_amount'],
        'price': row['price_per_btc'],
        'total': row['eur_amount'],
        'fee': row['fee_eur']
    })

print(f"\n\nPrepared {len(transactions_to_insert):,} transactions for import")

# Show user what will happen
print("\n" + "="*80)
print("DECISION POINT")
print("="*80)
print(f"\nCurrent DB has: {current_data[0]:,} Binance BTC transactions")
print(f"New file has: {len(transactions_to_insert):,} transactions (fees: included)")
print("\nOptions:")
print("1. DELETE existing Binance BTC data and import new (RECOMMENDED)")
print("2. Keep existing data (abort)")

choice = input("\nEnter choice (1 or 2): ").strip()

if choice != '1':
    print("\n✗ Aborted. No changes made.")
    conn.close()
    exit(0)

# Delete existing Binance BTC data
print("\nDeleting existing Binance BTC data...")
cursor.execute("""
    DELETE FROM transactions
    WHERE exchange_name = 'Binance'
    AND cryptocurrency = 'BTC'
""")
deleted = cursor.rowcount
print(f"  Deleted: {deleted:,} transactions")

# Insert new data
print("\nInserting new data with fees...")
inserted = 0
for tx in transactions_to_insert:
    cursor.execute("""
        INSERT INTO transactions (
            transaction_date, transaction_type, exchange_name, cryptocurrency,
            amount, price_per_unit, total_value, fee_amount
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        tx['date'], tx['type'], tx['exchange'], tx['crypto'],
        tx['amount'], tx['price'], tx['total'], tx['fee']
    ))
    inserted += 1

conn.commit()
conn.close()

print(f"  Inserted: {inserted:,} transactions")

# Verify
conn = sqlite3.connect(DB_PATH)
cursor = conn.cursor()

cursor.execute("""
    SELECT 
        transaction_type,
        COUNT(*) as count,
        SUM(amount) as total_btc,
        SUM(total_value) as total_eur,
        SUM(fee_amount) as total_fees
    FROM transactions
    WHERE exchange_name = 'Binance'
    AND cryptocurrency = 'BTC'
    GROUP BY transaction_type
""")

print("\n" + "="*80)
print("VERIFICATION")
print("="*80)

for row in cursor.fetchall():
    tx_type, count, btc, eur, fees = row
    print(f"\n{tx_type}:")
    print(f"  Transactions: {count:,}")
    print(f"  BTC: {btc:.8f}")
    print(f"  EUR: €{eur:,.2f}")
    print(f"  Fees: €{fees:,.2f}")

conn.close()

print("\n" + "="*80)
print("SUCCESS!")
print("="*80)
print("\n✓ Binance data imported with fees")
print("\nNext steps:")
print("1. Import other exchanges with fees (Coinbase, Mt.Gox, Bitstamp)")
print("2. Re-run FIFO calculation with fee-inclusive cost basis")
print("3. Re-generate Anexo G1 with correct fees")

print("\n" + "="*80)

"""
Bitfinex Trade History Importer
Handles BTC/USD and BTC/EUR trades with fee conversion
"""

import pandas as pd
import sqlite3
from datetime import datetime
import pytz

from config import DATABASE_PATH
DB_PATH = DATABASE_PATH

BITFINEX_FILE = 'data/bitfinex_trades.csv'
USD_TO_EUR_RATE = 1.28  # Historical average 2014-2018

print("="*80)
print("IMPORTING BITFINEX TRADE HISTORY")
print("="*80)

# Read CSV
df = pd.read_csv(BITFINEX_FILE)
print(f"\nLoaded {len(df):,} rows from {BITFINEX_FILE}")
print(f"Columns: {list(df.columns)}")

# Filter only BTC trades (exclude altcoin pairs)
df_btc = df[df['PAIR'].isin(['BTC/EUR', 'BTC/USD'])].copy()
print(f"\nBTC trades: {len(df_btc):,} (filtered from {len(df):,} total)")

print(f"\nTrade pairs:")
print(df_btc['PAIR'].value_counts())

# Parse data
df_btc['date_parsed'] = pd.to_datetime(df_btc['DATE'])
df_btc['transaction_type'] = df_btc['AMOUNT'].apply(lambda x: 'BUY' if x > 0 else 'SELL')
df_btc['amount'] = df_btc['AMOUNT'].abs()
df_btc['price_usd_or_eur'] = df_btc['PRICE'].astype(float)

# Convert prices to EUR
df_btc['price_per_unit'] = df_btc.apply(
    lambda row: row['price_usd_or_eur'] / USD_TO_EUR_RATE if row['PAIR'] == 'BTC/USD' else row['price_usd_or_eur'],
    axis=1
)

# Calculate total value in EUR
df_btc['total_value'] = df_btc['amount'] * df_btc['price_per_unit']

# Convert fees to EUR
# Fees are in BTC or the traded currency, convert to EUR value
df_btc['fee_btc'] = df_btc['FEE'].abs()
df_btc['fee_amount'] = df_btc['fee_btc'] * df_btc['price_per_unit']

# Statistics
print(f"\nTransaction types:")
print(df_btc['transaction_type'].value_counts())

print(f"\nDate range: {df_btc['date_parsed'].min()} to {df_btc['date_parsed'].max()}")

print(f"\nTotal fees: €{df_btc['fee_amount'].sum():.2f}")

btc_buy = df_btc[df_btc['transaction_type'] == 'BUY']['amount'].sum()
btc_sell = df_btc[df_btc['transaction_type'] == 'SELL']['amount'].sum()
print(f"\nBTC purchased: {btc_buy:.8f}")
print(f"BTC sold: {btc_sell:.8f}")

# Connect to database
conn = sqlite3.connect(DB_PATH)
cursor = conn.cursor()

# Check existing Bitfinex data
cursor.execute("""
    SELECT COUNT(*) FROM transactions
    WHERE exchange_name = 'Bitfinex'
    AND cryptocurrency = 'BTC'
""")
existing_count = cursor.fetchone()[0]

print(f"\nCurrent DB has: {existing_count:,} Bitfinex BTC transactions")

# Show sample
print("\n" + "="*80)
print("SAMPLE TRANSACTIONS (first 5):")
print("="*80)

for i, (_, row) in enumerate(df_btc.head(5).iterrows(), 1):
    print(f"\n{i}. {row['transaction_type']} on {row['date_parsed'].strftime('%Y-%m-%d')}")
    print(f"   Amount: {row['amount']:.8f} BTC")
    print(f"   Price:  €{row['price_per_unit']:.2f}/BTC ({row['PAIR']})")
    print(f"   Total:  €{row['total_value']:.2f}")
    print(f"   Fee:    €{row['fee_amount']:.4f} ({row['fee_btc']:.8f} BTC)")

# Ask for confirmation
print("\n" + "="*80)
print("DECISION POINT")
print("="*80)
print(f"\nCurrent DB has: {existing_count:,} Bitfinex transactions")
print(f"New import will add: {len(df_btc):,} transactions")
print(f"\nUsing USD to EUR rate: {USD_TO_EUR_RATE}")
print("\nOptions:")
print("1. DELETE existing Bitfinex data and import new (RECOMMENDED)")
print("2. APPEND new data (keep existing)")
print("3. Cancel (no changes)")

choice = input("\nEnter choice (1, 2, or 3): ").strip()

if choice == '1':
    print("\nDeleting existing Bitfinex data...")
    cursor.execute("DELETE FROM transactions WHERE exchange_name = 'Bitfinex'")
    deleted = cursor.rowcount
    print(f"  Deleted: {deleted:,} transactions")
elif choice != '2':
    print("\n✗ Aborted. No changes made.")
    conn.close()
    exit(0)

# Insert new data
print("\nInserting new data...")
inserted = 0

for _, row in df_btc.iterrows():
    dt = row['date_parsed']
    if dt.tzinfo is None:
        dt = pytz.UTC.localize(dt)
    
    cursor.execute("""
        INSERT INTO transactions (
            transaction_date, transaction_type, exchange_name, cryptocurrency,
            amount, price_per_unit, total_value, fee_amount, fee_currency, currency,
            transaction_id, notes
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        dt.isoformat(),
        row['transaction_type'],
        'Bitfinex',
        'BTC',
        row['amount'],
        row['price_per_unit'],
        row['total_value'],
        row['fee_amount'],
        'EUR',
        'EUR',
        str(row['#']),
        f"Original pair: {row['PAIR']}, Fee: {row['fee_btc']:.8f} BTC"
    ))
    inserted += 1

conn.commit()
print(f"  Inserted: {inserted:,} transactions")

# Verify
cursor.execute("""
    SELECT 
        transaction_type,
        COUNT(*) as count,
        SUM(amount) as total_btc,
        SUM(total_value) as total_eur,
        SUM(fee_amount) as total_fees
    FROM transactions
    WHERE exchange_name = 'Bitfinex'
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
    print(f"  Fees: €{fees:.2f}")

conn.close()

print("\n" + "="*80)
print("SUCCESS!")
print("="*80)
print("\n✓ Bitfinex data imported with USD to EUR conversion")
print(f"✓ Conversion rate used: {USD_TO_EUR_RATE}")
print("\nNext step:")
print("  python3 reports/verify_exchange_import.py Bitfinex")
print("\n" + "="*80)

"""
Binance Card Transaction Importer
Handles card payment transactions (Sell + Send pairs)
Only imports Sell rows (ignores Send/Payment rows)
"""

import pandas as pd
import sqlite3
from datetime import datetime
import pytz

from config import DATABASE_PATH
DB_PATH = DATABASE_PATH

BINANCE_CARD_FILE = 'data/binance_card.csv'

print("="*80)
print("IMPORTING BINANCE CARD TRANSACTIONS")
print("="*80)

# Read CSV
df = pd.read_csv(BINANCE_CARD_FILE)
print(f"\nLoaded {len(df):,} rows from {BINANCE_CARD_FILE}")
print(f"Columns: {list(df.columns)}")

# Filter only Sell transactions (ignore Send/Payment rows)
df_sell = df[df['type'] == 'Sell'].copy()
print(f"\nSell transactions: {len(df_sell):,} (filtered from {len(df):,} total)")

# Parse datetime with CET timezone
def parse_cet_datetime(dt_str):
    """Parse datetime in CET timezone: '2023-03-09-13:26:06'"""
    # Replace first two hyphens with spaces for parsing
    parts = dt_str.split('-')
    if len(parts) >= 4:
        # Format: YYYY-MM-DD-HH:MM:SS
        dt_str_fixed = f"{parts[0]}-{parts[1]}-{parts[2]} {parts[3]}"
        dt = datetime.strptime(dt_str_fixed, '%Y-%m-%d %H:%M:%S')
        # CET is UTC+1, but we'll store as UTC for consistency
        cet = pytz.timezone('CET')
        dt_cet = cet.localize(dt)
        return dt_cet.astimezone(pytz.UTC)
    return pytz.UTC.localize(datetime.strptime(dt_str, '%Y-%m-%d %H:%M:%S'))

df_sell['date_parsed'] = df_sell['datetime_tz_CET'].apply(parse_cet_datetime)
df_sell['cryptocurrency'] = df_sell['sent_currency']
df_sell['amount'] = df_sell['sent_amount'].astype(float)
df_sell['eur_received'] = df_sell['received_amount'].astype(float)
df_sell['price_per_unit'] = df_sell['eur_received'] / df_sell['amount']
df_sell['total_value'] = df_sell['eur_received']  # EUR received BEFORE fee
df_sell['fee_amount'] = df_sell['differenza'].astype(float)  # Fee is in differenza column

# Filter only BTC (in case there are other cryptos)
df_btc = df_sell[df_sell['cryptocurrency'] == 'BTC'].copy()
print(f"\nBTC sell transactions: {len(df_btc):,}")

# Statistics
print(f"\nDate range: {df_btc['date_parsed'].min()} to {df_btc['date_parsed'].max()}")
print(f"\nTotal BTC sold: {df_btc['amount'].sum():.8f}")
print(f"Total EUR received: €{df_btc['total_value'].sum():,.2f}")
print(f"Total fees: €{df_btc['fee_amount'].sum():.2f}")
print(f"Average fee: €{df_btc['fee_amount'].mean():.4f}")

# Connect to database
conn = sqlite3.connect(DB_PATH)
cursor = conn.cursor()

# Check existing Binance Card data
cursor.execute("""
    SELECT COUNT(*) FROM transactions
    WHERE exchange_name = 'Binance Card'
    AND cryptocurrency = 'BTC'
""")
existing_count = cursor.fetchone()[0]

print(f"\nCurrent DB has: {existing_count:,} Binance Card BTC transactions")

# Show sample
print("\n" + "="*80)
print("SAMPLE TRANSACTIONS (first 5):")
print("="*80)

for i, (_, row) in enumerate(df_btc.head(5).iterrows(), 1):
    print(f"\n{i}. SELL on {row['date_parsed'].strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"   Amount: {row['amount']:.8f} BTC")
    print(f"   Price:  €{row['price_per_unit']:.2f}/BTC")
    print(f"   Total:  €{row['total_value']:.2f}")
    print(f"   Fee:    €{row['fee_amount']:.4f}")
    print(f"   ID:     {row['id']}")

# Ask for confirmation
print("\n" + "="*80)
print("DECISION POINT")
print("="*80)
print(f"\nCurrent DB has: {existing_count:,} Binance Card transactions")
print(f"New import will add: {len(df_btc):,} transactions")
print("\nNOTE: Only importing Sell rows (ignoring Send/Payment rows)")
print("\nOptions:")
print("1. DELETE existing Binance Card data and import new (RECOMMENDED)")
print("2. APPEND new data (keep existing)")
print("3. Cancel (no changes)")

choice = input("\nEnter choice (1, 2, or 3): ").strip()

if choice == '1':
    print("\nDeleting existing Binance Card data...")
    cursor.execute("DELETE FROM transactions WHERE exchange_name = 'Binance Card'")
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
    cursor.execute("""
        INSERT INTO transactions (
            transaction_date, transaction_type, exchange_name, cryptocurrency,
            amount, price_per_unit, total_value, fee_amount, fee_currency, currency,
            transaction_id, notes
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        row['date_parsed'].isoformat(),
        'SELL',
        'Binance Card',
        row['cryptocurrency'],
        row['amount'],
        row['price_per_unit'],
        row['total_value'],
        row['fee_amount'],
        'EUR',
        'EUR',
        row['id'],
        row.get('label', '')
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
    WHERE exchange_name = 'Binance Card'
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
print("\n✓ Binance Card data imported")
print("✓ Only Sell transactions imported (Send/Payment rows ignored)")
print("\nNext step:")
print("  python3 reports/verify_exchange_import.py 'Binance Card'")
print("\n" + "="*80)

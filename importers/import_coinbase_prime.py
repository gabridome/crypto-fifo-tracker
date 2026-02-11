"""
Coinbase Prime Orders Importer
Handles BUY/SELL orders with USD to EUR conversion using ECB historical rates
"""

import pandas as pd
import sqlite3
from datetime import datetime
import pytz
from ecb_rates import ECBRates

from config import DATABASE_PATH
DB_PATH = DATABASE_PATH

COINBASE_PRIME_FILE = 'data/coinbaseprime_orders.csv'

print("="*80)
print("IMPORTING COINBASE PRIME ORDERS WITH ECB RATES")
print("="*80)

# Load ECB rates
ecb = ECBRates()

# Read CSV
df = pd.read_csv(COINBASE_PRIME_FILE)
print(f"\nLoaded {len(df):,} rows from {COINBASE_PRIME_FILE}")
print(f"Columns: {list(df.columns)}")

# Filter only completed orders
df = df[df['status'] == 'Completed'].copy()
print(f"\nCompleted orders: {len(df):,}")

# Filter only BTC market
df = df[df['market'] == 'BTC/USD'].copy()
print(f"BTC/USD orders: {len(df):,}")

# Parse data
df['date_parsed'] = pd.to_datetime(df['initiated time'])
df['transaction_type'] = df['side'].map({'BUY': 'BUY', 'SELL': 'SELL'})
df['amount'] = df['filled base quantity'].astype(float)
df['price_usd'] = df['average fill price'].astype(float)
df['total_usd'] = df['filled quote quantity'].astype(float)
df['fee_usd'] = df['total fees and commissions'].astype(float)

# Convert USD to EUR using ECB historical rates
print("\nConverting USD to EUR using ECB historical rates...")
df['price_per_unit'] = df.apply(lambda row: ecb.usd_to_eur(row['price_usd'], row['date_parsed']), axis=1)
df['total_value'] = df.apply(lambda row: ecb.usd_to_eur(row['total_usd'], row['date_parsed']), axis=1)
df['fee_amount'] = df.apply(lambda row: ecb.usd_to_eur(row['fee_usd'], row['date_parsed']), axis=1)
df['usd_eur_rate'] = df.apply(lambda row: ecb.get_rate(row['date_parsed']), axis=1)

# Statistics
print(f"\nTransaction types:")
print(df['transaction_type'].value_counts())

print(f"\nDate range: {df['date_parsed'].min()} to {df['date_parsed'].max()}")
print(f"USD/EUR rates used: {df['usd_eur_rate'].min():.4f} to {df['usd_eur_rate'].max():.4f}")

print(f"\nTotal BTC:")
for tx_type in ['BUY', 'SELL']:
    btc = df[df['transaction_type'] == tx_type]['amount'].sum()
    eur = df[df['transaction_type'] == tx_type]['total_value'].sum()
    fee = df[df['transaction_type'] == tx_type]['fee_amount'].sum()
    print(f"  {tx_type}: {btc:.8f} BTC, €{eur:,.2f}, fees: €{fee:.2f}")

# Connect to database
conn = sqlite3.connect(DB_PATH)
cursor = conn.cursor()

# Check existing Coinbase Prime data
cursor.execute("""
    SELECT COUNT(*) FROM transactions
    WHERE exchange_name = 'Coinbase Prime'
    AND cryptocurrency = 'BTC'
""")
existing_count = cursor.fetchone()[0]

print(f"\nCurrent DB has: {existing_count:,} Coinbase Prime BTC transactions")

# Show sample
print("\n" + "="*80)
print("SAMPLE TRANSACTIONS (first 5):")
print("="*80)

for i, (_, row) in enumerate(df.head(5).iterrows(), 1):
    print(f"\n{i}. {row['transaction_type']} on {row['date_parsed'].strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"   Amount: {row['amount']:.8f} BTC")
    print(f"   Price:  €{row['price_per_unit']:.2f}/BTC (${row['price_usd']:.2f})")
    print(f"   Total:  €{row['total_value']:.2f}")
    print(f"   Fee:    €{row['fee_amount']:.2f}")
    print(f"   ID:     {row['order id']}")

# Ask for confirmation
print("\n" + "="*80)
print("DECISION POINT")
print("="*80)
print(f"\nCurrent DB has: {existing_count:,} Coinbase Prime transactions")
print(f"New import will add: {len(df):,} transactions")
print(f"\nUsing ECB historical rates: {df['usd_eur_rate'].min():.4f} to {df['usd_eur_rate'].max():.4f}")
print("\nOptions:")
print("1. DELETE existing Coinbase Prime data and import new (RECOMMENDED)")
print("2. APPEND new data (keep existing)")
print("3. Cancel (no changes)")

choice = input("\nEnter choice (1, 2, or 3): ").strip()

if choice == '1':
    print("\nDeleting existing Coinbase Prime data...")
    cursor.execute("DELETE FROM transactions WHERE exchange_name = 'Coinbase Prime'")
    deleted = cursor.rowcount
    print(f"  Deleted: {deleted:,} transactions")
elif choice != '2':
    print("\n✗ Aborted. No changes made.")
    conn.close()
    exit(0)

# Insert new data
print("\nInserting new data...")
inserted = 0

for _, row in df.iterrows():
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
        'Coinbase Prime',
        'BTC',
        row['amount'],
        row['price_per_unit'],
        row['total_value'],
        row['fee_amount'],
        'EUR',
        'EUR',
        row['order id'],
        f"USD: ${row['price_usd']:.2f}/BTC, Total: ${row['total_usd']:.2f}, Fee: ${row['fee_usd']:.2f}, ECB Rate: {row['usd_eur_rate']:.4f}"
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
    WHERE exchange_name = 'Coinbase Prime'
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
    
ecb.print_summary()
conn.close()

print("\n" + "="*80)
print("SUCCESS!")
print("="*80)
print("\n✓ Coinbase Prime data imported with ECB historical USD/EUR rates")
print(f"✓ Rates ranged from {df['usd_eur_rate'].min():.4f} to {df['usd_eur_rate'].max():.4f}")
print("\nNext steps:")
print("  1. python3 reports/verify_exchange_import.py 'Coinbase Prime'")
print("  2. python3 calculators/calculate_fifo.py (recalculate with accurate rates)")
print("\n" + "="*80)


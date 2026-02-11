"""
Bitfinex Trade Importer with ECB Historical Rates
Handles BTC/USD and BTC/EUR trades with accurate fee conversion
"""

import pandas as pd
import sqlite3
from datetime import datetime
import pytz
from ecb_rates import ECBRates

from config import DATABASE_PATH
DB_PATH = DATABASE_PATH

BITFINEX_FILE = 'data/bitfinex_trades.csv'

print("="*80)
print("IMPORTING BITFINEX TRADES WITH ECB RATES")
print("="*80)

# Load ECB rates
ecb = ECBRates()

# Read CSV
df = pd.read_csv(BITFINEX_FILE)
print(f"\nLoaded {len(df):,} rows")

# Filter BTC trades
df_btc = df[df['PAIR'].isin(['BTC/EUR', 'BTC/USD'])].copy()
print(f"BTC trades (BTC/EUR + BTC/USD): {len(df_btc):,}")

# Parse datetime (DATE column contains both date and time)
df_btc['datetime'] = pd.to_datetime(df_btc['DATE'])

# Determine transaction type from AMOUNT sign
df_btc['transaction_type'] = df_btc['AMOUNT'].apply(lambda x: 'BUY' if x > 0 else 'SELL')
df_btc['amount'] = df_btc['AMOUNT'].abs()

# Extract price (already in correct currency - USD or EUR)
df_btc['price_usd_or_eur'] = df_btc['PRICE'].astype(float)

# Convert prices to EUR using ECB rates for USD pairs
print("\nConverting USD prices to EUR using ECB historical rates...")

def convert_price_to_eur(row):
    """Convert price to EUR - use ECB for USD pairs, keep EUR pairs as-is"""
    if row['PAIR'] == 'BTC/USD':
        return ecb.usd_to_eur(row['price_usd_or_eur'], row['datetime'])
    else:  # BTC/EUR
        return row['price_usd_or_eur']

df_btc['price_per_unit'] = df_btc.apply(convert_price_to_eur, axis=1)
df_btc['total_value'] = df_btc['amount'] * df_btc['price_per_unit']

# Get ECB rate for each transaction (for tracking)
df_btc['ecb_rate'] = df_btc.apply(
    lambda row: ecb.get_rate(row['datetime']) if row['PAIR'] == 'BTC/USD' else None, 
    axis=1
)

# Fee conversion
print("Converting fees to EUR...")

def convert_fee_to_eur(row):
    """Convert BTC fee to EUR using transaction price"""
    fee_btc = abs(row['FEE'])
    if fee_btc == 0:
        return 0.0
    # Fee in EUR = fee_btc * price_per_unit (already in EUR)
    return fee_btc * row['price_per_unit']

df_btc['fee_amount'] = df_btc.apply(convert_fee_to_eur, axis=1)

# Statistics
print(f"\nTransaction breakdown:")
print(df_btc.groupby(['PAIR', 'transaction_type']).size())

print(f"\nDate range: {df_btc['datetime'].min()} to {df_btc['datetime'].max()}")

usd_trades = df_btc[df_btc['PAIR'] == 'BTC/USD']
if len(usd_trades) > 0:
    print(f"\nECB rates used for USD trades:")
    print(f"  Range: {usd_trades['ecb_rate'].min():.4f} to {usd_trades['ecb_rate'].max():.4f}")
    print(f"  Mean: {usd_trades['ecb_rate'].mean():.4f}")

# Prepare for database
transactions_to_insert = []

for _, row in df_btc.iterrows():
    dt = row['datetime']
    if dt.tzinfo is None:
        dt = pytz.UTC.localize(dt)
    
    note = f"Pair: {row['PAIR']}, Price: {row['price_usd_or_eur']:.2f}"
    if row['ecb_rate']:
        note += f", ECB Rate: {row['ecb_rate']:.4f}"
    
    transactions_to_insert.append({
        'date': dt.isoformat(),
        'type': row['transaction_type'],
        'exchange': 'Bitfinex',
        'crypto': 'BTC',
        'amount': row['amount'],
        'price': row['price_per_unit'],
        'total': row['total_value'],
        'fee': row['fee_amount'],
        'fee_currency': 'EUR',
        'currency': 'EUR',
        'tx_id': row['#'],
        'notes': note
    })

print(f"\nPrepared {len(transactions_to_insert):,} transactions")

# Sample
print("\n" + "="*80)
print("SAMPLE TRANSACTIONS (first 5):")
print("="*80)

for i, tx in enumerate(transactions_to_insert[:5], 1):
    print(f"\n{i}. {tx['type']} on {tx['date'][:19]}")
    print(f"   Amount: {tx['amount']:.8f} BTC")
    print(f"   Price:  €{tx['price']:.2f}/BTC")
    print(f"   Total:  €{tx['total']:.2f}")
    print(f"   Fee:    €{tx['fee']:.2f}")
    print(f"   Note:   {tx['notes']}")

# Database operations
conn = sqlite3.connect(DB_PATH)
cursor = conn.cursor()

# Check existing
cursor.execute("""
    SELECT COUNT(*) FROM transactions
    WHERE exchange_name = 'Bitfinex'
    AND cryptocurrency = 'BTC'
""")
existing_count = cursor.fetchone()[0]

print("\n" + "="*80)
print("DECISION POINT")
print("="*80)
print(f"\nCurrent DB has: {existing_count:,} Bitfinex BTC transactions")
print(f"New import will add: {len(transactions_to_insert):,} transactions")

if len(usd_trades) > 0:
    print(f"\nUsing ECB rates: {usd_trades['ecb_rate'].min():.4f} to {usd_trades['ecb_rate'].max():.4f}")

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

# Insert
print("\nInserting transactions...")
inserted = 0

for tx in transactions_to_insert:
    cursor.execute("""
        INSERT INTO transactions (
            transaction_date, transaction_type, exchange_name, cryptocurrency,
            amount, price_per_unit, total_value, fee_amount, fee_currency, currency,
            transaction_id, notes
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        tx['date'],
        tx['type'],
        tx['exchange'],
        tx['crypto'],
        tx['amount'],
        tx['price'],
        tx['total'],
        tx['fee'],
        tx['fee_currency'],
        tx['currency'],
        tx['tx_id'],
        tx['notes']
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

ecb.print_summary()
conn.close()

print("\n" + "="*80)
print("SUCCESS!")
print("="*80)
print("\n✓ Bitfinex data imported with ECB historical USD/EUR rates")
if len(usd_trades) > 0:
    print(f"✓ {len(usd_trades)} USD trades converted using ECB rates")
print("\nNext steps:")
print("  1. python3 calculators/calculate_fifo.py (recalculate)")
print("\n" + "="*80)

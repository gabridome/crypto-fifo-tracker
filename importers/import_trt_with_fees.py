"""
TRT (TheRockTrading) Importer with Fee Handling

Format: Multi-line trades (3-4 lines per trade)
BUY:
  1. EUR, paid_commission (fee)
  2. BTC, acquired_currency_from_fund (BTC received)
  3. EUR, bought_currency_from_fund (EUR paid)

SELL:
  1. EUR, paid_commission (fee)
  2. BTC, released_currency_to_fund (BTC sold)
  3. EUR, sold_currency_to_fund (EUR received)

Values are in cents (EUR) and satoshi (BTC):
  - EUR: divide by 100
  - BTC: divide by 100,000,000
"""

import pandas as pd
import sqlite3
from datetime import datetime
import pytz
from collections import defaultdict

from config import DATABASE_PATH
DB_PATH = DATABASE_PATH

TRT_FILE = 'data/historical/trt.csv'

# Conversion factors
SATOSHI_TO_BTC = 100_000_000
CENTS_TO_EUR = 100

print("="*80)
print("IMPORTING TRT (TheRockTrading) WITH FEE HANDLING")
print("="*80)

# Read file
df = pd.read_csv(TRT_FILE)
print(f"\nLoaded {len(df):,} rows from {TRT_FILE}")

# Parse dates
df['Date'] = pd.to_datetime(df['Date'])

# Group by timestamp and description to reconstruct trades
print("\nGrouping multi-line trades by timestamp...")

trades = []

# Group by date and description
for (date, desc), group in df.groupby(['Date', 'Description']):
    
    # Skip if not a BTC/EUR trade
    if 'Trade Bitcoin with Euro' not in desc:
        continue
    
    # Extract components
    fee_eur = 0
    btc_amount = 0
    eur_amount = 0
    trade_type = None
    
    for _, row in group.iterrows():
        currency = row['Currency']
        price_cents = float(row['Price (cents)']) if pd.notna(row['Price (cents)']) else 0
        trans_type = row['Type']
        
        if trans_type == 'paid_commission' and currency == 'EUR':
            # Fee in EUR cents
            fee_eur += price_cents / CENTS_TO_EUR
        
        elif trans_type == 'acquired_currency_from_fund' and currency == 'BTC':
            # BTC received (BUY)
            btc_amount = price_cents / SATOSHI_TO_BTC
            trade_type = 'BUY'
        
        elif trans_type == 'bought_currency_from_fund' and currency == 'EUR':
            # EUR paid (BUY)
            eur_amount = price_cents / CENTS_TO_EUR
        
        elif trans_type == 'released_currency_to_fund' and currency == 'BTC':
            # BTC sold (SELL)
            btc_amount = price_cents / SATOSHI_TO_BTC
            trade_type = 'SELL'
        
        elif trans_type == 'sold_currency_to_fund' and currency == 'EUR':
            # EUR received (SELL)
            eur_amount = price_cents / CENTS_TO_EUR
    
    # Only add if we have a complete trade
    if trade_type and btc_amount > 0 and eur_amount > 0:
        trades.append({
            'date': date,
            'type': trade_type,
            'btc_amount': btc_amount,
            'eur_amount': eur_amount,
            'fee_eur': fee_eur
        })

print(f"Reconstructed {len(trades):,} complete BTC/EUR trades")

# Separate by type
trades_buy = [t for t in trades if t['type'] == 'BUY']
trades_sell = [t for t in trades if t['type'] == 'SELL']

print(f"  BUY:  {len(trades_buy):,} trades")
print(f"  SELL: {len(trades_sell):,} trades")

# Statistics
total_btc_buy = sum(t['btc_amount'] for t in trades_buy)
total_btc_sell = sum(t['btc_amount'] for t in trades_sell)
total_eur_buy = sum(t['eur_amount'] for t in trades_buy)
total_eur_sell = sum(t['eur_amount'] for t in trades_sell)
total_fees = sum(t['fee_eur'] for t in trades)

print(f"\nBTC purchased: {total_btc_buy:.8f}")
print(f"BTC sold: {total_btc_sell:.8f}")
print(f"EUR spent: €{total_eur_buy:,.2f}")
print(f"EUR received: €{total_eur_sell:,.2f}")
print(f"Total fees: €{total_fees:.2f}")

# Connect to database
conn = sqlite3.connect(DB_PATH)
cursor = conn.cursor()

# Check current TRT data
cursor.execute("""
    SELECT COUNT(*), MIN(transaction_date), MAX(transaction_date)
    FROM transactions
    WHERE exchange_name = 'TRT'
    AND cryptocurrency = 'BTC'
""")
current_data = cursor.fetchone()
print(f"\nCurrent TRT BTC data in DB:")
print(f"  Transactions: {current_data[0]:,}")
if current_data[1]:
    print(f"  Date range: {current_data[1]} to {current_data[2]}")

# Prepare transactions
transactions_to_insert = []

for trade in trades:
    # Parse date with timezone
    dt = trade['date']
    if dt.tzinfo is None:
        dt = pytz.UTC.localize(dt.to_pydatetime())
    else:
        dt = dt.to_pydatetime()
    
    # Calculate price per unit
    if trade['btc_amount'] > 0:
        price_per_unit = trade['eur_amount'] / trade['btc_amount']
    else:
        price_per_unit = 0
    
    transactions_to_insert.append({
        'date': dt.isoformat(),
        'type': trade['type'],
        'exchange': 'TRT',
        'crypto': 'BTC',
        'amount': trade['btc_amount'],
        'price': price_per_unit,
        'total': trade['eur_amount'],
        'fee': trade['fee_eur']
    })

print(f"\n\nPrepared {len(transactions_to_insert):,} transactions for import")

# Show sample
print("\n" + "="*80)
print("SAMPLE TRANSACTIONS (first 5):")
print("="*80)

for i, tx in enumerate(transactions_to_insert[:5]):
    print(f"\n{i+1}. {tx['type']} on {tx['date'][:10]}")
    print(f"   Amount: {tx['amount']:.8f} BTC")
    print(f"   Price:  €{tx['price']:.2f}/BTC")
    print(f"   Total:  €{tx['total']:.2f}")
    print(f"   Fee:    €{tx['fee']:.4f}")

# Ask for confirmation
print("\n" + "="*80)
print("DECISION POINT")
print("="*80)
print(f"\nCurrent DB has: {current_data[0]:,} TRT BTC transactions")
print(f"New import will add: {len(transactions_to_insert):,} transactions")
print("\nOptions:")
print("1. DELETE existing TRT data and import new (RECOMMENDED)")
print("2. APPEND new data (keep existing)")
print("3. Cancel (no changes)")

choice = input("\nEnter choice (1, 2, or 3): ").strip()

if choice == '1':
    # Delete existing
    print("\nDeleting existing TRT data...")
    cursor.execute("""
        DELETE FROM transactions
        WHERE exchange_name = 'TRT'
        AND cryptocurrency = 'BTC'
    """)
    deleted = cursor.rowcount
    print(f"  Deleted: {deleted:,} transactions")
elif choice != '2':
    print("\n✗ Aborted. No changes made.")
    conn.close()
    exit(0)

# Insert new data
print("\nInserting new data...")
inserted = 0
for tx in transactions_to_insert:
    cursor.execute("""
        INSERT INTO transactions (
            transaction_date, transaction_type, exchange_name, cryptocurrency,
            amount, price_per_unit, total_value, fee_amount, currency
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        tx['date'], tx['type'], tx['exchange'], tx['crypto'],
        tx['amount'], tx['price'], tx['total'], tx['fee'], 'EUR'
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
        SUM(fee_amount) as total_fees_eur
    FROM transactions
    WHERE exchange_name = 'TRT'
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
print("\n✓ TRT data imported with fee handling")
print("\nKey points:")
print("  - Multi-line trades grouped by timestamp")
print("  - Values converted: satoshi→BTC, cents→EUR")
print("  - Fees extracted and stored separately")

print("\n" + "="*80)

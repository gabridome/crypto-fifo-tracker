"""
Coinbase Standalone Importer with Fee Handling
Extracts fees from 'Fees and/or Spread' column
"""

import pandas as pd
import sqlite3
from datetime import datetime
import pytz

from config import DATABASE_PATH
DB_PATH = DATABASE_PATH

COINBASE_FILE = 'data/2024/coinbase_history.csv'

print("="*80)
print("IMPORTING COINBASE WITH FEE HANDLING")
print("="*80)

# Read file
df = pd.read_csv(COINBASE_FILE)
print(f"\nLoaded {len(df):,} rows from {COINBASE_FILE}")

# Deduplicate by ID
original_count = len(df)
df = df.drop_duplicates(subset=['ID'])
print(f"After deduplication: {len(df):,} rows ({original_count - len(df):,} duplicates removed)")

# Parse dates
df['Timestamp'] = pd.to_datetime(df['Timestamp'], format='%Y-%m-%d %H:%M:%S %Z', utc=True)

# Filter BTC only
df_btc = df[df['Asset'] == 'BTC'].copy()
print(f"BTC transactions: {len(df_btc):,}")

# Process each row
transactions_to_insert = []

for _, row in df_btc.iterrows():
    trans_type = str(row['Transaction Type'])
    quantity = float(row['Quantity Transacted'])
    
    if quantity == 0:
        continue
    
    # Parse values
    try:
        price_at_trans = float(str(row['Price at Transaction']).replace('€', '').replace(',', '')) if pd.notna(row['Price at Transaction']) and row['Price at Transaction'] != '' else 0
    except:
        price_at_trans = 0
    
    try:
        subtotal = float(str(row['Subtotal']).replace('€', '').replace(',', '').replace('-', '')) if pd.notna(row['Subtotal']) and row['Subtotal'] != '' else 0
    except:
        subtotal = 0
    
    # EXTRACT FEE from 'Fees and/or Spread'
    fee_str = str(row.get('Fees and/or Spread', ''))
    fee_amount = 0
    if fee_str and fee_str != 'nan' and fee_str != '':
        try:
            fee_amount = float(fee_str.replace('€', '').replace(',', '').replace('-', ''))
        except:
            fee_amount = 0
    
    # Determine transaction type
    trans_type_lower = trans_type.lower()
    if 'buy' in trans_type_lower:
        transaction_type = 'BUY'
    elif 'sell' in trans_type_lower:
        transaction_type = 'SELL'
    elif 'send' in trans_type_lower:
        transaction_type = 'WITHDRAWAL'
    elif 'receive' in trans_type_lower:
        transaction_type = 'DEPOSIT'
    else:
        transaction_type = 'OTHER'
    
    transactions_to_insert.append({
        'date': row['Timestamp'].isoformat(),
        'type': transaction_type,
        'exchange': 'Coinbase',
        'crypto': 'BTC',
        'amount': abs(quantity),
        'price': price_at_trans,
        'total': subtotal,
        'fee': fee_amount,
        'id': str(row['ID'])
    })

print(f"\nPrepared {len(transactions_to_insert):,} transactions")

# Statistics
buys = [t for t in transactions_to_insert if t['type'] == 'BUY']
sells = [t for t in transactions_to_insert if t['type'] == 'SELL']
total_fees = sum(t['fee'] for t in transactions_to_insert)

print(f"  BUY:  {len(buys):,}")
print(f"  SELL: {len(sells):,}")
print(f"  Total fees: €{total_fees:,.2f}")

# Connect to database
conn = sqlite3.connect(DB_PATH)
cursor = conn.cursor()

# Check current Coinbase data
cursor.execute("""
    SELECT COUNT(*), MIN(transaction_date), MAX(transaction_date)
    FROM transactions
    WHERE exchange_name = 'Coinbase'
    AND cryptocurrency = 'BTC'
""")
current_data = cursor.fetchone()
print(f"\nCurrent Coinbase BTC data in DB:")
print(f"  Transactions: {current_data[0]:,}")
if current_data[1]:
    print(f"  Date range: {current_data[1]} to {current_data[2]}")

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
print(f"\nCurrent DB has: {current_data[0]:,} Coinbase BTC transactions")
print(f"New import will add: {len(transactions_to_insert):,} transactions")
print("\nOptions:")
print("1. DELETE existing Coinbase data and import new (RECOMMENDED)")
print("2. APPEND new data (keep existing)")
print("3. Cancel (no changes)")

choice = input("\nEnter choice (1, 2, or 3): ").strip()

if choice == '1':
    # Delete existing
    print("\nDeleting existing Coinbase data...")
    cursor.execute("""
        DELETE FROM transactions
        WHERE exchange_name = 'Coinbase'
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
    WHERE exchange_name = 'Coinbase'
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
print("\n✓ Coinbase data imported with fee handling")
print("\nNext step:")
print("  python3 verify_exchange_import.py Coinbase")

print("\n" + "="*80)

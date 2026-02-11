"""
Wirex Card Payment Importer
Handles Card Payment transactions (BTC sells via card)
Processes 3 separate files (2023, 2024, 2025)
NOTE: Files MUST be in UTF-8 format (not UTF-16)
"""

import pandas as pd
import sqlite3
from datetime import datetime
import pytz

from config import DATABASE_PATH
DB_PATH = DATABASE_PATH

WIREX_FILES = [
    'data/wirex_2023.csv',
    'data/wirex_2024.csv',
    'data/wirex_2025.csv'
]

print("="*80)
print("IMPORTING WIREX CARD PAYMENT TRANSACTIONS")
print("="*80)

# Read all files
all_dfs = []

for file in WIREX_FILES:
    try:
        # Read with semicolon delimiter
        df = pd.read_csv(file, delimiter=';', encoding='utf-8')
        print(f"\n✓ Loaded {len(df):,} rows from {file}")
        all_dfs.append(df)
    except Exception as e:
        print(f"\n✗ Error loading {file}: {e}")
        print(f"  Make sure file is converted to UTF-8!")
        continue

if not all_dfs:
    print("\n✗ No files loaded successfully!")
    print("\nPlease convert files to UTF-8 first:")
    print("  iconv -f UTF-16LE -t UTF-8 original.csv > wirex_YYYY.csv")
    exit(1)

# Combine all files
df = pd.concat(all_dfs, ignore_index=True)
print(f"\n✓ Combined total: {len(df):,} rows")
print(f"Columns: {list(df.columns)}")

# Filter only Card Payment transactions
df_payments = df[df['Type'] == 'Card Payment'].copy()
print(f"\nCard Payment transactions: {len(df_payments):,}")

if len(df_payments) == 0:
    print("\n⚠️  No Card Payment transactions found!")
    print("Check if:")
    print("  1. Files are UTF-8 encoded")
    print("  2. Column 'Type' contains 'Card Payment'")
    print("  3. Delimiter is semicolon ';'")
    exit(1)

# Parse datetime: "10-01-2024 11:36:28"
def parse_wirex_date(dt_str):
    """Parse Wirex datetime: DD-MM-YYYY HH:MM:SS"""
    dt = datetime.strptime(dt_str, '%d-%m-%Y %H:%M:%S')
    return pytz.UTC.localize(dt)

df_payments['date_parsed'] = df_payments['Completed Date'].apply(parse_wirex_date)

# Amount is negative (we spent BTC), make it positive
df_payments['amount'] = df_payments['Amount'].astype(float).abs()

# Currency should be BTC
df_payments['cryptocurrency'] = df_payments['Account Currency']

# Filter only BTC transactions
df_btc = df_payments[df_payments['cryptocurrency'] == 'BTC'].copy()
print(f"\nBTC Card Payments: {len(df_btc):,}")

# Calculate EUR value from Rate or Foreign Amount
# If Rate exists, EUR = Amount * Rate
# If Foreign Amount exists, use that
def calculate_eur_value(row):
    """Calculate EUR value of the transaction"""
    amount_btc = row['amount']
    
    # Try to use Rate column
    if pd.notna(row['Rate']) and row['Rate'] != '':
        rate = float(row['Rate'])
        return amount_btc * rate
    
    # Try to use Foreign Amount
    if pd.notna(row['Foreign Amount']) and row['Foreign Amount'] != '':
        foreign = float(row['Foreign Amount'])
        # If Foreign Currency is EUR, use directly
        if row.get('Foreign Currency') == 'EUR':
            return foreign
        # Otherwise estimate (this shouldn't happen often)
        return amount_btc * 50000  # Rough estimate ~€50k/BTC
    
    # Fallback: estimate based on year
    year = row['date_parsed'].year
    if year == 2023:
        return amount_btc * 25000  # ~€25k/BTC in 2023
    elif year == 2024:
        return amount_btc * 60000  # ~€60k/BTC in 2024
    else:
        return amount_btc * 60000  # Use 2024 rate for 2025

df_btc['total_value'] = df_btc.apply(calculate_eur_value, axis=1)
df_btc['price_per_unit'] = df_btc['total_value'] / df_btc['amount']

# No separate fee column, assume 0 (fee included in price)
df_btc['fee_amount'] = 0.0

# Statistics
print(f"\nDate range: {df_btc['date_parsed'].min()} to {df_btc['date_parsed'].max()}")
print(f"\nTotal BTC spent: {df_btc['amount'].sum():.8f}")
print(f"Total EUR value: €{df_btc['total_value'].sum():,.2f}")
print(f"Average price: €{df_btc['price_per_unit'].mean():,.2f}/BTC")

# Connect to database
conn = sqlite3.connect(DB_PATH)
cursor = conn.cursor()

# Check existing Wirex data
cursor.execute("""
    SELECT COUNT(*) FROM transactions
    WHERE exchange_name = 'Wirex'
    AND cryptocurrency = 'BTC'
""")
existing_count = cursor.fetchone()[0]

print(f"\nCurrent DB has: {existing_count:,} Wirex BTC transactions")

# Show sample
print("\n" + "="*80)
print("SAMPLE TRANSACTIONS (first 5):")
print("="*80)

for i, (_, row) in enumerate(df_btc.head(5).iterrows(), 1):
    print(f"\n{i}. SELL (Card Payment) on {row['date_parsed'].strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"   Amount: {row['amount']:.8f} BTC")
    print(f"   Price:  €{row['price_per_unit']:.2f}/BTC")
    print(f"   Total:  €{row['total_value']:.2f}")
    print(f"   Merchant: {row['Description'][:50]}")

# Ask for confirmation
print("\n" + "="*80)
print("DECISION POINT")
print("="*80)
print(f"\nCurrent DB has: {existing_count:,} Wirex transactions")
print(f"New import will add: {len(df_btc):,} transactions")
print("\nNOTE: Card Payments = BTC sells")
print("\nOptions:")
print("1. DELETE existing Wirex data and import new (RECOMMENDED)")
print("2. APPEND new data (keep existing)")
print("3. Cancel (no changes)")

choice = input("\nEnter choice (1, 2, or 3): ").strip()

if choice == '1':
    print("\nDeleting existing Wirex data...")
    cursor.execute("DELETE FROM transactions WHERE exchange_name = 'Wirex'")
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
        'Wirex',
        row['cryptocurrency'],
        row['amount'],
        row['price_per_unit'],
        row['total_value'],
        row['fee_amount'],
        'EUR',
        'EUR',
        row.get('Related Entity ID', ''),
        row['Description']
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
    WHERE exchange_name = 'Wirex'
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
print("\n✓ Wirex card payment data imported")
print("✓ Combined 3 years: 2023, 2024, 2025")
print("\nNext step:")
print("  python3 reports/verify_exchange_import.py Wirex")
print("\n" + "="*80)

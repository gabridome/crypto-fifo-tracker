"""
Revolut Crypto Statement Importer
Handles Buy and Sell transactions with EUR values
"""

import pandas as pd
import sqlite3
from datetime import datetime
import pytz

from config import DATABASE_PATH
DB_PATH = DATABASE_PATH

REVOLUT_FILE = 'data/revolut_crypto.csv'

print("="*80)
print("IMPORTING REVOLUT CRYPTO TRANSACTIONS")
print("="*80)

# Read CSV
df = pd.read_csv(REVOLUT_FILE)
print(f"\nLoaded {len(df):,} rows from {REVOLUT_FILE}")
print(f"Columns: {list(df.columns)}")

# Parse date: "Jun 7, 2018, 9:10:51 AM" format
def parse_revolut_date(date_str):
    """Parse Revolut date format to datetime"""
    import re
    # Remove extra spaces, Unicode non-breaking spaces, and standardize
    date_str = date_str.strip()
    # Remove ALL Unicode whitespace variants and normalize to single space
    date_str = re.sub(r'[\u00A0\u202F\u2009\u200A\xa0]+', ' ', date_str)
    # Also remove any weird characters before AM/PM
    date_str = re.sub(r'[^\x00-\x7F]+', ' ', date_str)  # Remove non-ASCII
    date_str = re.sub(r'\s+', ' ', date_str)  # Normalize multiple spaces
    
    # Try multiple formats
    formats = [
        "%b %d, %Y, %I:%M:%S %p",   # Jun 7, 2018, 9:10:51 AM
        "%b %d, %Y %I:%M:%S %p",    # Jun 7, 2018 9:10:51 AM (no comma after year)
    ]
    
    for fmt in formats:
        try:
            dt = datetime.strptime(date_str, fmt)
            return pytz.UTC.localize(dt)
        except ValueError:
            continue
    
    # If all fail, raise error with cleaned string
    raise ValueError(f"Could not parse date: '{date_str}'")

# Parse EUR values: "€6,622.93" → 6622.93
def parse_eur_value(value_str):
    """Parse EUR value with € symbol and comma separator"""
    import re
    if pd.isna(value_str):
        return 0
    # Remove € symbol (both correct and corrupted versions), keep only digits, comma, dot
    value_str = str(value_str)
    value_str = re.sub(r'[^\d,.]', '', value_str)  # Keep only digits, comma, dot
    value_str = value_str.replace(',', '')  # Remove comma (thousands separator)
    try:
        return float(value_str)
    except:
        return 0

# Process data
df['date_parsed'] = df['Date'].apply(parse_revolut_date)
df['transaction_type'] = df['Type'].map({'Buy': 'BUY', 'Sell': 'SELL'})
df['cryptocurrency'] = df['Symbol']

# Filter only BTC (and valid transaction types)
df = df[df['cryptocurrency'] == 'BTC'].copy()
df = df[df['transaction_type'].notna()].copy()  # Remove rows with no valid type

print(f"\nBTC transactions with valid types: {len(df)}")

df['amount'] = df['Quantity'].astype(float)
df['price_per_unit'] = df['Price'].apply(parse_eur_value)
df['total_value'] = df['Value'].apply(parse_eur_value)
df['fee_amount'] = df['Fees'].apply(parse_eur_value)

print(f"\nTransactions by type:")
print(df['transaction_type'].value_counts())

print(f"\nDate range: {df['date_parsed'].min()} to {df['date_parsed'].max()}")

print(f"\nTotal fees: €{df['fee_amount'].sum():.2f}")

# Connect to database
conn = sqlite3.connect(DB_PATH)
cursor = conn.cursor()

# Check existing Revolut data
cursor.execute("""
    SELECT COUNT(*) FROM transactions
    WHERE exchange_name = 'Revolut'
    AND cryptocurrency = 'BTC'
""")
existing_count = cursor.fetchone()[0]

print(f"\nCurrent DB has: {existing_count:,} Revolut BTC transactions")

# Show sample
print("\n" + "="*80)
print("SAMPLE TRANSACTIONS (first 3):")
print("="*80)

for i, row in df.head(3).iterrows():
    print(f"\n{i+1}. {row['transaction_type']} on {row['date_parsed'].strftime('%Y-%m-%d')}")
    print(f"   Amount: {row['amount']:.8f} {row['cryptocurrency']}")
    print(f"   Price:  €{row['price_per_unit']:.2f}")
    print(f"   Total:  €{row['total_value']:.2f}")
    print(f"   Fee:    €{row['fee_amount']:.2f}")

# Ask for confirmation
print("\n" + "="*80)
print("DECISION POINT")
print("="*80)
print(f"\nCurrent DB has: {existing_count:,} Revolut transactions")
print(f"New import will add: {len(df):,} transactions")
print("\nOptions:")
print("1. DELETE existing Revolut data and import new (RECOMMENDED)")
print("2. APPEND new data (keep existing)")
print("3. Cancel (no changes)")

choice = input("\nEnter choice (1, 2, or 3): ").strip()

if choice == '1':
    print("\nDeleting existing Revolut data...")
    cursor.execute("DELETE FROM transactions WHERE exchange_name = 'Revolut'")
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
    cursor.execute("""
        INSERT INTO transactions (
            transaction_date, transaction_type, exchange_name, cryptocurrency,
            amount, price_per_unit, total_value, fee_amount, fee_currency, currency
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        row['date_parsed'].isoformat(),
        row['transaction_type'],
        'Revolut',
        row['cryptocurrency'],
        row['amount'],
        row['price_per_unit'],
        row['total_value'],
        row['fee_amount'],
        'EUR',
        'EUR'
    ))
    inserted += 1

conn.commit()
print(f"  Inserted: {inserted:,} transactions")

# Verify
cursor.execute("""
    SELECT 
        transaction_type,
        cryptocurrency,
        COUNT(*) as count,
        SUM(amount) as total_amount,
        SUM(total_value) as total_eur,
        SUM(fee_amount) as total_fees
    FROM transactions
    WHERE exchange_name = 'Revolut'
    GROUP BY transaction_type, cryptocurrency
""")

print("\n" + "="*80)
print("VERIFICATION")
print("="*80)

for row in cursor.fetchall():
    tx_type, crypto, count, amount, eur, fees = row
    print(f"\n{crypto} {tx_type}:")
    print(f"  Transactions: {count:,}")
    print(f"  Amount: {amount:.8f}")
    print(f"  EUR: €{eur:,.2f}")
    print(f"  Fees: €{fees:.2f}")

conn.close()

print("\n" + "="*80)
print("SUCCESS!")
print("="*80)
print("\n✓ Revolut data imported")
print("\nNext step:")
print("  python3 reports/verify_exchange_import.py Revolut")
print("\n" + "="*80)

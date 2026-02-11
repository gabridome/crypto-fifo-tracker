"""
Generic CSV Importer for Standard Format Transactions
Works with any CSV file matching the standard column format
"""

import pandas as pd
import sqlite3
import sys
from datetime import datetime
import pytz

from config import DATABASE_PATH
DB_PATH = DATABASE_PATH


def parse_numeric(value):
    """Parse numeric value, handling comma as thousands separator"""
    if pd.isna(value):
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    # Remove comma thousands separator, quotes
    value_str = str(value).replace(',', '').replace('"', '').strip()
    try:
        return float(value_str)
    except:
        return 0.0

def import_standard_csv(filepath, exchange_name_override=None):
    """
    Import transactions from a standard CSV file
    
    CSV must have these columns:
    - transaction_date (ISO format with timezone)
    - transaction_type (BUY, SELL, DEPOSIT, WITHDRAWAL)
    - cryptocurrency (BTC, USDC, etc)
    - amount (quantity)
    - price_per_unit (EUR per unit)
    - total_value (EUR total, before fees)
    - fee_amount (EUR)
    - fee_currency (EUR)
    - currency (EUR)
    - exchange_name (descriptive name)
    - transaction_id (unique ID)
    - notes (optional description)
    """
    
    print("="*80)
    print(f"IMPORTING STANDARD CSV: {filepath}")
    print("="*80)
    
    # Read CSV
    df = pd.read_csv(filepath)
    print(f"\nLoaded {len(df):,} rows")
    
    # Validate required columns
    required_cols = ['transaction_date', 'transaction_type', 'cryptocurrency', 
                     'amount', 'total_value', 'exchange_name']
    missing = [col for col in required_cols if col not in df.columns]
    if missing:
        print(f"✗ Missing required columns: {missing}")
        return 0
    
    # Parse dates
    df['transaction_date'] = pd.to_datetime(df['transaction_date'])
    
    # Get exchange name
    if exchange_name_override:
        exchange_name = exchange_name_override
    else:
        exchange_name = df['exchange_name'].iloc[0] if len(df) > 0 else 'Unknown'
    
    print(f"Exchange: {exchange_name}")
    print(f"Date range: {df['transaction_date'].min()} to {df['transaction_date'].max()}")
    
    # Statistics
    crypto_counts = df['cryptocurrency'].value_counts()
    print(f"\nCryptocurrencies:")
    for crypto, count in crypto_counts.items():
        print(f"  {crypto}: {count} transactions")
    
    type_counts = df['transaction_type'].value_counts()
    print(f"\nTransaction types:")
    for tx_type, count in type_counts.items():
        print(f"  {tx_type}: {count}")
    
    # Connect to database
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Check existing data
    cursor.execute("""
        SELECT COUNT(*) FROM transactions
        WHERE exchange_name = ?
    """, (exchange_name,))
    existing_count = cursor.fetchone()[0]
    
    print(f"\nCurrent DB has: {existing_count:,} transactions from {exchange_name}")
    
    # Ask for confirmation
    print("\n" + "="*80)
    print("DECISION POINT")
    print("="*80)
    print(f"\nOptions:")
    print(f"1. DELETE existing {exchange_name} data and import new (RECOMMENDED)")
    print(f"2. APPEND new data (keep existing)")
    print(f"3. Cancel (no changes)")
    
    choice = input("\nEnter choice (1, 2, or 3): ").strip()
    
    if choice == '1':
        print(f"\nDeleting existing {exchange_name} data...")
        cursor.execute("DELETE FROM transactions WHERE exchange_name = ?", (exchange_name,))
        deleted = cursor.rowcount
        print(f"  Deleted: {deleted:,} transactions")
    elif choice != '2':
        print("\n✗ Aborted. No changes made.")
        conn.close()
        return 0
    
    # Insert transactions
    print("\nInserting transactions...")
    inserted = 0
    
    for _, row in df.iterrows():
        # Parse date with timezone
        dt = row['transaction_date']
        if dt.tzinfo is None:
            dt = pytz.UTC.localize(dt)
        
        # Get values with defaults
        fee_amount = row.get('fee_amount', 0)
        fee_currency = row.get('fee_currency', 'EUR')
        price_per_unit = row.get('price_per_unit', 0)
        notes = row.get('notes', '')
        transaction_id = row.get('transaction_id', f"{exchange_name}_{dt.isoformat()}")
        
        cursor.execute("""
            INSERT INTO transactions (
                transaction_date, transaction_type, exchange_name, cryptocurrency,
                amount, price_per_unit, total_value, fee_amount, fee_currency,
                currency, transaction_id, notes
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            dt.isoformat(),
            row['transaction_type'],
            exchange_name,
            row['cryptocurrency'],
            parse_numeric(row['amount']),
            parse_numeric(price_per_unit) if pd.notna(price_per_unit) else 0,
            parse_numeric(row['total_value']),
            parse_numeric(fee_amount) if pd.notna(fee_amount) else 0,
            fee_currency if pd.notna(fee_currency) else 'EUR',
            row.get('currency', 'EUR'),
            transaction_id,
            notes if pd.notna(notes) else ''
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
            SUM(total_value) as total_value,
            SUM(fee_amount) as total_fees
        FROM transactions
        WHERE exchange_name = ?
        GROUP BY transaction_type, cryptocurrency
    """, (exchange_name,))
    
    print("\n" + "="*80)
    print("VERIFICATION")
    print("="*80)
    
    for row in cursor.fetchall():
        tx_type, crypto, count, amount, value, fees = row
        print(f"\n{crypto} {tx_type}:")
        print(f"  Transactions: {count:,}")
        print(f"  Amount: {amount:.8f}")
        print(f"  Value: €{value:,.2f}")
        print(f"  Fees: €{fees:,.2f}")
    
    conn.close()
    
    print("\n" + "="*80)
    print("SUCCESS!")
    print("="*80)
    print(f"\n✓ {exchange_name} data imported")
    
    return inserted

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 import_standard_csv.py <filepath> [exchange_name]")
        print("\nExample:")
        print("  python3 import_standard_csv.py data/historical/wirecard_2024.csv Wirecard")
        sys.exit(1)
    
    filepath = sys.argv[1]
    exchange_name = sys.argv[2] if len(sys.argv) > 2 else None
    
    import_standard_csv(filepath, exchange_name)

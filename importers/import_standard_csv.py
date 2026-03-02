"""
Generic CSV Importer for Standard Format Transactions
Works with any CSV file matching the standard column format.

Now with source tracking:
  - DELETE by source (file-level) instead of exchange (exchange-level)
  - Records source filename, import timestamp, and record hash
"""

import pandas as pd
import sqlite3
import sys
import os
from datetime import datetime
import pytz

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

try:
    from config import DATABASE_PATH
except ImportError:
    DATABASE_PATH = os.path.join(PROJECT_ROOT, 'data', 'crypto_fifo.db')

from importers.import_utils import compute_record_hash

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
    Import transactions from a standard CSV file.

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

    source_filename = os.path.basename(filepath)
    import_timestamp = datetime.now().isoformat()

    print("="*80)
    print(f"IMPORTING STANDARD CSV: {filepath}")
    print(f"Source: {source_filename}")
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

    # Check existing data — by SOURCE (file-level), not by exchange
    cursor.execute("""
        SELECT COUNT(*) FROM transactions
        WHERE source = ?
    """, (source_filename,))
    existing_by_source = cursor.fetchone()[0]

    cursor.execute("""
        SELECT COUNT(*) FROM transactions
        WHERE exchange_name = ?
    """, (exchange_name,))
    existing_by_exchange = cursor.fetchone()[0]

    print(f"\nCurrent DB has:")
    print(f"  {existing_by_source:,} transactions from source '{source_filename}'")
    print(f"  {existing_by_exchange:,} transactions total for exchange '{exchange_name}'")

    # Ask for confirmation
    print("\n" + "="*80)
    print("DECISION POINT")
    print("="*80)
    print(f"\nOptions:")
    print(f"1. DELETE records from '{source_filename}' and re-import (RECOMMENDED)")
    print(f"   → Only removes {existing_by_source:,} records from this file")
    print(f"   → Records from other files of {exchange_name} are preserved")
    print(f"2. APPEND new data (keep existing)")
    print(f"3. Cancel (no changes)")

    choice = input("\nEnter choice (1, 2, or 3): ").strip()

    if choice == '1':
        print(f"\nDeleting records from source '{source_filename}'...")
        cursor.execute("DELETE FROM transactions WHERE source = ?", (source_filename,))
        deleted = cursor.rowcount
        if deleted == 0 and existing_by_source == 0:
            # Fallback: this might be a first import, also clean by exchange for safety
            # (handles transition from old DELETE-by-exchange importers)
            cursor.execute("DELETE FROM transactions WHERE exchange_name = ? AND source IS NULL",
                         (exchange_name,))
            deleted = cursor.rowcount
            if deleted > 0:
                print(f"  Deleted: {deleted:,} legacy records (no source) for {exchange_name}")
        else:
            print(f"  Deleted: {deleted:,} transactions from '{source_filename}'")
    elif choice != '2':
        print("\n✗ Aborted. No changes made.")
        conn.close()
        return 0

    # Insert transactions
    print("\nInserting transactions...")
    inserted = 0
    dupes = 0

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

        amount_val = parse_numeric(row['amount'])
        total_val = parse_numeric(row['total_value'])
        fee_val = parse_numeric(fee_amount) if pd.notna(fee_amount) else 0
        price_val = parse_numeric(price_per_unit) if pd.notna(price_per_unit) else 0

        # Compute record hash
        record_hash = compute_record_hash(
            source_filename, dt.isoformat(), row['transaction_type'],
            exchange_name, row['cryptocurrency'],
            amount_val, total_val, fee_val
        )

        # Check for duplicate hash (dedup)
        cursor.execute("SELECT id FROM transactions WHERE record_hash = ?", (record_hash,))
        if cursor.fetchone():
            dupes += 1
            continue

        cursor.execute("""
            INSERT INTO transactions (
                transaction_date, transaction_type, exchange_name, cryptocurrency,
                amount, price_per_unit, total_value, fee_amount, fee_currency,
                currency, transaction_id, notes,
                source, imported_at, record_hash
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            dt.isoformat(),
            row['transaction_type'],
            exchange_name,
            row['cryptocurrency'],
            amount_val,
            price_val,
            total_val,
            fee_val,
            fee_currency if pd.notna(fee_currency) else 'EUR',
            row.get('currency', 'EUR'),
            transaction_id,
            notes if pd.notna(notes) else '',
            source_filename,
            import_timestamp,
            record_hash,
        ))
        inserted += 1

    conn.commit()
    print(f"  Inserted: {inserted:,} transactions")
    if dupes > 0:
        print(f"  Skipped:  {dupes:,} duplicates (hash already in DB)")

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
        WHERE source = ?
        GROUP BY transaction_type, cryptocurrency
    """, (source_filename,))

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
    print(f"\n✓ {exchange_name} data imported from '{source_filename}'")

    return inserted

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 import_standard_csv.py <filepath> [exchange_name]")
        print("\nExample:")
        print("  python3 import_standard_csv.py data/otc_2017.csv OTC")
        sys.exit(1)

    filepath = sys.argv[1]
    exchange_name = sys.argv[2] if len(sys.argv) > 2 else None

    import_standard_csv(filepath, exchange_name)

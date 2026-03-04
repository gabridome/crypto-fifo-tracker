"""
Coinbase Standalone Importer with Fee Handling
Extracts fees from 'Fees and/or Spread' column

Usage:
    python3 importers/import_coinbase_standalone.py <filepath> [exchange_name]
"""

import sys
import os
import pandas as pd
import sqlite3
from datetime import datetime
import pytz

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from importers.import_utils import compute_record_hash, delete_by_source
from config import DATABASE_PATH
DB_PATH = DATABASE_PATH


def import_coinbase(filepath, exchange_name='Coinbase'):
    """Import Coinbase transactions from a CSV file with source tracking."""

    source = os.path.basename(filepath)
    imported_at = datetime.now().isoformat()

    print("="*80)
    print("IMPORTING COINBASE WITH FEE HANDLING")
    print("="*80)
    print(f"  File:     {filepath}")
    print(f"  Source:   {source}")
    print(f"  Exchange: {exchange_name}")

    # Read file
    df = pd.read_csv(filepath)
    print(f"\nLoaded {len(df):,} rows from {filepath}")

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

        record_hash = compute_record_hash(
            source, row['Timestamp'].isoformat(), transaction_type,
            exchange_name, 'BTC', abs(quantity), subtotal, fee_amount
        )

        transactions_to_insert.append({
            'date': row['Timestamp'].isoformat(),
            'type': transaction_type,
            'exchange': exchange_name,
            'crypto': 'BTC',
            'amount': abs(quantity),
            'price': price_at_trans,
            'total': subtotal,
            'fee': fee_amount,
            'id': str(row['ID']),
            'record_hash': record_hash
        })

    print(f"\nPrepared {len(transactions_to_insert):,} transactions")

    # Statistics
    buys = [t for t in transactions_to_insert if t['type'] == 'BUY']
    sells = [t for t in transactions_to_insert if t['type'] == 'SELL']
    total_fees = sum(t['fee'] for t in transactions_to_insert)

    print(f"  BUY:  {len(buys):,}")
    print(f"  SELL: {len(sells):,}")
    print(f"  Total fees: EUR{total_fees:,.2f}")

    # Show sample
    print("\n" + "="*80)
    print("SAMPLE TRANSACTIONS (first 5):")
    print("="*80)

    for i, tx in enumerate(transactions_to_insert[:5]):
        print(f"\n{i+1}. {tx['type']} on {tx['date'][:10]}")
        print(f"   Amount: {tx['amount']:.8f} BTC")
        print(f"   Price:  EUR{tx['price']:.2f}/BTC")
        print(f"   Total:  EUR{tx['total']:.2f}")
        print(f"   Fee:    EUR{tx['fee']:.4f}")

    # Connect to database
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # Delete by source
    deleted = delete_by_source(conn, source)
    print(f"\n  Deleted {deleted} previous records for {source}")

    # Insert new data
    print("\nInserting new data...")
    inserted = 0
    for tx in transactions_to_insert:
        cursor.execute("""
            INSERT INTO transactions (
                transaction_date, transaction_type, exchange_name, cryptocurrency,
                amount, price_per_unit, total_value, fee_amount, currency,
                transaction_id, source, imported_at, record_hash
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            tx['date'], tx['type'], tx['exchange'], tx['crypto'],
            tx['amount'], tx['price'], tx['total'], tx['fee'], 'EUR',
            tx['id'], source, imported_at, tx['record_hash']
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
            SUM(fee_amount) as total_fees_eur
        FROM transactions
        WHERE exchange_name = ?
        AND cryptocurrency = 'BTC'
        GROUP BY transaction_type
    """, (exchange_name,))

    print("\n" + "="*80)
    print("VERIFICATION")
    print("="*80)

    for row in cursor.fetchall():
        tx_type, count, btc, eur, fees = row
        print(f"\n{tx_type}:")
        print(f"  Transactions: {count:,}")
        print(f"  BTC: {btc:.8f}")
        print(f"  EUR: EUR{eur:,.2f}")
        print(f"  Fees: EUR{fees:,.2f}")

    conn.close()

    print("\n" + "="*80)
    print("SUCCESS!")
    print("="*80)
    print(f"\n  Coinbase data imported with fee handling")
    print(f"  Source: {source}")
    print(f"  Records: {inserted}")
    print("\n" + "="*80)

    return inserted


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print("Usage: python3 import_coinbase_standalone.py <filepath> [exchange_name]")
        sys.exit(1)
    filepath = sys.argv[1]
    exchange = sys.argv[2] if len(sys.argv) > 2 else 'Coinbase'
    import_coinbase(filepath, exchange)

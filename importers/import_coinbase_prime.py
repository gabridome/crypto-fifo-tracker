"""
Coinbase Prime Orders Importer
Handles BUY/SELL orders with USD to EUR conversion using ECB historical rates

Usage:
    python3 importers/import_coinbase_prime.py <filepath> [exchange_name]
"""

import sys
import os
import pandas as pd
import sqlite3
from datetime import datetime
import pytz

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from ecb_rates import ECBRates
from importers.import_utils import compute_record_hash, delete_by_source
from config import DATABASE_PATH
DB_PATH = DATABASE_PATH


def import_coinbase_prime(filepath, exchange_name='Coinbase Prime'):
    """Import Coinbase Prime orders from a CSV file with source tracking."""

    source = os.path.basename(filepath)
    imported_at = datetime.now().isoformat()

    print("="*80)
    print("IMPORTING COINBASE PRIME ORDERS WITH ECB RATES")
    print("="*80)
    print(f"  File:     {filepath}")
    print(f"  Source:   {source}")
    print(f"  Exchange: {exchange_name}")

    # Load ECB rates
    ecb = ECBRates()

    # Read CSV
    df = pd.read_csv(filepath)
    print(f"\nLoaded {len(df):,} rows from {filepath}")
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
        print(f"  {tx_type}: {btc:.8f} BTC, EUR{eur:,.2f}, fees: EUR{fee:.2f}")

    # Show sample
    print("\n" + "="*80)
    print("SAMPLE TRANSACTIONS (first 5):")
    print("="*80)

    for i, (_, row) in enumerate(df.head(5).iterrows(), 1):
        print(f"\n{i}. {row['transaction_type']} on {row['date_parsed'].strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"   Amount: {row['amount']:.8f} BTC")
        print(f"   Price:  EUR{row['price_per_unit']:.2f}/BTC (${row['price_usd']:.2f})")
        print(f"   Total:  EUR{row['total_value']:.2f}")
        print(f"   Fee:    EUR{row['fee_amount']:.2f}")
        print(f"   ID:     {row['order id']}")

    # Connect to database
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # Delete by source
    deleted = delete_by_source(conn, source)
    print(f"\n  Deleted {deleted} previous records for {source}")

    # Insert new data
    print("\nInserting new data...")
    inserted = 0

    for _, row in df.iterrows():
        dt = row['date_parsed']
        if dt.tzinfo is None:
            dt = pytz.UTC.localize(dt)

        notes = f"USD: ${row['price_usd']:.2f}/BTC, Total: ${row['total_usd']:.2f}, Fee: ${row['fee_usd']:.2f}, ECB Rate: {row['usd_eur_rate']:.4f}"

        record_hash = compute_record_hash(
            source, dt.isoformat(), row['transaction_type'],
            exchange_name, 'BTC', row['amount'], row['total_value'], row['fee_amount']
        )

        cursor.execute("""
            INSERT INTO transactions (
                transaction_date, transaction_type, exchange_name, cryptocurrency,
                amount, price_per_unit, total_value, fee_amount, fee_currency, currency,
                transaction_id, notes, source, imported_at, record_hash
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            dt.isoformat(),
            row['transaction_type'],
            exchange_name,
            'BTC',
            row['amount'],
            row['price_per_unit'],
            row['total_value'],
            row['fee_amount'],
            'EUR',
            'EUR',
            row['order id'],
            notes,
            source,
            imported_at,
            record_hash
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
        print(f"  Fees: EUR{fees:.2f}")

    ecb.print_summary()
    conn.close()

    print("\n" + "="*80)
    print("SUCCESS!")
    print("="*80)
    print(f"\n  Coinbase Prime data imported with ECB historical USD/EUR rates")
    print(f"  Source: {source}")
    print(f"  Records: {inserted}")
    print(f"  Rates ranged from {df['usd_eur_rate'].min():.4f} to {df['usd_eur_rate'].max():.4f}")
    print("\n" + "="*80)

    return inserted


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print("Usage: python3 import_coinbase_prime.py <filepath> [exchange_name]")
        sys.exit(1)
    filepath = sys.argv[1]
    exchange = sys.argv[2] if len(sys.argv) > 2 else 'Coinbase Prime'
    import_coinbase_prime(filepath, exchange)

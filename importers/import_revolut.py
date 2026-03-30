"""
Revolut Crypto Statement Importer
Handles Buy and Sell transactions with EUR values

Usage:
  python3 importers/import_revolut.py <filepath> [exchange_name]
"""

import sys
import os
import re
import pandas as pd
from datetime import datetime
import pytz

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from config import DATABASE_PATH
from importers.import_utils import compute_record_hash, import_and_verify

DB_PATH = DATABASE_PATH


# Parse date: "Jun 7, 2018, 9:10:51 AM" format
def parse_revolut_date(date_str):
    """Parse Revolut date format to datetime"""
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


# Parse EUR values: "EUR6,622.93" -> 6622.93
def parse_eur_value(value_str):
    """Parse EUR value with EUR symbol and comma separator"""
    if pd.isna(value_str):
        return 0
    # Remove EUR symbol (both correct and corrupted versions), keep only digits, comma, dot
    value_str = str(value_str)
    value_str = re.sub(r'[^\d,.]', '', value_str)  # Keep only digits, comma, dot
    value_str = value_str.replace(',', '')  # Remove comma (thousands separator)
    try:
        return float(value_str)
    except:
        return 0


def import_revolut(filepath, exchange_name='Revolut'):
    """Import Revolut crypto CSV with source tracking."""

    source = os.path.basename(filepath)
    imported_at = datetime.now().isoformat()

    print("=" * 80)
    print("IMPORTING REVOLUT CRYPTO TRANSACTIONS")
    print(f"  File:     {filepath}")
    print(f"  Exchange: {exchange_name}")
    print(f"  Source:   {source}")
    print("=" * 80)

    # Read CSV
    df = pd.read_csv(filepath)
    print(f"\nLoaded {len(df):,} rows from {filepath}")
    print(f"Columns: {list(df.columns)}")

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

    print(f"\nTotal fees: EUR{df['fee_amount'].sum():.2f}")

    # Show sample
    print("\n" + "=" * 80)
    print("SAMPLE TRANSACTIONS (first 3):")
    print("=" * 80)

    for i, row in df.head(3).iterrows():
        print(f"\n{i+1}. {row['transaction_type']} on {row['date_parsed'].strftime('%Y-%m-%d')}")
        print(f"   Amount: {row['amount']:.8f} {row['cryptocurrency']}")
        print(f"   Price:  EUR{row['price_per_unit']:.2f}")
        print(f"   Total:  EUR{row['total_value']:.2f}")
        print(f"   Fee:    EUR{row['fee_amount']:.2f}")

    # Insert function for import_and_verify
    def do_inserts(conn):
        cursor = conn.cursor()
        inserted = 0

        for _, row in df.iterrows():
            tx_date = row['date_parsed'].isoformat()
            tx_type = row['transaction_type']
            tx_crypto = row['cryptocurrency']
            tx_amount = row['amount']
            tx_price = row['price_per_unit']
            tx_total = row['total_value']
            tx_fee = row['fee_amount']

            record_hash = compute_record_hash(
                source, tx_date, tx_type, exchange_name,
                tx_crypto, tx_amount, tx_total, tx_fee
            )

            cursor.execute("""
                INSERT INTO transactions (
                    transaction_date, transaction_type, exchange_name, cryptocurrency,
                    amount, price_per_unit, total_value, fee_amount, fee_currency, currency,
                    source, imported_at, record_hash
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                tx_date,
                tx_type,
                exchange_name,
                tx_crypto,
                tx_amount,
                tx_price,
                tx_total,
                tx_fee,
                'EUR',
                'EUR',
                source,
                imported_at,
                record_hash
            ))
            inserted += 1

        return inserted

    return import_and_verify(DB_PATH, source, do_inserts, group_by_crypto=True)


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print("Usage: python3 importers/import_revolut.py <filepath> [exchange_name]")
        sys.exit(1)
    filepath = sys.argv[1]
    exchange = sys.argv[2] if len(sys.argv) > 2 else 'Revolut'
    import_revolut(filepath, exchange)

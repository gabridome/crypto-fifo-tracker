"""
Binance Card Transaction Importer
Handles card payment transactions (Sell + Send pairs)
Only imports Sell rows (ignores Send/Payment rows)
Accepts a single CSV file via CLI argument.
"""

import sys
import os
import pandas as pd
from datetime import datetime
import pytz

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from config import DATABASE_PATH
from importers.import_utils import compute_record_hash, import_and_verify


def import_binance_card(filepath, exchange_name='Binance Card'):
    """
    Import Binance Card transactions from a single CSV file.

    Args:
        filepath: path to the Binance Card CSV file
        exchange_name: exchange name for DB records (default 'Binance Card')
    """
    source = os.path.basename(filepath)
    imported_at = datetime.now().isoformat()

    print("=" * 80)
    print(f"IMPORTING BINANCE CARD TRANSACTIONS")
    print(f"  File:     {filepath}")
    print(f"  Source:   {source}")
    print(f"  Exchange: {exchange_name}")
    print("=" * 80)

    # Read CSV
    df = pd.read_csv(filepath)
    print(f"\nLoaded {len(df):,} rows from {filepath}")
    print(f"Columns: {list(df.columns)}")

    # Filter only Sell transactions (ignore Send/Payment rows)
    df_sell = df[df['type'] == 'Sell'].copy()
    print(f"\nSell transactions: {len(df_sell):,} (filtered from {len(df):,} total)")

    # Parse datetime with CET timezone
    def parse_cet_datetime(dt_str):
        """Parse datetime in CET timezone: '2023-03-09-13:26:06'"""
        # Replace first two hyphens with spaces for parsing
        parts = dt_str.split('-')
        if len(parts) >= 4:
            # Format: YYYY-MM-DD-HH:MM:SS
            dt_str_fixed = f"{parts[0]}-{parts[1]}-{parts[2]} {parts[3]}"
            dt = datetime.strptime(dt_str_fixed, '%Y-%m-%d %H:%M:%S')
            # CET is UTC+1, but we'll store as UTC for consistency
            cet = pytz.timezone('CET')
            dt_cet = cet.localize(dt)
            return dt_cet.astimezone(pytz.UTC)
        return pytz.UTC.localize(datetime.strptime(dt_str, '%Y-%m-%d %H:%M:%S'))

    df_sell['date_parsed'] = df_sell['datetime_tz_CET'].apply(parse_cet_datetime)
    df_sell['cryptocurrency'] = df_sell['sent_currency']
    df_sell['amount'] = df_sell['sent_amount'].astype(float)
    df_sell['eur_received'] = df_sell['received_amount'].astype(float)
    df_sell['price_per_unit'] = df_sell['eur_received'] / df_sell['amount']
    df_sell['total_value'] = df_sell['eur_received']  # EUR received BEFORE fee
    df_sell['fee_amount'] = df_sell['differenza'].astype(float)  # Fee is in differenza column

    # Statistics per crypto
    print(f"\nCryptocurrencies:")
    for crypto, grp in df_sell.groupby('cryptocurrency'):
        print(f"  {crypto}: {len(grp):,} sells, {grp['amount'].sum():.8f} {crypto}, "
              f"€{grp['total_value'].sum():,.2f}, fees €{grp['fee_amount'].sum():.2f}")
    print(f"\nDate range: {df_sell['date_parsed'].min()} to {df_sell['date_parsed'].max()}")
    print(f"Total EUR received: {df_sell['total_value'].sum():,.2f} EUR")
    print(f"Total fees: {df_sell['fee_amount'].sum():.2f} EUR")

    # Database insert function
    def do_inserts(conn):
        cursor = conn.cursor()
        inserted = 0

        for _, row in df_sell.iterrows():
            tx_date = row['date_parsed'].isoformat()
            tx_type = 'SELL'
            crypto = row['cryptocurrency']
            amount = row['amount']
            total_value = row['total_value']
            fee = row['fee_amount']

            record_hash = compute_record_hash(
                source, tx_date, tx_type, exchange_name,
                crypto, amount, total_value, fee
            )

            cursor.execute("""
                INSERT INTO transactions (
                    transaction_date, transaction_type, exchange_name, cryptocurrency,
                    amount, price_per_unit, total_value, fee_amount, fee_currency, currency,
                    transaction_id, notes,
                    source, imported_at, record_hash
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                tx_date,
                tx_type,
                exchange_name,
                crypto,
                amount,
                row['price_per_unit'],
                total_value,
                fee,
                'EUR',
                'EUR',
                row['id'],
                row.get('label', ''),
                source,
                imported_at,
                record_hash
            ))
            inserted += 1

        return inserted

    import_and_verify(DATABASE_PATH, source, do_inserts, group_by_crypto=True)


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print("Usage: python3 import_binance_card.py <filepath> [exchange_name]")
        print("  filepath:      path to Binance Card CSV file")
        print("  exchange_name: optional, default 'Binance Card'")
        sys.exit(1)
    filepath = sys.argv[1]
    exchange = sys.argv[2] if len(sys.argv) > 2 else 'Binance Card'
    import_binance_card(filepath, exchange)

"""
Bitfinex Trade Importer with ECB Historical Rates
Handles BTC/USD and BTC/EUR trades with accurate fee conversion

Usage:
    python3 importers/import_bitfinex_ecb.py <filepath> [exchange_name]
"""

import sys
import os
import pandas as pd
from datetime import datetime
import pytz

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from importers.ecb_rates import ECBRates
from importers.import_utils import compute_record_hash, delete_by_source, import_and_verify
from config import DATABASE_PATH


def import_bitfinex(filepath, exchange_name='Bitfinex'):
    """Import Bitfinex trades from a CSV file with source tracking."""

    source = os.path.basename(filepath)
    imported_at = datetime.now().isoformat()

    print("="*80)
    print("IMPORTING BITFINEX TRADES WITH ECB RATES")
    print("="*80)
    print(f"  File:     {filepath}")
    print(f"  Source:   {source}")
    print(f"  Exchange: {exchange_name}")

    # Load ECB rates
    ecb = ECBRates()

    # Read CSV
    df = pd.read_csv(filepath)
    print(f"\nLoaded {len(df):,} rows")

    # Filter BTC trades
    df_btc = df[df['PAIR'].isin(['BTC/EUR', 'BTC/USD'])].copy()
    print(f"BTC trades (BTC/EUR + BTC/USD): {len(df_btc):,}")

    # Parse datetime (DATE column contains both date and time)
    df_btc['datetime'] = pd.to_datetime(df_btc['DATE'])

    # Determine transaction type from AMOUNT sign
    df_btc['transaction_type'] = df_btc['AMOUNT'].apply(lambda x: 'BUY' if x > 0 else 'SELL')
    df_btc['amount'] = df_btc['AMOUNT'].abs()

    # Extract price (already in correct currency - USD or EUR)
    df_btc['price_usd_or_eur'] = df_btc['PRICE'].astype(float)

    # Convert prices to EUR using ECB rates for USD pairs
    print("\nConverting USD prices to EUR using ECB historical rates...")

    def convert_price_to_eur(row):
        """Convert price to EUR - use ECB for USD pairs, keep EUR pairs as-is"""
        if row['PAIR'] == 'BTC/USD':
            return ecb.usd_to_eur(row['price_usd_or_eur'], row['datetime'])
        else:  # BTC/EUR
            return row['price_usd_or_eur']

    df_btc['price_per_unit'] = df_btc.apply(convert_price_to_eur, axis=1)
    df_btc['total_value'] = df_btc['amount'] * df_btc['price_per_unit']

    # Get ECB rate for each transaction (for tracking)
    df_btc['ecb_rate'] = df_btc.apply(
        lambda row: ecb.get_rate(row['datetime']) if row['PAIR'] == 'BTC/USD' else None,
        axis=1
    )

    # Fee conversion
    print("Converting fees to EUR...")

    def convert_fee_to_eur(row):
        """Convert BTC fee to EUR using transaction price"""
        fee_btc = abs(row['FEE'])
        if fee_btc == 0:
            return 0.0
        # Fee in EUR = fee_btc * price_per_unit (already in EUR)
        return fee_btc * row['price_per_unit']

    df_btc['fee_amount'] = df_btc.apply(convert_fee_to_eur, axis=1)

    # Statistics
    print(f"\nTransaction breakdown:")
    print(df_btc.groupby(['PAIR', 'transaction_type']).size())

    print(f"\nDate range: {df_btc['datetime'].min()} to {df_btc['datetime'].max()}")

    usd_trades = df_btc[df_btc['PAIR'] == 'BTC/USD']
    if len(usd_trades) > 0:
        print(f"\nECB rates used for USD trades:")
        print(f"  Range: {usd_trades['ecb_rate'].min():.4f} to {usd_trades['ecb_rate'].max():.4f}")
        print(f"  Mean: {usd_trades['ecb_rate'].mean():.4f}")

    # Prepare for database
    transactions_to_insert = []

    for _, row in df_btc.iterrows():
        dt = row['datetime']
        if dt.tzinfo is None:
            dt = pytz.UTC.localize(dt)

        note = f"Pair: {row['PAIR']}, Price: {row['price_usd_or_eur']:.2f}"
        if row['ecb_rate']:
            note += f", ECB Rate: {row['ecb_rate']:.4f}"

        record_hash = compute_record_hash(
            source, dt.isoformat(), row['transaction_type'],
            exchange_name, 'BTC', row['amount'], row['total_value'], row['fee_amount']
        )

        transactions_to_insert.append({
            'date': dt.isoformat(),
            'type': row['transaction_type'],
            'exchange': exchange_name,
            'crypto': 'BTC',
            'amount': row['amount'],
            'price': row['price_per_unit'],
            'total': row['total_value'],
            'fee': row['fee_amount'],
            'fee_currency': 'EUR',
            'currency': 'EUR',
            'tx_id': row['#'],
            'notes': note,
            'record_hash': record_hash
        })

    print(f"\nPrepared {len(transactions_to_insert):,} transactions")

    # Sample
    print("\n" + "="*80)
    print("SAMPLE TRANSACTIONS (first 5):")
    print("="*80)

    for i, tx in enumerate(transactions_to_insert[:5], 1):
        print(f"\n{i}. {tx['type']} on {tx['date'][:19]}")
        print(f"   Amount: {tx['amount']:.8f} BTC")
        print(f"   Price:  EUR{tx['price']:.2f}/BTC")
        print(f"   Total:  EUR{tx['total']:.2f}")
        print(f"   Fee:    EUR{tx['fee']:.2f}")
        print(f"   Note:   {tx['notes']}")

    # Database insert function
    def do_inserts(conn):
        cursor = conn.cursor()
        inserted = 0

        for tx in transactions_to_insert:
            cursor.execute("""
                INSERT INTO transactions (
                    transaction_date, transaction_type, exchange_name, cryptocurrency,
                    amount, price_per_unit, total_value, fee_amount, fee_currency, currency,
                    transaction_id, notes, source, imported_at, record_hash
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                tx['date'],
                tx['type'],
                tx['exchange'],
                tx['crypto'],
                tx['amount'],
                tx['price'],
                tx['total'],
                tx['fee'],
                tx['fee_currency'],
                tx['currency'],
                tx['tx_id'],
                tx['notes'],
                source,
                imported_at,
                tx['record_hash']
            ))
            inserted += 1

        return inserted

    ecb.print_summary()
    return import_and_verify(DATABASE_PATH, source, do_inserts)


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print("Usage: python3 import_bitfinex_ecb.py <filepath> [exchange_name]")
        sys.exit(1)
    filepath = sys.argv[1]
    exchange = sys.argv[2] if len(sys.argv) > 2 else 'Bitfinex'
    import_bitfinex(filepath, exchange)

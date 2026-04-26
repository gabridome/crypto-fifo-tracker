"""
Wirex Card Payment Importer
Handles Card Payment transactions (BTC sells via card)
Accepts a single CSV file via CLI argument.
NOTE: Files MUST be in UTF-8 format (not UTF-16)

EUR values: uses daily BTC/EUR prices from CryptoCompare (data/crypto_prices.csv).
Fallback chain: Rate column → Foreign Amount → crypto_prices → yearly estimate.
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
from importers.crypto_prices import CryptoPrices


def import_wirex(filepath, exchange_name='Wirex'):
    """
    Import Wirex card payment transactions from a single CSV file.

    Args:
        filepath: path to the Wirex CSV file (semicolon-delimited, UTF-8)
        exchange_name: exchange name for DB records (default 'Wirex')
    """
    source = os.path.basename(filepath)
    imported_at = datetime.now().isoformat()

    print("=" * 80)
    print(f"IMPORTING WIREX CARD PAYMENT TRANSACTIONS")
    print(f"  File:     {filepath}")
    print(f"  Source:   {source}")
    print(f"  Exchange: {exchange_name}")
    print("=" * 80)

    # Read file with semicolon delimiter
    try:
        df = pd.read_csv(filepath, delimiter=';', encoding='utf-8')
        print(f"\nLoaded {len(df):,} rows from {filepath}")
    except Exception as e:
        print(f"\nError loading {filepath}: {e}")
        print(f"  Make sure file is converted to UTF-8!")
        print("  iconv -f UTF-16LE -t UTF-8 original.csv > wirex_YYYY.csv")
        sys.exit(1)

    print(f"Columns: {list(df.columns)}")

    # Filter only Card Payment transactions
    df_payments = df[df['Type'] == 'Card Payment'].copy()
    print(f"\nCard Payment transactions: {len(df_payments):,}")

    if len(df_payments) == 0:
        print("\nNo Card Payment transactions found!")
        print("Check if:")
        print("  1. Files are UTF-8 encoded")
        print("  2. Column 'Type' contains 'Card Payment'")
        print("  3. Delimiter is semicolon ';'")
        sys.exit(1)

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

    # Load crypto prices for EUR valuation
    prices_path = os.path.join(PROJECT_ROOT, 'data', 'crypto_prices.csv')
    crypto_prices = None
    if os.path.exists(prices_path):
        crypto_prices = CryptoPrices(prices_path)
    else:
        print(f"  ⚠️  {prices_path} not found — EUR values will use CSV Rate/Foreign Amount only")

    # Calculate EUR value from Rate or Foreign Amount or crypto prices
    def calculate_eur_value(row):
        """Calculate EUR value of the transaction.

        Fallback chain: Rate column → Foreign Amount → CryptoPrices daily → None (error).
        """
        amount_btc = row['amount']
        crypto = row['cryptocurrency']

        # Try to use Rate column (Wirex-provided exchange rate)
        if pd.notna(row.get('Rate')) and row['Rate'] != '':
            try:
                rate = float(row['Rate'])
                if rate > 0:
                    return amount_btc * rate
            except (ValueError, TypeError):
                pass

        # Try to use Foreign Amount (if in EUR)
        if pd.notna(row.get('Foreign Amount')) and row['Foreign Amount'] != '':
            try:
                foreign = abs(float(row['Foreign Amount']))
                if row.get('Foreign Currency') == 'EUR' and foreign > 0:
                    return foreign
            except (ValueError, TypeError):
                pass

        # Use CryptoPrices daily closing price
        if crypto_prices:
            eur_val = crypto_prices.crypto_to_eur(crypto, amount_btc, row['date_parsed'])
            if eur_val is not None:
                return eur_val

        print(f"    ⚠️  No EUR price for {crypto} on {row['date_parsed'].strftime('%Y-%m-%d')}")
        return 0.0

    df_btc['total_value'] = df_btc.apply(calculate_eur_value, axis=1)
    df_btc['price_per_unit'] = df_btc['total_value'] / df_btc['amount']

    # No separate fee column, assume 0 (fee included in price)
    df_btc['fee_amount'] = 0.0

    # Statistics
    print(f"\nDate range: {df_btc['date_parsed'].min()} to {df_btc['date_parsed'].max()}")
    print(f"\nTotal BTC spent: {df_btc['amount'].sum():.8f}")
    print(f"Total EUR value: {df_btc['total_value'].sum():,.2f} EUR")
    print(f"Average price: {df_btc['price_per_unit'].mean():,.2f} EUR/BTC")

    # Database insert function
    def do_inserts(conn):
        cursor = conn.cursor()
        inserted = 0

        for _, row in df_btc.iterrows():
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
                row.get('Related Entity ID', ''),
                row['Description'],
                source,
                imported_at,
                record_hash
            ))
            inserted += 1

        return inserted

    import_and_verify(DATABASE_PATH, source, do_inserts)


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print("Usage: python3 import_wirex.py <filepath> [exchange_name]")
        print("  filepath:      path to Wirex CSV file (semicolon-delimited, UTF-8)")
        print("  exchange_name: optional, default 'Wirex'")
        sys.exit(1)
    filepath = sys.argv[1]
    exchange = sys.argv[2] if len(sys.argv) > 2 else 'Wirex'
    import_wirex(filepath, exchange)

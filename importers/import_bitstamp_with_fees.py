"""
Bitstamp Importer with Fee Handling
- Fees in USD -> convert to EUR
- Sub Type: Buy/Sell

Usage:
    python3 importers/import_bitstamp_with_fees.py <filepath> [exchange_name]
"""

import sys
import os
import pandas as pd
from datetime import datetime
import pytz

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from importers.ecb_rates import ECBRates
from importers.import_utils import compute_record_hash, import_and_verify
from config import DATABASE_PATH
DB_PATH = DATABASE_PATH


def import_bitstamp(filepath, exchange_name='Bitstamp'):
    """Import Bitstamp transactions from a CSV file with source tracking."""

    source = os.path.basename(filepath)
    imported_at = datetime.now().isoformat()

    ecb = ECBRates()

    print("="*80)
    print("IMPORTING BITSTAMP WITH FEE HANDLING")
    print("="*80)
    print(f"  File:     {filepath}")
    print(f"  Source:   {source}")
    print(f"  Exchange: {exchange_name}")

    # Read file
    df = pd.read_csv(filepath)
    print(f"\nLoaded {len(df):,} rows from {filepath}")

    # Parse dates (format: "Nov. 18, 2012, 10:01 AM")
    df['Datetime'] = pd.to_datetime(df['Datetime'], format='%b. %d, %Y, %I:%M %p')

    # Filter only Market transactions (trades)
    df_trades = df[df['Type'] == 'Market'].copy()
    print(f"Market transactions: {len(df_trades):,}")

    # Parse amounts
    def parse_amount(value):
        """Parse '5.00000000 BTC' or '45.48 USD' to float"""
        if pd.isna(value) or value == '':
            return 0
        return float(str(value).split()[0])

    df_trades['btc_amount'] = df_trades['Amount'].apply(parse_amount)
    df_trades['usd_value'] = df_trades['Value'].apply(parse_amount)
    df_trades['usd_fee'] = df_trades['Fee'].apply(parse_amount)
    df_trades['usd_rate'] = df_trades['Rate'].apply(parse_amount)

    # Separate Buy and Sell
    df_buy = df_trades[df_trades['Sub Type'] == 'Buy'].copy()
    df_sell = df_trades[df_trades['Sub Type'] == 'Sell'].copy()

    print(f"  BUY:  {len(df_buy):,} trades")
    print(f"  SELL: {len(df_sell):,} trades")

    # Statistics
    total_btc_buy = df_buy['btc_amount'].sum()
    total_btc_sell = df_sell['btc_amount'].sum()
    total_usd_buy = df_buy['usd_value'].sum()
    total_usd_sell = df_sell['usd_value'].sum()
    total_fees_usd = df_trades['usd_fee'].sum()

    print(f"\nBTC purchased: {total_btc_buy:.8f}")
    print(f"BTC sold: {total_btc_sell:.8f}")
    print(f"USD spent: ${total_usd_buy:,.2f}")
    print(f"USD received: ${total_usd_sell:,.2f}")
    print(f"Total fees: ${total_fees_usd:.2f}")

    # Prepare transactions
    transactions_to_insert = []

    for _, row in df_trades.iterrows():
        # Parse date with timezone
        dt = pytz.UTC.localize(row['Datetime'].to_pydatetime())

        # Determine transaction type
        if row['Sub Type'] == 'Buy':
            transaction_type = 'BUY'
        elif row['Sub Type'] == 'Sell':
            transaction_type = 'SELL'
        else:
            continue

        # Get values
        btc_amount = row['btc_amount']
        usd_value = row['usd_value']
        usd_fee = row['usd_fee']
        usd_rate = row['usd_rate']

        # Convert to EUR
        tx_date = row['Datetime']
        eur_value = ecb.usd_to_eur(usd_value, tx_date)
        eur_fee = ecb.usd_to_eur(usd_fee, tx_date)
        eur_rate = ecb.usd_to_eur(usd_rate, tx_date)

        record_hash = compute_record_hash(
            source, dt.isoformat(), transaction_type,
            exchange_name, 'BTC', btc_amount, eur_value, eur_fee
        )

        transactions_to_insert.append({
            'date': dt.isoformat(),
            'type': transaction_type,
            'exchange': exchange_name,
            'crypto': 'BTC',
            'amount': btc_amount,
            'price': eur_rate,
            'total': eur_value,
            'fee': eur_fee,
            'record_hash': record_hash
        })

    print(f"\n\nPrepared {len(transactions_to_insert):,} transactions for import")

    # Show sample
    print("\n" + "="*80)
    print("SAMPLE TRANSACTIONS (first 3):")
    print("="*80)

    for i, tx in enumerate(transactions_to_insert[:3]):
        print(f"\n{i+1}. {tx['type']} on {tx['date'][:10]}")
        print(f"   Amount: {tx['amount']:.8f} BTC")
        print(f"   Price:  EUR{tx['price']:.2f}/BTC")
        print(f"   Total:  EUR{tx['total']:.2f}")
        print(f"   Fee:    EUR{tx['fee']:.4f}")

    # Insert via import_and_verify
    def do_inserts(conn):
        cursor = conn.cursor()
        inserted = 0
        for tx in transactions_to_insert:
            cursor.execute("""
                INSERT INTO transactions (
                    transaction_date, transaction_type, exchange_name, cryptocurrency,
                    amount, price_per_unit, total_value, fee_amount, currency,
                    source, imported_at, record_hash
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                tx['date'], tx['type'], tx['exchange'], tx['crypto'],
                tx['amount'], tx['price'], tx['total'], tx['fee'], 'EUR',
                source, imported_at, tx['record_hash']
            ))
            inserted += 1
        return inserted

    inserted = import_and_verify(DB_PATH, source, do_inserts)
    ecb.print_summary()

    return inserted


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print("Usage: python3 import_bitstamp_with_fees.py <filepath> [exchange_name]")
        sys.exit(1)
    filepath = sys.argv[1]
    exchange = sys.argv[2] if len(sys.argv) > 2 else 'Bitstamp'
    import_bitstamp(filepath, exchange)

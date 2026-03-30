"""
Mt.Gox Importer with Correct Fee Handling
- BTC netti: amount = Bitcoins - Bitcoin_Fee
- Fee in EUR: fee_amount = Bitcoin_Fee x (Money / Bitcoins)
- Currency conversion: JPY/USD -> EUR

Usage:
  python3 importers/import_mtgox_with_fees.py <filepath> [exchange_name]
"""

import sys
import os
import pandas as pd
from datetime import datetime
import pytz

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from config import DATABASE_PATH
from importers.import_utils import compute_record_hash, delete_by_source, import_and_verify
from importers.ecb_rates import ECBRates

DB_PATH = DATABASE_PATH


def convert_to_eur(amount, currency, date, ecb, money_fee_rate=None):
    """Convert to EUR using ECB historical rates (USD) or CSV rate (JPY)."""
    if currency == 'EUR':
        return amount
    elif currency == 'JPY':
        # Use Money_Fee_Rate from file (verified accurate)
        if money_fee_rate and money_fee_rate > 0:
            return amount / money_fee_rate
        else:
            return amount / 102.73  # Fallback to 2012 average
    elif currency == 'USD':
        return ecb.usd_to_eur(amount, date)
    else:
        raise ValueError(f"Unsupported currency: {currency}")


def import_mtgox(filepath, exchange_name='Mt.Gox'):
    """Import Mt.Gox CSV with fee handling and source tracking."""

    source = os.path.basename(filepath)
    imported_at = datetime.now().isoformat()

    print("=" * 80)
    print("IMPORTING MT.GOX WITH CORRECT FEE HANDLING")
    print(f"  File:     {filepath}")
    print(f"  Exchange: {exchange_name}")
    print(f"  Source:   {source}")
    print("=" * 80)

    # Load ECB rates for USD conversion
    ecb = ECBRates(os.path.join(PROJECT_ROOT, 'data', 'eurusd.csv'))

    # Read file
    df = pd.read_csv(filepath)
    print(f"\nLoaded {len(df):,} rows from {filepath}")

    # Deduplicate by ID
    original_count = len(df)
    df = df.drop_duplicates(subset=['ID'])
    print(f"After deduplication: {len(df):,} rows ({original_count - len(df):,} duplicates removed)")

    # Parse dates
    df['Date'] = pd.to_datetime(df['Date'])

    # Statistics
    print(f"\nDate range: {df['Date'].min()} to {df['Date'].max()}")
    print(f"Currencies: {df['Currency'].value_counts().to_dict()}")

    buys = len(df[df['Type'] == 'buy'])
    sells = len(df[df['Type'] == 'sell'])
    print(f"Trades: {buys} BUY, {sells} SELL")

    # Calculate totals
    total_btc_gross = df[df['Type'] == 'buy']['Bitcoins'].sum()
    total_btc_fees = df[df['Type'] == 'buy']['Bitcoin_Fee'].sum()
    total_btc_net = total_btc_gross - total_btc_fees

    print(f"\nBTC purchased:")
    print(f"  Gross: {total_btc_gross:.8f} BTC")
    print(f"  Fees:  {total_btc_fees:.8f} BTC")
    print(f"  Net:   {total_btc_net:.8f} BTC")

    # Prepare transactions
    transactions_to_insert = []

    for _, row in df.iterrows():
        # Parse date with timezone
        dt = pytz.UTC.localize(row['Date'].to_pydatetime())

        # Skip non-BTC transactions
        asset = 'BTC'

        # Get values
        bitcoins_gross = float(row['Bitcoins'])
        bitcoin_fee = float(row['Bitcoin_Fee'])
        money = float(row['Money'])
        currency = str(row['Currency'])
        money_fee_rate = float(row['Money_Fee_Rate']) if pd.notna(row['Money_Fee_Rate']) else None

        # Calculate BTC net (what you actually received)
        if row['Type'] == 'buy':
            bitcoins_net = bitcoins_gross - bitcoin_fee
            transaction_type = 'BUY'
        elif row['Type'] == 'sell':
            # For sells, fee is additional cost (you sold gross + paid fee)
            bitcoins_net = bitcoins_gross + bitcoin_fee
            transaction_type = 'SELL'
        else:
            continue  # Skip other types

        # Convert money to EUR
        money_eur = convert_to_eur(money, currency, row['Date'], ecb, money_fee_rate)

        # Calculate price per unit in EUR
        if bitcoins_net > 0:
            price_per_unit_eur = money_eur / bitcoins_net
        else:
            price_per_unit_eur = 0

        # Calculate fee in EUR
        # Fee represents the BTC you didn't receive (for buy) or had to pay extra (for sell)
        # Value of that BTC at the transaction price
        fee_eur = bitcoin_fee * price_per_unit_eur

        tx_date = dt.isoformat()

        record_hash = compute_record_hash(
            source, tx_date, transaction_type, exchange_name,
            asset, bitcoins_net, money_eur, fee_eur
        )

        transactions_to_insert.append({
            'date': tx_date,
            'type': transaction_type,
            'exchange': exchange_name,
            'crypto': asset,
            'amount': bitcoins_net,  # Net BTC (what you actually got)
            'price': price_per_unit_eur,
            'total': money_eur,  # Money paid/received in EUR
            'fee': fee_eur,  # Fee in EUR
            'original_currency': currency,
            'bitcoin_fee': bitcoin_fee,  # Keep for reference
            'id': str(row['ID']),
            'source': source,
            'imported_at': imported_at,
            'record_hash': record_hash
        })

    print(f"\nPrepared {len(transactions_to_insert):,} transactions for import")

    # Show sample
    print("\n" + "=" * 80)
    print("SAMPLE TRANSACTIONS (first 3):")
    print("=" * 80)

    for i, tx in enumerate(transactions_to_insert[:3]):
        print(f"\n{i+1}. {tx['type']} on {tx['date'][:10]}")
        print(f"   Amount:   {tx['amount']:.8f} BTC (net)")
        print(f"   Price:    EUR{tx['price']:.2f}/BTC")
        print(f"   Total:    EUR{tx['total']:.2f}")
        print(f"   Fee:      EUR{tx['fee']:.4f} ({tx['bitcoin_fee']:.8f} BTC)")
        print(f"   Currency: {tx['original_currency']}")

    # Import via import_and_verify (atomic delete+insert+verify)
    def do_inserts(conn):
        cursor = conn.cursor()
        count = 0
        for tx in transactions_to_insert:
            cursor.execute("""
                INSERT INTO transactions (
                    transaction_date, transaction_type, exchange_name, cryptocurrency,
                    amount, price_per_unit, total_value, fee_amount, fee_currency, currency,
                    source, imported_at, record_hash
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                tx['date'], tx['type'], tx['exchange'], tx['crypto'],
                tx['amount'], tx['price'], tx['total'], tx['fee'], 'EUR', 'EUR',
                tx['source'], tx['imported_at'], tx['record_hash']
            ))
            count += 1
        return count

    inserted = import_and_verify(DB_PATH, source, do_inserts)

    return inserted


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print("Usage: python3 importers/import_mtgox_with_fees.py <filepath> [exchange_name]")
        sys.exit(1)
    filepath = sys.argv[1]
    exchange = sys.argv[2] if len(sys.argv) > 2 else 'Mt.Gox'
    import_mtgox(filepath, exchange)

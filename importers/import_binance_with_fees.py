"""
Import Binance Trade History with Fees
Accepts a single CSV file via CLI argument.

Format:
"Date(UTC)","Pair","Side","Price","Executed","Amount","Fee"
"2024-10-18 01:29:50","BTCEUR","SELL","62391.49","0.02776BTC","1731.9877624EUR","1.73198776EUR"

Supports BTCEUR (native EUR) and BTCUSDT/BTCBUSD (converted via ECB rates).
"""

import sys
import os
import pandas as pd
import sqlite3
from datetime import datetime

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from config import DATABASE_PATH
from importers.import_utils import compute_record_hash, delete_by_source
from importers.ecb_rates import ECBRates

# Pairs whose quote currency is USD-equivalent and needs ECB conversion
USD_PAIRS = {'BTCUSDT', 'BTCBUSD'}


def import_binance(filepath, exchange_name='Binance'):
    """
    Import Binance trade history from a single CSV file.

    Args:
        filepath: path to the Binance trade history CSV file
        exchange_name: exchange name for DB records (default 'Binance')
    """
    source = os.path.basename(filepath)
    imported_at = datetime.now().isoformat()

    print("=" * 80)
    print(f"IMPORTING BINANCE TRADE HISTORY WITH FEES")
    print(f"  File:     {filepath}")
    print(f"  Source:   {source}")
    print(f"  Exchange: {exchange_name}")
    print("=" * 80)

    # Read file
    df = pd.read_csv(filepath)
    print(f"\nLoaded {len(df):,} rows from {filepath}")
    print(f"Columns: {df.columns.tolist()}")

    # Parse columns
    def parse_value_with_currency(value):
        """Parse '0.02776BTC' -> (0.02776, 'BTC'). Returns (float, currency_str)."""
        if pd.isna(value):
            return 0.0, ''
        s = str(value).strip()
        for suffix in ('BTC', 'EUR', 'USDT', 'BUSD'):
            if s.endswith(suffix):
                try:
                    return float(s[:-len(suffix)]), suffix
                except ValueError:
                    return 0.0, suffix
        try:
            return float(s), ''
        except ValueError:
            return 0.0, ''

    def parse_amount(value):
        """Parse '0.02776BTC' -> 0.02776 (numeric only)."""
        return parse_value_with_currency(value)[0]

    # Filter BTC trades (any BTC pair)
    btc_pairs = {'BTCEUR'} | USD_PAIRS
    df_btc = df[df['Pair'].isin(btc_pairs)].copy()
    print(f"\nBTC trades: {len(df_btc):,}")

    # Show skipped pairs
    other = df[~df['Pair'].isin(btc_pairs)]
    if len(other) > 0:
        print(f"\nSkipped pairs:")
        for pair, count in other['Pair'].value_counts().items():
            print(f"    {pair}: {count:,} trades")

    # Breakdown by pair
    for pair in sorted(df_btc['Pair'].unique()):
        n = (df_btc['Pair'] == pair).sum()
        print(f"  {pair}: {n:,} trades")

    # Load ECB rates if needed for USD-pair conversion
    needs_ecb = df_btc['Pair'].isin(USD_PAIRS).any()
    ecb = None
    if needs_ecb:
        ecb = ECBRates(os.path.join(PROJECT_ROOT, 'data', 'eurusd.csv'))

    df_btc['date_parsed'] = pd.to_datetime(df_btc['Date(UTC)'])
    df_btc['btc_amount'] = df_btc['Executed'].apply(parse_amount)
    df_btc['raw_amount'] = df_btc['Amount'].apply(parse_amount)
    df_btc['raw_price'] = df_btc['Price'].astype(float)

    # Parse fee with currency detection (BUY fees are in BTC, SELL fees in quote currency)
    fee_parsed = df_btc['Fee'].apply(parse_value_with_currency)
    df_btc['raw_fee'] = fee_parsed.apply(lambda x: x[0])
    df_btc['fee_currency'] = fee_parsed.apply(lambda x: x[1])

    # Convert all values to EUR
    def to_eur_amount(row):
        """Convert Amount/Price from quote currency to EUR."""
        if row['Pair'] in USD_PAIRS and ecb:
            return ecb.usd_to_eur(row['raw_amount'], row['date_parsed'])
        return row['raw_amount']

    def to_eur_price(row):
        """Convert price from quote currency to EUR."""
        if row['Pair'] in USD_PAIRS and ecb:
            return ecb.usd_to_eur(row['raw_price'], row['date_parsed'])
        return row['raw_price']

    def to_eur_fee(row):
        """Convert fee to EUR. BTC fees use the trade price; USD fees use ECB."""
        fee_val = row['raw_fee']
        fee_cur = row['fee_currency']
        if fee_cur == 'BTC':
            # Fee in BTC → convert using the trade's EUR price
            eur_price = to_eur_price(row)
            return fee_val * eur_price
        elif fee_cur in ('USDT', 'BUSD') and ecb:
            return ecb.usd_to_eur(fee_val, row['date_parsed'])
        # EUR or unknown → pass through
        return fee_val

    df_btc['eur_amount'] = df_btc.apply(to_eur_amount, axis=1)
    df_btc['price_per_btc'] = df_btc.apply(to_eur_price, axis=1)
    df_btc['fee_eur'] = df_btc.apply(to_eur_fee, axis=1)

    # Separate BUY and SELL
    df_buy = df_btc[df_btc['Side'] == 'BUY'].copy()
    df_sell = df_btc[df_btc['Side'] == 'SELL'].copy()

    print(f"\n  BUY:  {len(df_buy):,} trades")
    print(f"  SELL: {len(df_sell):,} trades")

    # Check fee consistency
    print(f"\nFee analysis:")
    print(f"  Total fees (all): {df_btc['fee_eur'].sum():,.2f} EUR")
    print(f"  Average fee: {df_btc['fee_eur'].mean():.2f} EUR")
    print(f"  Fee as % of amount: {(df_btc['fee_eur'].sum() / df_btc['eur_amount'].sum() * 100):.4f}%")

    if ecb:
        ecb.print_summary()

    # Prepare transactions for insert
    transactions_to_insert = []

    for _, row in df_btc.iterrows():
        transactions_to_insert.append({
            'date': row['date_parsed'].isoformat(),
            'type': row['Side'],
            'exchange': exchange_name,
            'crypto': 'BTC',
            'amount': row['btc_amount'],
            'price': row['price_per_btc'],
            'total': row['eur_amount'],
            'fee': row['fee_eur']
        })

    print(f"\nPrepared {len(transactions_to_insert):,} transactions for import")

    # Connect to database
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()

    # Delete previous records for this source file
    deleted = delete_by_source(conn, source)
    print(f"\n  Deleted {deleted} previous records for {source}")

    # Insert new data
    print("\nInserting new data with fees...")
    inserted = 0
    for tx in transactions_to_insert:
        record_hash = compute_record_hash(
            source, tx['date'], tx['type'], tx['exchange'],
            tx['crypto'], tx['amount'], tx['total'], tx['fee']
        )

        cursor.execute("""
            INSERT INTO transactions (
                transaction_date, transaction_type, exchange_name, cryptocurrency,
                amount, price_per_unit, total_value, fee_amount,
                source, imported_at, record_hash
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            tx['date'], tx['type'], tx['exchange'], tx['crypto'],
            tx['amount'], tx['price'], tx['total'], tx['fee'],
            source, imported_at, record_hash
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
        WHERE source = ?
        GROUP BY transaction_type
    """, (source,))

    print("\n" + "=" * 80)
    print("VERIFICATION")
    print("=" * 80)

    for row in cursor.fetchall():
        tx_type, count, btc, eur, fees = row
        print(f"\n{tx_type}:")
        print(f"  Transactions: {count:,}")
        print(f"  BTC: {btc:.8f}")
        print(f"  EUR: {eur:,.2f} EUR")
        print(f"  Fees: {fees:,.2f} EUR")

    conn.close()

    print("\n" + "=" * 80)
    print("SUCCESS!")
    print("=" * 80)
    print(f"\nBinance data imported with fees from {source}")
    print(f"  Source tracking: source={source}, imported_at={imported_at}")
    print("\n" + "=" * 80)


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print("Usage: python3 import_binance_with_fees.py <filepath> [exchange_name]")
        print("  filepath:      path to Binance trade history CSV file")
        print("  exchange_name: optional, default 'Binance'")
        sys.exit(1)
    filepath = sys.argv[1]
    exchange = sys.argv[2] if len(sys.argv) > 2 else 'Binance'
    import_binance(filepath, exchange)

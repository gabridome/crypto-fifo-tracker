"""
Kraken Ledger Importer with Fee Handling

Format: Each trade has 2 rows with same refid
BUY:
  Row 1: BTC, amount=positive (BTC received), fee=0
  Row 2: EUR, amount=negative (EUR paid), fee=EUR_fee

SELL:
  Row 1: BTC, amount=negative (BTC sold), fee=0
  Row 2: EUR, amount=positive (EUR received), fee=EUR_fee

Usage:
  python3 importers/import_kraken_with_fees.py <filepath> [exchange_name]
"""

import sys
import os
import pandas as pd
from datetime import datetime
import pytz
from collections import defaultdict

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from config import DATABASE_PATH
from importers.import_utils import compute_record_hash, delete_by_source, import_and_verify

DB_PATH = DATABASE_PATH


def import_kraken(filepath, exchange_name='Kraken'):
    """Import Kraken ledger CSV with fee handling and source tracking."""

    source = os.path.basename(filepath)
    imported_at = datetime.now().isoformat()

    print("=" * 80)
    print(f"IMPORTING KRAKEN LEDGER WITH FEE HANDLING")
    print(f"  File:     {filepath}")
    print(f"  Exchange: {exchange_name}")
    print(f"  Source:   {source}")
    print("=" * 80)

    # Read file
    df = pd.read_csv(filepath)
    print(f"\nLoaded {len(df):,} rows from {filepath}")

    # Parse dates
    df['time'] = pd.to_datetime(df['time'])

    # Filter only trades (not deposits/withdrawals)
    df_trades = df[df['type'] == 'trade'].copy()
    print(f"Trade rows: {len(df_trades):,}")

    # Group by refid to pair BTC and EUR rows
    print("\nPairing BTC/EUR rows by refid...")

    trades = defaultdict(dict)

    for _, row in df_trades.iterrows():
        refid = row['refid']
        asset = row['asset']
        amount = float(row['amount'])
        fee = float(row['fee'])
        time = row['time']

        if asset == 'BTC':
            trades[refid]['btc_amount'] = amount
            trades[refid]['time'] = time
        elif asset == 'EUR':
            trades[refid]['eur_amount'] = amount
            trades[refid]['eur_fee'] = fee
            trades[refid]['time'] = time

    # Convert to list and determine trade type
    complete_trades = []

    for refid, data in trades.items():
        # Skip incomplete trades
        if 'btc_amount' not in data or 'eur_amount' not in data:
            continue

        btc_amount = data['btc_amount']
        eur_amount = data['eur_amount']
        eur_fee = data.get('eur_fee', 0)
        time = data['time']

        # Determine trade type
        if btc_amount > 0 and eur_amount < 0:
            # BUY: received BTC, paid EUR
            trade_type = 'BUY'
            btc_net = btc_amount
            eur_paid = abs(eur_amount)
        elif btc_amount < 0 and eur_amount > 0:
            # SELL: sold BTC, received EUR
            trade_type = 'SELL'
            btc_net = abs(btc_amount)
            eur_paid = eur_amount  # EUR received (will be proceeds)
        else:
            # Invalid combination
            continue

        complete_trades.append({
            'refid': refid,
            'time': time,
            'type': trade_type,
            'btc_amount': btc_net,
            'eur_amount': eur_paid,
            'fee_eur': eur_fee
        })

    print(f"Reconstructed {len(complete_trades):,} complete BTC/EUR trades")

    # Separate by type
    trades_buy = [t for t in complete_trades if t['type'] == 'BUY']
    trades_sell = [t for t in complete_trades if t['type'] == 'SELL']

    print(f"  BUY:  {len(trades_buy):,} trades")
    print(f"  SELL: {len(trades_sell):,} trades")

    # Statistics
    total_btc_buy = sum(t['btc_amount'] for t in trades_buy)
    total_btc_sell = sum(t['btc_amount'] for t in trades_sell)
    total_eur_buy = sum(t['eur_amount'] for t in trades_buy)
    total_eur_sell = sum(t['eur_amount'] for t in trades_sell)
    total_fees = sum(t['fee_eur'] for t in complete_trades)

    print(f"\nBTC purchased: {total_btc_buy:.8f}")
    print(f"BTC sold: {total_btc_sell:.8f}")
    print(f"EUR spent: EUR{total_eur_buy:,.2f}")
    print(f"EUR received: EUR{total_eur_sell:,.2f}")
    print(f"Total fees: EUR{total_fees:.2f}")

    # Prepare transactions
    transactions_to_insert = []

    for trade in complete_trades:
        # Parse date with timezone
        dt = pytz.UTC.localize(trade['time'].to_pydatetime())

        # Calculate price per unit
        if trade['btc_amount'] > 0:
            price_per_unit = trade['eur_amount'] / trade['btc_amount']
        else:
            price_per_unit = 0

        tx_date = dt.isoformat()
        tx_type = trade['type']
        tx_amount = trade['btc_amount']
        tx_total = trade['eur_amount']
        tx_fee = trade['fee_eur']

        record_hash = compute_record_hash(
            source, tx_date, tx_type, exchange_name,
            'BTC', tx_amount, tx_total, tx_fee
        )

        transactions_to_insert.append({
            'date': tx_date,
            'type': tx_type,
            'exchange': exchange_name,
            'crypto': 'BTC',
            'amount': tx_amount,
            'price': price_per_unit,
            'total': tx_total,
            'fee': tx_fee,
            'refid': trade['refid'],
            'source': source,
            'imported_at': imported_at,
            'record_hash': record_hash
        })

    print(f"\nPrepared {len(transactions_to_insert):,} transactions for import")

    # Show sample
    print("\n" + "=" * 80)
    print("SAMPLE TRANSACTIONS (first 5):")
    print("=" * 80)

    for i, tx in enumerate(transactions_to_insert[:5]):
        print(f"\n{i+1}. {tx['type']} on {tx['date'][:10]}")
        print(f"   Amount: {tx['amount']:.8f} BTC")
        print(f"   Price:  EUR{tx['price']:.2f}/BTC")
        print(f"   Total:  EUR{tx['total']:.2f}")
        print(f"   Fee:    EUR{tx['fee']:.4f}")
        print(f"   RefID:  {tx['refid']}")

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
        print("Usage: python3 importers/import_kraken_with_fees.py <filepath> [exchange_name]")
        sys.exit(1)
    filepath = sys.argv[1]
    exchange = sys.argv[2] if len(sys.argv) > 2 else 'Kraken'
    import_kraken(filepath, exchange)

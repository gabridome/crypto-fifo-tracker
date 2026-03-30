"""
Bybit UTA (Unified Trading Account) Importer

Format: AssetChangeDetails CSV from Bybit with a UID header line.
Rows are individual fills — BTC and EUR rows paired by timestamp.
Each BTC TRADE row = a partial fill of a sell order.

Aggregates fills by second into trades for cleaner DB records.

Usage:
    python3 importers/import_bybit.py <filepath> [exchange_name]
"""

import sys
import os
import csv
from datetime import datetime
from collections import defaultdict
import pytz

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from config import DATABASE_PATH
from importers.import_utils import compute_record_hash, import_and_verify

DB_PATH = DATABASE_PATH


def import_bybit(filepath, exchange_name='Bybit'):
    """Import Bybit UTA trade history from AssetChangeDetails CSV."""

    source = os.path.basename(filepath)
    imported_at = datetime.now().isoformat()

    print("=" * 80)
    print("IMPORTING BYBIT UTA TRADE HISTORY")
    print(f"  File:     {filepath}")
    print(f"  Source:   {source}")
    print(f"  Exchange: {exchange_name}")
    print("=" * 80)

    # Read CSV — skip UID header line
    rows = []
    with open(filepath, 'r', encoding='utf-8-sig') as f:
        first_line = f.readline()
        if not first_line.startswith('Uid,'):
            # First line is the "UID: 176178208,..." metadata — skip it
            pass
        else:
            # First line IS the header — seek back
            f.seek(0)
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(row)

    print(f"\nLoaded {len(rows):,} rows")

    # Filter TRADE rows only
    trade_rows = [r for r in rows if r.get('Type') == 'TRADE']
    transfer_rows = [r for r in rows if r.get('Type') in ('TRANSFER_IN', 'TRANSFER_OUT')]
    print(f"  TRADE rows: {len(trade_rows)}")
    print(f"  TRANSFER rows: {len(transfer_rows)}")

    if not trade_rows:
        print("\nNo TRADE rows found!")
        return 0

    # Group BTC trade fills by timestamp → aggregate into trades
    # Each timestamp has paired BTC (negative=sold) and EUR (positive=received) rows
    by_time = defaultdict(lambda: {'btc': 0.0, 'eur': 0.0, 'fee': 0.0, 'prices': []})

    for r in trade_rows:
        ts = r['Time(UTC)'].strip()
        currency = r['Currency'].strip()
        qty = float(r['Quantity'])
        price = float(r['Filled Price'])
        fee = float(r['Fee Paid'])

        if currency == 'BTC':
            by_time[ts]['btc'] += qty  # negative for sells
            by_time[ts]['prices'].append(price)
            by_time[ts]['fee'] += abs(fee)
        elif currency == 'EUR':
            by_time[ts]['eur'] += qty  # positive for receives

    # Build trade records
    trades = []
    for ts in sorted(by_time.keys()):
        data = by_time[ts]
        btc_amount = abs(data['btc'])
        eur_amount = abs(data['eur'])

        if btc_amount <= 0:
            continue

        # Determine trade type from sign
        tx_type = 'SELL' if data['btc'] < 0 else 'BUY'

        # Average price across fills at this timestamp
        avg_price = eur_amount / btc_amount if btc_amount > 0 else 0

        dt = datetime.strptime(ts, '%Y-%m-%d %H:%M:%S')
        dt = pytz.UTC.localize(dt)

        trades.append({
            'date': dt.isoformat(),
            'type': tx_type,
            'crypto': 'BTC',
            'amount': btc_amount,
            'price': avg_price,
            'total': eur_amount,
            'fee': data['fee'],
        })

    print(f"\nAggregated into {len(trades)} trades:")
    total_btc = sum(t['amount'] for t in trades)
    total_eur = sum(t['total'] for t in trades)
    total_fees = sum(t['fee'] for t in trades)
    for t in trades:
        print(f"  {t['date'][:19]} {t['type']:4s} {t['amount']:.8f} BTC @ {t['price']:,.2f} = {t['total']:,.2f} EUR")
    print(f"\nTotal: {total_btc:.8f} BTC, {total_eur:,.2f} EUR, fees: {total_fees:.2f} EUR")

    # Insert via import_and_verify
    def do_inserts(conn):
        cursor = conn.cursor()

        # Also clean up the old manual_entry record for Bybit
        cursor.execute("DELETE FROM transactions WHERE exchange_name = ? AND source = 'manual_entry'",
                       (exchange_name,))
        manual_deleted = cursor.rowcount
        if manual_deleted:
            print(f"  Deleted {manual_deleted} legacy manual_entry record(s) for {exchange_name}")

        inserted = 0
        for t in trades:
            record_hash = compute_record_hash(
                source, t['date'], t['type'], exchange_name,
                t['crypto'], t['amount'], t['total'], t['fee']
            )
            cursor.execute("""
                INSERT INTO transactions (
                    transaction_date, transaction_type, exchange_name, cryptocurrency,
                    amount, price_per_unit, total_value, fee_amount, fee_currency, currency,
                    source, imported_at, record_hash
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                t['date'], t['type'], exchange_name, t['crypto'],
                t['amount'], t['price'], t['total'], t['fee'],
                'EUR', 'EUR',
                source, imported_at, record_hash,
            ))
            inserted += 1
        return inserted

    inserted = import_and_verify(DB_PATH, source, do_inserts)

    return inserted


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print("Usage: python3 importers/import_bybit.py <filepath> [exchange_name]")
        sys.exit(1)
    filepath = sys.argv[1]
    exchange = sys.argv[2] if len(sys.argv) > 2 else 'Bybit'
    import_bybit(filepath, exchange)

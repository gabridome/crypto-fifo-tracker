"""
Kraken Ledger Importer with Fee Handling

Format: Each trade has 2 rows with same refid
BUY:
  Row 1: BTC, amount=positive (BTC received), fee=0
  Row 2: EUR, amount=negative (EUR paid), fee=EUR_fee

SELL:
  Row 1: BTC, amount=negative (BTC sold), fee=0
  Row 2: EUR, amount=positive (EUR received), fee=EUR_fee
"""

import pandas as pd
import sqlite3
from datetime import datetime
import pytz
from collections import defaultdict

from config import DATABASE_PATH
DB_PATH = DATABASE_PATH

KRAKEN_FILE = 'data/historical/kraken_ledgers.csv'

print("="*80)
print("IMPORTING KRAKEN LEDGER WITH FEE HANDLING")
print("="*80)

# Read file
df = pd.read_csv(KRAKEN_FILE)
print(f"\nLoaded {len(df):,} rows from {KRAKEN_FILE}")

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
print(f"EUR spent: €{total_eur_buy:,.2f}")
print(f"EUR received: €{total_eur_sell:,.2f}")
print(f"Total fees: €{total_fees:.2f}")

# Connect to database
conn = sqlite3.connect(DB_PATH)
cursor = conn.cursor()

# Check current Kraken data
cursor.execute("""
    SELECT COUNT(*), MIN(transaction_date), MAX(transaction_date)
    FROM transactions
    WHERE exchange_name = 'Kraken'
    AND cryptocurrency = 'BTC'
""")
current_data = cursor.fetchone()
print(f"\nCurrent Kraken BTC data in DB:")
print(f"  Transactions: {current_data[0]:,}")
if current_data[1]:
    print(f"  Date range: {current_data[1]} to {current_data[2]}")

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
    
    transactions_to_insert.append({
        'date': dt.isoformat(),
        'type': trade['type'],
        'exchange': 'Kraken',
        'crypto': 'BTC',
        'amount': trade['btc_amount'],
        'price': price_per_unit,
        'total': trade['eur_amount'],
        'fee': trade['fee_eur'],
        'refid': trade['refid']
    })

print(f"\n\nPrepared {len(transactions_to_insert):,} transactions for import")

# Show sample
print("\n" + "="*80)
print("SAMPLE TRANSACTIONS (first 5):")
print("="*80)

for i, tx in enumerate(transactions_to_insert[:5]):
    print(f"\n{i+1}. {tx['type']} on {tx['date'][:10]}")
    print(f"   Amount: {tx['amount']:.8f} BTC")
    print(f"   Price:  €{tx['price']:.2f}/BTC")
    print(f"   Total:  €{tx['total']:.2f}")
    print(f"   Fee:    €{tx['fee']:.4f}")
    print(f"   RefID:  {tx['refid']}")

# Ask for confirmation
print("\n" + "="*80)
print("DECISION POINT")
print("="*80)
print(f"\nCurrent DB has: {current_data[0]:,} Kraken BTC transactions")
print(f"New import will add: {len(transactions_to_insert):,} transactions")
print("\nOptions:")
print("1. DELETE existing Kraken data and import new (RECOMMENDED)")
print("2. APPEND new data (keep existing)")
print("3. Cancel (no changes)")

choice = input("\nEnter choice (1, 2, or 3): ").strip()

if choice == '1':
    # Delete existing
    print("\nDeleting existing Kraken data...")
    cursor.execute("""
        DELETE FROM transactions
        WHERE exchange_name = 'Kraken'
        AND cryptocurrency = 'BTC'
    """)
    deleted = cursor.rowcount
    print(f"  Deleted: {deleted:,} transactions")
elif choice != '2':
    print("\n✗ Aborted. No changes made.")
    conn.close()
    exit(0)

# Insert new data
print("\nInserting new data...")
inserted = 0
for tx in transactions_to_insert:
    cursor.execute("""
        INSERT INTO transactions (
            transaction_date, transaction_type, exchange_name, cryptocurrency,
            amount, price_per_unit, total_value, fee_amount, currency
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        tx['date'], tx['type'], tx['exchange'], tx['crypto'],
        tx['amount'], tx['price'], tx['total'], tx['fee'], 'EUR'
    ))
    inserted += 1

conn.commit()
conn.close()

print(f"  Inserted: {inserted:,} transactions")

# Verify
conn = sqlite3.connect(DB_PATH)
cursor = conn.cursor()

cursor.execute("""
    SELECT 
        transaction_type,
        COUNT(*) as count,
        SUM(amount) as total_btc,
        SUM(total_value) as total_eur,
        SUM(fee_amount) as total_fees_eur
    FROM transactions
    WHERE exchange_name = 'Kraken'
    AND cryptocurrency = 'BTC'
    GROUP BY transaction_type
""")

print("\n" + "="*80)
print("VERIFICATION")
print("="*80)

for row in cursor.fetchall():
    tx_type, count, btc, eur, fees = row
    print(f"\n{tx_type}:")
    print(f"  Transactions: {count:,}")
    print(f"  BTC: {btc:.8f}")
    print(f"  EUR: €{eur:,.2f}")
    print(f"  Fees: €{fees:,.2f}")

conn.close()

print("\n" + "="*80)
print("SUCCESS!")
print("="*80)
print("\n✓ Kraken ledger imported with fee handling")
print("\nKey points:")
print("  - Paired BTC/EUR rows by refid")
print("  - Fee extracted from EUR row")
print("  - Trade type determined by amount signs")

print("\n" + "="*80)

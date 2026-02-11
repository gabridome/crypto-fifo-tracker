"""
Bitstamp Importer with Fee Handling
- Fees in USD → convert to EUR
- Sub Type: Buy/Sell
"""

import pandas as pd
import sqlite3
from datetime import datetime
import pytz
from ecb_rates import ECBRates

ecb = ECBRates()

from config import DATABASE_PATH
DB_PATH = DATABASE_PATH

BITSTAMP_FILE = 'data/bitstamp_history.csv'

print("="*80)
print("IMPORTING BITSTAMP WITH FEE HANDLING")
print("="*80)

# Read file
df = pd.read_csv(BITSTAMP_FILE)
print(f"\nLoaded {len(df):,} rows from {BITSTAMP_FILE}")

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

# Connect to database
conn = sqlite3.connect(DB_PATH)
cursor = conn.cursor()

# Check current Bitstamp data
cursor.execute("""
    SELECT COUNT(*), MIN(transaction_date), MAX(transaction_date)
    FROM transactions
    WHERE exchange_name = 'Bitstamp'
    AND cryptocurrency = 'BTC'
""")
current_data = cursor.fetchone()
print(f"\nCurrent Bitstamp BTC data in DB:")
print(f"  Transactions: {current_data[0]:,}")
if current_data[1]:
    print(f"  Date range: {current_data[1]} to {current_data[2]}")

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

    transactions_to_insert.append({
        'date': dt.isoformat(),
        'type': transaction_type,
        'exchange': 'Bitstamp',
        'crypto': 'BTC',
        'amount': btc_amount,
        'price': eur_rate,
        'total': eur_value,
        'fee': eur_fee
    })

print(f"\n\nPrepared {len(transactions_to_insert):,} transactions for import")

# Show sample
print("\n" + "="*80)
print("SAMPLE TRANSACTIONS (first 3):")
print("="*80)

for i, tx in enumerate(transactions_to_insert[:3]):
    print(f"\n{i+1}. {tx['type']} on {tx['date'][:10]}")
    print(f"   Amount: {tx['amount']:.8f} BTC")
    print(f"   Price:  €{tx['price']:.2f}/BTC")
    print(f"   Total:  €{tx['total']:.2f}")
    print(f"   Fee:    €{tx['fee']:.4f}")

# Ask for confirmation
print("\n" + "="*80)
print("DECISION POINT")
print("="*80)
print(f"\nCurrent DB has: {current_data[0]:,} Bitstamp BTC transactions")
print(f"New import will add: {len(transactions_to_insert):,} transactions")
print("\nOptions:")
print("1. DELETE existing Bitstamp data and import new (RECOMMENDED)")
print("2. APPEND new data (keep existing)")
print("3. Cancel (no changes)")

choice = input("\nEnter choice (1, 2, or 3): ").strip()

if choice == '1':
    # Delete existing
    print("\nDeleting existing Bitstamp data...")
    cursor.execute("""
        DELETE FROM transactions
        WHERE exchange_name = 'Bitstamp'
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
    WHERE exchange_name = 'Bitstamp'
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

ecb.print_summary()
conn.close()

print("\n" + "="*80)
print("SUCCESS!")
print("="*80)
print("\n✓ Bitstamp data imported with fee handling")

print("\n" + "="*80)

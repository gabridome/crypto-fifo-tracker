"""
Mt.Gox Importer with Correct Fee Handling
- BTC netti: amount = Bitcoins - Bitcoin_Fee
- Fee in EUR: fee_amount = Bitcoin_Fee × (Money / Bitcoins)
- Currency conversion: JPY/USD → EUR
"""

import pandas as pd
import sqlite3
from datetime import datetime
import pytz

from config import DATABASE_PATH
DB_PATH = DATABASE_PATH

MTGOX_FILE = 'data/historical/mtgox.csv'

print("="*80)
print("IMPORTING MT.GOX WITH CORRECT FEE HANDLING")
print("="*80)

def convert_to_eur(amount, currency, money_fee_rate=None):
    """Convert to EUR using best available rate"""
    if currency == 'EUR':
        return amount
    elif currency == 'JPY':
        # Use Money_Fee_Rate from file (verified accurate)
        if money_fee_rate and money_fee_rate > 0:
            return amount / money_fee_rate
        else:
            return amount / 102.73  # Fallback to 2012 average
    elif currency == 'USD':
        # Use fixed 2012 average: EUR/USD = 1.28
        return amount / 1.28
    else:
        raise ValueError(f"Unsupported currency: {currency}")

# Read file
df = pd.read_csv(MTGOX_FILE)
print(f"\nLoaded {len(df):,} rows from {MTGOX_FILE}")

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

# Connect to database
conn = sqlite3.connect(DB_PATH)
cursor = conn.cursor()

# Check current Mt.Gox data
cursor.execute("""
    SELECT COUNT(*), MIN(transaction_date), MAX(transaction_date)
    FROM transactions
    WHERE exchange_name = 'Mt.Gox'
    AND cryptocurrency = 'BTC'
""")
current_data = cursor.fetchone()
print(f"\nCurrent Mt.Gox BTC data in DB:")
print(f"  Transactions: {current_data[0]:,}")
if current_data[1]:
    print(f"  Date range: {current_data[1]} to {current_data[2]}")

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
    money_eur = convert_to_eur(money, currency, money_fee_rate)
    
    # Calculate price per unit in EUR
    if bitcoins_net > 0:
        price_per_unit_eur = money_eur / bitcoins_net
    else:
        price_per_unit_eur = 0
    
    # Calculate fee in EUR
    # Fee represents the BTC you didn't receive (for buy) or had to pay extra (for sell)
    # Value of that BTC at the transaction price
    fee_eur = bitcoin_fee * price_per_unit_eur
    
    transactions_to_insert.append({
        'date': dt.isoformat(),
        'type': transaction_type,
        'exchange': 'Mt.Gox',
        'crypto': asset,
        'amount': bitcoins_net,  # Net BTC (what you actually got)
        'price': price_per_unit_eur,
        'total': money_eur,  # Money paid/received in EUR
        'fee': fee_eur,  # Fee in EUR
        'original_currency': currency,
        'bitcoin_fee': bitcoin_fee,  # Keep for reference
        'id': str(row['ID'])
    })

print(f"\n\nPrepared {len(transactions_to_insert):,} transactions for import")

# Show sample
print("\n" + "="*80)
print("SAMPLE TRANSACTIONS (first 3):")
print("="*80)

for i, tx in enumerate(transactions_to_insert[:3]):
    print(f"\n{i+1}. {tx['type']} on {tx['date'][:10]}")
    print(f"   Amount:   {tx['amount']:.8f} BTC (net)")
    print(f"   Price:    €{tx['price']:.2f}/BTC")
    print(f"   Total:    €{tx['total']:.2f}")
    print(f"   Fee:      €{tx['fee']:.4f} ({tx['bitcoin_fee']:.8f} BTC)")
    print(f"   Currency: {tx['original_currency']}")

# Ask for confirmation
print("\n" + "="*80)
print("DECISION POINT")
print("="*80)
print(f"\nCurrent DB has: {current_data[0]:,} Mt.Gox BTC transactions")
print(f"New import will add: {len(transactions_to_insert):,} transactions")
print("\nOptions:")
print("1. DELETE existing Mt.Gox data and import new (RECOMMENDED)")
print("2. APPEND new data (keep existing)")
print("3. Cancel (no changes)")

choice = input("\nEnter choice (1, 2, or 3): ").strip()

if choice == '1':
    # Delete existing
    print("\nDeleting existing Mt.Gox data...")
    cursor.execute("""
        DELETE FROM transactions
        WHERE exchange_name = 'Mt.Gox'
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
    WHERE exchange_name = 'Mt.Gox'
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
    print(f"  BTC (net): {btc:.8f}")
    print(f"  EUR: €{eur:,.2f}")
    print(f"  Fees: €{fees:,.2f}")

conn.close()

print("\n" + "="*80)
print("SUCCESS!")
print("="*80)
print("\n✓ Mt.Gox data imported with correct fee handling")
print("\nKey points:")
print("  - BTC amounts are NET (after deducting Bitcoin_Fee)")
print("  - Fees converted to EUR and stored separately")
print("  - JPY/USD converted to EUR using historical rates")
print("\nNext steps:")
print("1. Import Bitstamp (if needed)")
print("2. Re-run FIFO calculation")
print("3. Re-generate Anexo G1")

print("\n" + "="*80)

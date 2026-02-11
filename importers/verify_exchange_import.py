"""
Verify Exchange Import
Check that exchange data was imported correctly with fees
"""

import sqlite3
import sys

DB_PATH = 'data/crypto_fifo.db'

if len(sys.argv) < 2:
    print("Usage: python3 verify_exchange_import.py <ExchangeName>")
    print("Example: python3 verify_exchange_import.py Coinbase")
    sys.exit(1)

exchange_name = sys.argv[1]

print("="*80)
print(f"VERIFYING IMPORT: {exchange_name}")
print("="*80)

conn = sqlite3.connect(DB_PATH)
cursor = conn.cursor()

# 1. Basic counts
cursor.execute("""
    SELECT 
        COUNT(*) as total,
        COUNT(CASE WHEN transaction_type = 'BUY' THEN 1 END) as buys,
        COUNT(CASE WHEN transaction_type = 'SELL' THEN 1 END) as sells,
        COUNT(CASE WHEN transaction_type = 'DEPOSIT' THEN 1 END) as deposits,
        COUNT(CASE WHEN transaction_type = 'WITHDRAWAL' THEN 1 END) as withdrawals
    FROM transactions
    WHERE exchange_name = ?
    AND cryptocurrency = 'BTC'
""", (exchange_name,))

counts = cursor.fetchone()
print(f"\nTransaction Counts:")
print(f"  Total:      {counts[0]:>8,}")
print(f"  BUY:        {counts[1]:>8,}")
print(f"  SELL:       {counts[2]:>8,}")
print(f"  DEPOSIT:    {counts[3]:>8,}")
print(f"  WITHDRAWAL: {counts[4]:>8,}")

# 2. Fee statistics
cursor.execute("""
    SELECT 
        COUNT(*) as total_trans,
        COUNT(CASE WHEN fee_amount > 0 THEN 1 END) as with_fees,
        SUM(fee_amount) as total_fees,
        AVG(CASE WHEN fee_amount > 0 THEN fee_amount END) as avg_fee,
        MIN(fee_amount) as min_fee,
        MAX(fee_amount) as max_fee
    FROM transactions
    WHERE exchange_name = ?
    AND cryptocurrency = 'BTC'
    AND transaction_type IN ('BUY', 'SELL')
""", (exchange_name,))

fees = cursor.fetchone()
print(f"\nFee Statistics:")
print(f"  Transactions with fees: {fees[1]:,} / {fees[0]:,} ({fees[1]/fees[0]*100:.1f}%)")
print(f"  Total fees:             €{fees[2]:,.2f}")
print(f"  Average fee:            €{fees[3]:.4f}")
print(f"  Min fee:                €{fees[4]:.4f}")
print(f"  Max fee:                €{fees[5]:,.2f}")

# 3. BTC and EUR totals
cursor.execute("""
    SELECT 
        transaction_type,
        SUM(amount) as total_btc,
        SUM(total_value) as total_eur,
        SUM(fee_amount) as total_fees
    FROM transactions
    WHERE exchange_name = ?
    AND cryptocurrency = 'BTC'
    AND transaction_type IN ('BUY', 'SELL')
    GROUP BY transaction_type
""", (exchange_name,))

print(f"\nTotals by Type:")
for row in cursor.fetchall():
    tx_type, btc, eur, row_fees = row
    print(f"  {tx_type}:")
    print(f"    BTC:   {btc:>15.8f}")
    print(f"    EUR:   €{eur:>14,.2f}")
    print(f"    Fees:  €{row_fees:>14,.2f}")

# 4. Date range
cursor.execute("""
    SELECT 
        MIN(transaction_date) as first_tx,
        MAX(transaction_date) as last_tx
    FROM transactions
    WHERE exchange_name = ?
    AND cryptocurrency = 'BTC'
""", (exchange_name,))

dates = cursor.fetchone()
print(f"\nDate Range:")
print(f"  First: {dates[0]}")
print(f"  Last:  {dates[1]}")

# 5. Sample transactions
cursor.execute("""
    SELECT 
        transaction_date,
        transaction_type,
        amount,
        price_per_unit,
        total_value,
        fee_amount
    FROM transactions
    WHERE exchange_name = ?
    AND cryptocurrency = 'BTC'
    AND transaction_type IN ('BUY', 'SELL')
    ORDER BY transaction_date
    LIMIT 3
""", (exchange_name,))

print(f"\nSample Transactions (first 3):")
print(f"{'Date':<12} {'Type':<6} {'Amount':>12} {'Price':>10} {'Total':>12} {'Fee':>10}")
print("-"*80)

for row in cursor.fetchall():
    date, tx_type, amount, price, total, fee = row
    print(f"{date[:10]:<12} {tx_type:<6} {amount:>12.8f} {price:>10.2f} €{total:>11.2f} €{fee:>9.4f}")

# 6. Warnings and issues
print(f"\n" + "="*80)
print("CHECKS:")
print("="*80)

# Check 1: Missing fees
cursor.execute("""
    SELECT COUNT(*)
    FROM transactions
    WHERE exchange_name = ?
    AND cryptocurrency = 'BTC'
    AND transaction_type IN ('BUY', 'SELL')
    AND (fee_amount IS NULL OR fee_amount = 0)
""", (exchange_name,))

missing_fees = cursor.fetchone()[0]
total_trades = fees[0]  # fees[0] is total_trans from the earlier query
if missing_fees == 0:
    print("✓ All transactions have fees")
elif total_trades > 0 and missing_fees / total_trades < 0.1:
    print(f"⚠️  {missing_fees} transactions missing fees (< 10%, acceptable)")
else:
    print(f"✗ {missing_fees} transactions missing fees (> 10%, check!)")

# Check 2: Zero amounts
cursor.execute("""
    SELECT COUNT(*)
    FROM transactions
    WHERE exchange_name = ?
    AND cryptocurrency = 'BTC'
    AND amount = 0
""", (exchange_name,))

zero_amounts = cursor.fetchone()[0]
if zero_amounts == 0:
    print("✓ No zero-amount transactions")
else:
    print(f"⚠️  {zero_amounts} zero-amount transactions found")

# Check 3: Negative prices
cursor.execute("""
    SELECT COUNT(*)
    FROM transactions
    WHERE exchange_name = ?
    AND cryptocurrency = 'BTC'
    AND price_per_unit < 0
""", (exchange_name,))

negative_prices = cursor.fetchone()[0]
if negative_prices == 0:
    print("✓ No negative prices")
else:
    print(f"✗ {negative_prices} negative prices found (ERROR!)")

# Check 4: Reasonable price range
cursor.execute("""
    SELECT 
        MIN(price_per_unit) as min_price,
        MAX(price_per_unit) as max_price,
        AVG(price_per_unit) as avg_price
    FROM transactions
    WHERE exchange_name = ?
    AND cryptocurrency = 'BTC'
    AND transaction_type IN ('BUY', 'SELL')
    AND price_per_unit > 0
""", (exchange_name,))

prices = cursor.fetchone()
print(f"✓ Price range: €{prices[0]:,.2f} - €{prices[1]:,.2f} (avg: €{prices[2]:,.2f})")

conn.close()

print("\n" + "="*80)
print(f"VERIFICATION COMPLETE: {exchange_name}")
print("="*80)

"""
Add purchase_fee_total column to fifo_lots table
This allows proportional fee allocation when selling partial lots
"""

import sqlite3

from config import DATABASE_PATH
DB_PATH = DATABASE_PATH


print("="*80)
print("DATABASE SCHEMA UPDATE: Adding purchase_fee_total to fifo_lots")
print("="*80)

conn = sqlite3.connect(DB_PATH)
cursor = conn.cursor()

# Check if column already exists
cursor.execute("PRAGMA table_info(fifo_lots)")
columns = [row[1] for row in cursor.fetchall()]

if 'purchase_fee_total' in columns:
    print("\n✓ Column 'purchase_fee_total' already exists")
else:
    print("\nAdding 'purchase_fee_total' column...")
    cursor.execute("""
        ALTER TABLE fifo_lots 
        ADD COLUMN purchase_fee_total REAL DEFAULT 0
    """)
    conn.commit()
    print("✓ Column added")

# Update existing lots with fee from purchase transaction
print("\nUpdating existing lots with purchase fees...")

cursor.execute("""
    UPDATE fifo_lots
    SET purchase_fee_total = (
        SELECT COALESCE(t.fee_amount, 0)
        FROM transactions t
        WHERE t.id = fifo_lots.purchase_transaction_id
    )
    WHERE purchase_fee_total = 0
""")

updated = cursor.rowcount
conn.commit()

print(f"✓ Updated {updated:,} lots with purchase fees")

# Verify
cursor.execute("""
    SELECT 
        COUNT(*) as total_lots,
        COUNT(CASE WHEN purchase_fee_total > 0 THEN 1 END) as lots_with_fees,
        SUM(purchase_fee_total) as total_fees
    FROM fifo_lots
""")

total, with_fees, fees = cursor.fetchone()
print(f"\nVerification:")
print(f"  Total lots: {total:,}")
print(f"  Lots with fees > 0: {with_fees:,}")
print(f"  Total purchase fees: €{fees:,.2f}")

conn.close()

print("\n" + "="*80)
print("✓ Schema update complete")
print("="*80)

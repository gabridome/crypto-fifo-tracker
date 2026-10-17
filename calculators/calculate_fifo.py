"""
Calculate FIFO matching for all cryptocurrencies
SQLite Database | EUR Currency | Lisbon Timezone
"""

import os
import sys
import time
import traceback

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from crypto_fifo_tracker import CryptoFIFOTracker, swap_dependency_order
from config import DATABASE_PATH, SWAP_REALIZES, tax_rule_banner
DB_PATH = DATABASE_PATH


def format_file_size(size_bytes):
    """Format file size in human-readable format"""
    for unit in ['B', 'KB', 'MB', 'GB']:
        if size_bytes < 1024.0:
            return f"{size_bytes:.2f} {unit}"
        size_bytes /= 1024.0
    return f"{size_bytes:.2f} TB"

def main():
    print("="*70)
    print("CRYPTO FIFO TRACKER - FIFO CALCULATION")
    print("SQLite Database | EUR Currency | Lisbon Timezone")
    print("="*70)
    
    if not os.path.exists(DB_PATH):
        print(f"\n✗ Error: Database file not found: {DB_PATH}")
        print("\nPlease run import_all_data.py first!")
        return 1
    
    print(f"\n[1/4] Connecting to database...")
    tracker = CryptoFIFOTracker(DB_PATH)
    
    db_size = os.path.getsize(DB_PATH)
    print(f"✓ Connected!")
    print(f"  Database: {os.path.abspath(DB_PATH)}")
    print(f"  Size: {format_file_size(db_size)}")
    
    # Get all cryptocurrencies
    print(f"\n[2/4] Detecting cryptocurrencies...")
    tracker.cursor.execute("""
        SELECT DISTINCT cryptocurrency 
        FROM transactions 
        ORDER BY cryptocurrency
    """)
    
    cryptos = [row[0] for row in tracker.cursor.fetchall()]
    
    print(f"✓ Found {len(cryptos)} cryptocurrency(ies): {', '.join(cryptos)}")

    # Only under deferral, where the asset received inherits the lots of the
    # asset given up and therefore has to be computed after it. With
    # swap_realizes True no cryptocurrency depends on another, so the ordering
    # buys nothing — and it would hand the already-declared figures a new way
    # to fail: swapping A for B and later B for A is a cycle, which
    # swap_dependency_order refuses, so one round trip in the data would stop
    # a whole run over a rule that is not in force.
    if not SWAP_REALIZES:
        cryptos = swap_dependency_order(tracker.conn, cryptos)
        print(f"✓ Processing order (swap deferral): {', '.join(cryptos)}")
    
    # Get transaction counts
    print(f"\nTransaction counts by cryptocurrency:")
    for crypto in cryptos:
        tracker.cursor.execute("""
            SELECT 
                COUNT(*) as total,
                SUM(CASE WHEN transaction_type = 'BUY' THEN 1 ELSE 0 END) as buys,
                SUM(CASE WHEN transaction_type = 'SELL' THEN 1 ELSE 0 END) as sells
            FROM transactions 
            WHERE cryptocurrency = ?
        """, (crypto,))
        
        counts = dict(tracker.cursor.fetchone())
        print(f"  {crypto}: {counts['total']:,} total ({counts['buys']:,} buys, {counts['sells']:,} sells)")
    
    # Calculate FIFO for each
    print(f"\n[3/4] Calculating FIFO lots...")
    print("⚠️  This may take 10-20 minutes for large datasets (70k+ transactions)")
    print("☕ Grab a coffee and let it run...\n")
    
    start_time = time.time()
    failures = []

    for idx, crypto in enumerate(cryptos, 1):
        print(f"{'='*70}")
        print(f"[{idx}/{len(cryptos)}] Calculating FIFO for {crypto}...")
        print(f"{'='*70}")
        
        crypto_start = time.time()
        
        try:
            tracker.calculate_fifo_lots(crypto)
            
            crypto_elapsed = time.time() - crypto_start
            print(f"✓ {crypto} FIFO complete in {crypto_elapsed:.1f} seconds")
            
            # Get matching statistics
            tracker.cursor.execute("""
                SELECT 
                    COUNT(DISTINCT sale_transaction_id) as sales_matched,
                    COUNT(*) as total_matches,
                    SUM(CASE WHEN holding_period_days >= 365 THEN 1 ELSE 0 END) as long_term_matches,
                    SUM(CASE WHEN holding_period_days < 365 THEN 1 ELSE 0 END) as short_term_matches
                FROM sale_lot_matches slm
                JOIN transactions t ON slm.sale_transaction_id = t.id
                WHERE t.cryptocurrency = ?
            """, (crypto,))
            
            stats = dict(tracker.cursor.fetchone())
            
            if stats['sales_matched']:
                print(f"\nMatching Statistics:")
                print(f"  Sales matched: {stats['sales_matched']:,}")
                print(f"  Total lot matches: {stats['total_matches']:,}")
                print(f"  Long-term (≥1 year): {stats['long_term_matches']:,}")
                print(f"  Short-term (<1 year): {stats['short_term_matches']:,}")
                
                if stats['long_term_matches'] > 0:
                    pct = (stats['long_term_matches'] / stats['total_matches']) * 100
                    print(f"  Long-term percentage: {pct:.1f}%")
            
        except Exception as e:
            failures.append(crypto)
            print(f"✗ Error calculating FIFO for {crypto}: {e}")
            # On stdout, not stderr: web/app.py:1317 flashes the last 500
            # characters of stdout + stderr, in that order, so anything written
            # to stderr lands after everything on stdout and would push the
            # summary line below out of what the user is shown.
            traceback.print_exc(file=sys.stdout)
    
    total_elapsed = time.time() - start_time
    
    # Summary. The banner is conditional and the run returns a code: the
    # engine refuses rather than guess, the loop above catches per crypto so
    # that one broken cryptocurrency does not stop the others, and a refusal
    # that reaches the caller as `✓ COMPLETE` and exit 0 is indistinguishable
    # from a clean run — web/app.py:1314-1315 flashes `completed successfully` on
    # exit 0. A crypto that was not recalculated keeps whatever fifo_lots
    # already held, which is the previous run's figures.
    print(f"\n[4/4] Summary")
    print("="*70)
    print("✗ FIFO CALCULATION INCOMPLETE" if failures else "✓ FIFO CALCULATION COMPLETE!")
    print(tax_rule_banner())
    print(f"\nTotal time: {total_elapsed:.1f} seconds ({total_elapsed/60:.1f} minutes)")
    
    # Database size after FIFO
    new_db_size = os.path.getsize(DB_PATH)
    print(f"\nDatabase size after FIFO: {format_file_size(new_db_size)}")
    
    if new_db_size > db_size:
        growth = new_db_size - db_size
        print(f"Database grew by: {format_file_size(growth)}")

    tracker.close()

    if failures:
        # Last line of the whole run, deliberately: this is the tail the web
        # app shows, and a single error message can be longer than the 500
        # characters it keeps.
        print("\n" + "="*70)
        print(f"✗ {len(failures)} of {len(cryptos)} cryptocurrencies were NOT calculated: "
              f"{', '.join(failures)}. For those, fifo_lots still holds the figures of "
              "the previous run — do not use them for a tax return. Fix the errors above "
              "and run this script again.")
        return 1

    print("\n" + "="*70)
    print("NEXT STEP:")
    print("="*70)
    print("\nGenerate tax reports:")
    print("  python3 generate_reports.py")
    print()
    return 0

if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n\n⚠️  Calculation interrupted by user")
        print("You can safely run this script again")
        sys.exit(1)
    except Exception as e:
        # Everything that fails outside the per-crypto loop lands here, the
        # dependency ordering included: it runs before the loop, so nothing at
        # all has been calculated — not even the cryptocurrencies unrelated to
        # the failure.
        print(f"\n\n✗ Fatal error: {e}")
        traceback.print_exc(file=sys.stdout)
        print("✗ NOTHING WAS CALCULATED: the run stopped before any cryptocurrency was "
              "processed, so fifo_lots still holds the figures of the previous run — do "
              "not use them for a tax return.")
        sys.exit(1)
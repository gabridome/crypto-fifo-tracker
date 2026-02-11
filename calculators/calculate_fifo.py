"""
Calculate FIFO matching for all cryptocurrencies
SQLite Database | EUR Currency | Lisbon Timezone
"""

from crypto_fifo_tracker import CryptoFIFOTracker
import os
import time

from config import DATABASE_PATH
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
        return
    
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
            print(f"✗ Error calculating FIFO for {crypto}: {e}")
            import traceback
            traceback.print_exc()
    
    total_elapsed = time.time() - start_time
    
    # Summary
    print(f"\n[4/4] Summary")
    print("="*70)
    print(f"✓ FIFO CALCULATION COMPLETE!")
    print(f"\nTotal time: {total_elapsed:.1f} seconds ({total_elapsed/60:.1f} minutes)")
    
    # Database size after FIFO
    new_db_size = os.path.getsize(DB_PATH)
    print(f"\nDatabase size after FIFO: {format_file_size(new_db_size)}")
    
    if new_db_size > db_size:
        growth = new_db_size - db_size
        print(f"Database grew by: {format_file_size(growth)}")
    
    print("\n" + "="*70)
    print("NEXT STEP:")
    print("="*70)
    print("\nGenerate tax reports:")
    print("  python3 generate_reports.py")
    print()
    
    tracker.close()

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n⚠️  Calculation interrupted by user")
        print("You can safely run this script again")
    except Exception as e:
        print(f"\n\n✗ Fatal error: {e}")
        import traceback
        traceback.print_exc()
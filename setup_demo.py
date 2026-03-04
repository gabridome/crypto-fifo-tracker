#!/usr/bin/env python3
"""
Create and populate a demo database for the Crypto FIFO Tracker.

Creates data/DEMO_crypto_fifo.db with sample transactions across 3 fictional
exchanges, then runs FIFO calculation to populate lots and sale matches.

Usage:
    python3 setup_demo.py

Then run the web app pointing at the demo database:
    FIFO_DB=data/DEMO_crypto_fifo.db python3 web/app.py
"""

import os
import sys
import glob
import sqlite3
import subprocess

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
DEMO_DB = os.path.join(PROJECT_ROOT, 'data', 'DEMO_crypto_fifo.db')
SCHEMA_FILE = os.path.join(PROJECT_ROOT, 'doc', 'schema.sql')

# Demo CSV files to import
DEMO_CSVS = sorted(glob.glob(os.path.join(PROJECT_ROOT, 'data', 'DEMO_*.csv')))


def main():
    print("=" * 60)
    print("CRYPTO FIFO TRACKER — DEMO SETUP")
    print("=" * 60)

    # Check prerequisites
    if not os.path.exists(SCHEMA_FILE):
        print(f"\n✗ Schema file not found: {SCHEMA_FILE}")
        sys.exit(1)

    if not DEMO_CSVS:
        print(f"\n✗ No DEMO_*.csv files found in data/")
        sys.exit(1)

    print(f"\nDemo CSV files found:")
    for f in DEMO_CSVS:
        print(f"  {os.path.basename(f)}")

    # Remove old demo DB
    if os.path.exists(DEMO_DB):
        os.remove(DEMO_DB)
        print(f"\n  Removed old demo database")

    # Create schema
    print(f"\n[1/3] Creating database schema...")
    conn = sqlite3.connect(DEMO_DB)
    with open(SCHEMA_FILE, 'r') as f:
        conn.executescript(f.read())
    conn.close()
    print(f"  ✓ {DEMO_DB}")

    # Set env so importers and calculators use the demo DB
    env = os.environ.copy()
    env['FIFO_DB'] = DEMO_DB

    # Import demo CSVs
    print(f"\n[2/3] Importing demo data...")
    importer = os.path.join(PROJECT_ROOT, 'importers', 'import_standard_csv.py')
    total_imported = 0

    for csv_file in DEMO_CSVS:
        basename = os.path.basename(csv_file)
        print(f"\n  Importing {basename}...")
        result = subprocess.run(
            [sys.executable, importer, csv_file],
            cwd=PROJECT_ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode != 0:
            print(f"  ✗ Error importing {basename}:")
            print(result.stderr[-500:] if result.stderr else result.stdout[-500:])
            sys.exit(1)

        # Extract inserted count from output
        for line in result.stdout.splitlines():
            if 'Inserted:' in line or 'inserted' in line.lower():
                print(f"    {line.strip()}")
        total_imported += 1

    print(f"\n  ✓ Imported {total_imported} files")

    # Run FIFO calculation
    print(f"\n[3/3] Calculating FIFO lots...")
    fifo_script = os.path.join(PROJECT_ROOT, 'calculators', 'calculate_fifo.py')
    result = subprocess.run(
        [sys.executable, fifo_script],
        cwd=PROJECT_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    if result.returncode != 0:
        print(f"  ✗ FIFO calculation error:")
        print(result.stderr[-500:] if result.stderr else result.stdout[-500:])
        sys.exit(1)

    # Print FIFO summary from output
    for line in result.stdout.splitlines():
        if any(kw in line for kw in ['Sales matched', 'Total lot matches',
                                      'Long-term', 'Short-term', 'COMPLETE']):
            print(f"    {line.strip()}")

    # Verify
    conn = sqlite3.connect(DEMO_DB)
    tx_count = conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
    lot_count = conn.execute("SELECT COUNT(*) FROM fifo_lots").fetchone()[0]
    match_count = conn.execute("SELECT COUNT(*) FROM sale_lot_matches").fetchone()[0]

    exchanges = conn.execute(
        "SELECT DISTINCT exchange_name FROM transactions ORDER BY exchange_name"
    ).fetchall()

    long_term = conn.execute(
        "SELECT COUNT(*) FROM sale_lot_matches WHERE holding_period_days >= 365"
    ).fetchone()[0]
    short_term = conn.execute(
        "SELECT COUNT(*) FROM sale_lot_matches WHERE holding_period_days < 365"
    ).fetchone()[0]

    conn.close()

    db_size = os.path.getsize(DEMO_DB)

    print(f"\n{'=' * 60}")
    print(f"DEMO DATABASE READY!")
    print(f"{'=' * 60}")
    print(f"\n  Database:     {DEMO_DB}")
    print(f"  Size:         {db_size / 1024:.1f} KB")
    print(f"  Transactions: {tx_count}")
    print(f"  FIFO lots:    {lot_count}")
    print(f"  Sale matches: {match_count} ({long_term} long-term, {short_term} short-term)")
    print(f"  Exchanges:    {', '.join(r[0] for r in exchanges)}")

    print(f"\n  To run the web app with demo data:")
    print(f"  FIFO_DB=data/DEMO_crypto_fifo.db python3 web/app.py")
    print(f"\n{'=' * 60}")


if __name__ == '__main__':
    main()

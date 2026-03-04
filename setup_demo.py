#!/usr/bin/env python3
"""
Create and populate a demo database for the Crypto FIFO Tracker.

Handles everything automatically:
  1. Creates a virtual environment and installs dependencies (if needed)
  2. Generates realistic demo CSV files (900 transactions)
  3. Creates the database schema
  4. Imports demo data
  5. Runs FIFO calculation

Usage:
    python3 setup_demo.py

Then run the web app pointing at the demo database:
    FIFO_DB=data/DEMO_crypto_fifo.db python3 web/app.py
"""

import os
import sys
import subprocess

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
VENV_DIR = os.path.join(PROJECT_ROOT, 'venv')
VENV_PYTHON = os.path.join(VENV_DIR, 'bin', 'python3')
DEMO_DIR = os.path.join(PROJECT_ROOT, 'demo')
DEMO_DB = os.path.join(DEMO_DIR, 'DEMO_crypto_fifo.db')
SCHEMA_FILE = os.path.join(PROJECT_ROOT, 'doc', 'schema.sql')
PACKAGES = ['flask', 'pandas', 'pytz', 'openpyxl', 'requests']


def ensure_venv():
    """Create venv and install dependencies if needed, then re-exec inside it."""
    # Already running inside a virtual environment — nothing to do
    if sys.prefix != sys.base_prefix:
        return

    print("=" * 60)
    print("CRYPTO FIFO TRACKER — ENVIRONMENT SETUP")
    print("=" * 60)

    # Create venv if it doesn't exist
    if not os.path.exists(VENV_PYTHON):
        print("\n  Creating virtual environment...")
        try:
            subprocess.run(
                [sys.executable, '-m', 'venv', VENV_DIR],
                check=True,
            )
        except subprocess.CalledProcessError:
            print("\n  ✗ Failed to create virtual environment.")
            print("    On Debian/Ubuntu you may need: sudo apt install python3-venv")
            sys.exit(1)
        print(f"  ✓ venv created in {VENV_DIR}/")

    # Install/upgrade packages
    print(f"\n  Installing dependencies: {', '.join(PACKAGES)}")
    subprocess.run(
        [VENV_PYTHON, '-m', 'pip', 'install', '-q', '--upgrade'] + PACKAGES,
        check=True,
    )
    print("  ✓ Dependencies installed")

    # Re-exec this script inside the venv
    print(f"\n  Restarting inside virtual environment...\n")
    os.execv(VENV_PYTHON, [VENV_PYTHON] + sys.argv)


def main():
    ensure_venv()

    import glob
    import sqlite3

    print("=" * 60)
    print("CRYPTO FIFO TRACKER — DEMO SETUP")
    print("=" * 60)

    # Check prerequisites
    if not os.path.exists(SCHEMA_FILE):
        print(f"\n✗ Schema file not found: {SCHEMA_FILE}")
        sys.exit(1)

    # Ensure demo directory exists
    os.makedirs(DEMO_DIR, exist_ok=True)

    # Step 1: Generate demo CSV files
    print(f"\n[1/5] Generating demo CSV data...")
    from generate_demo_data import main as generate_main
    generate_main()

    # Create a minimal eurusd.csv so the web app doesn't warn about missing rates.
    # The demo exchanges are all EUR so this file isn't used for calculation,
    # but its presence satisfies the Collect/Status page checks.
    eurusd_path = os.path.join(DEMO_DIR, 'eurusd.csv')
    if not os.path.exists(eurusd_path):
        print(f"  Creating minimal eurusd.csv for demo...")
        # Monthly EUR/USD rates 2016-2025 (approximate historical values)
        monthly_rates = [
            # 2016
            ('2016-01-04', 1.0887), ('2016-04-01', 1.1391), ('2016-07-01', 1.1102),
            ('2016-10-03', 1.1214),
            # 2017
            ('2017-01-02', 1.0471), ('2017-04-03', 1.0666), ('2017-07-03', 1.1412),
            ('2017-10-02', 1.1815),
            # 2018
            ('2018-01-02', 1.2014), ('2018-04-03', 1.2281), ('2018-07-02', 1.1658),
            ('2018-10-01', 1.1573),
            # 2019
            ('2019-01-02', 1.1467), ('2019-04-01', 1.1221), ('2019-07-01', 1.1367),
            ('2019-10-01', 1.0894),
            # 2020
            ('2020-01-02', 1.1195), ('2020-04-01', 1.1023), ('2020-07-01', 1.1237),
            ('2020-10-01', 1.1721),
            # 2021
            ('2021-01-04', 1.2271), ('2021-04-01', 1.1725), ('2021-07-01', 1.1856),
            ('2021-10-01', 1.1579),
            # 2022
            ('2022-01-03', 1.1370), ('2022-04-01', 1.1053), ('2022-07-01', 1.0430),
            ('2022-10-03', 0.9802),
            # 2023
            ('2023-01-02', 1.0666), ('2023-04-03', 1.0883), ('2023-07-03', 1.0907),
            ('2023-10-02', 1.0579),
            # 2024
            ('2024-01-02', 1.1039), ('2024-04-02', 1.0746), ('2024-07-01', 1.0713),
            ('2024-10-01', 1.1133),
            # 2025
            ('2025-01-02', 1.0352), ('2025-04-01', 1.0813), ('2025-07-01', 1.1350),
            ('2025-10-01', 1.1200),
        ]
        with open(eurusd_path, 'w') as f:
            f.write('"DATE","TIME PERIOD","US dollar/Euro (EXR.D.USD.EUR.SP00.A)"\n')
            from datetime import datetime
            for date_str, rate in monthly_rates:
                dt = datetime.strptime(date_str, '%Y-%m-%d')
                time_period = dt.strftime('%d %b %Y')
                f.write(f'"{date_str}","{time_period}","{rate:.4f}"\n')
        print(f"  ✓ {eurusd_path} ({len(monthly_rates)} rates)")

    # Re-scan after generation
    demo_csvs = sorted(glob.glob(os.path.join(DEMO_DIR, 'DEMO_*.csv')))

    if not demo_csvs:
        print(f"\n✗ No DEMO_*.csv files found in demo/")
        sys.exit(1)

    # Remove old demo DB
    if os.path.exists(DEMO_DB):
        os.remove(DEMO_DB)
        print(f"\n  Removed old demo database")

    # Step 2: Create schema
    print(f"\n[2/5] Creating database schema...")
    conn = sqlite3.connect(DEMO_DB)
    with open(SCHEMA_FILE, 'r') as f:
        conn.executescript(f.read())
    conn.close()
    print(f"  ✓ {DEMO_DB}")

    # Set env so importers and calculators use the demo DB
    env = os.environ.copy()
    env['FIFO_DB'] = DEMO_DB

    # Step 3: Import demo CSVs
    print(f"\n[3/5] Importing demo data...")
    importer = os.path.join(PROJECT_ROOT, 'importers', 'import_standard_csv.py')
    total_imported = 0

    for csv_file in demo_csvs:
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

    # Step 4: Run FIFO calculation
    print(f"\n[4/5] Calculating FIFO lots...")
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

    # Step 5: Verify
    print(f"\n[5/5] Verifying...")
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
    print(f"  source venv/bin/activate")
    print(f"  FIFO_DB=demo/DEMO_crypto_fifo.db FIFO_PORT=5003 python3 web/app.py")
    print(f"  Open http://127.0.0.1:5003")
    print(f"\n{'=' * 60}")


if __name__ == '__main__':
    main()

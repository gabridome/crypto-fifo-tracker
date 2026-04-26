"""
Backfill source and record_hash for existing transaction records.

For records that were imported before the source tracking migration,
this script:
  1. Infers 'source' from exchange_name → known CSV file mapping
  2. Computes record_hash for every record

Usage:
    python3 backfill_source_hash.py

Safe to run multiple times — only updates NULL fields.
"""

import sqlite3
import hashlib
import os
import sys
import glob
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, PROJECT_ROOT)

try:
    from config import DATABASE_PATH
except ImportError:
    DATABASE_PATH = os.path.join(PROJECT_ROOT, 'data', 'crypto_fifo.db')

DATA_DIR = os.path.join(PROJECT_ROOT, 'data')


def compute_record_hash(source, date, tx_type, exchange, crypto, amount, value, fee):
    """
    Deterministic SHA256 hash from core record fields.
    Same inputs → same hash, always.
    """
    # Normalize: strip whitespace, fixed decimal precision
    try:
        amount_n = f"{Decimal(str(amount)):.8f}"
    except (ValueError, TypeError, InvalidOperation):
        amount_n = str(amount)
    try:
        value_n = f"{Decimal(str(value)):.2f}"
    except (ValueError, TypeError, InvalidOperation):
        value_n = str(value)
    try:
        if fee is None or str(fee).strip() == '':
            fee_n = "0.00"
        else:
            fee_n = f"{Decimal(str(fee)):.2f}"
    except (ValueError, TypeError, InvalidOperation):
        fee_n = str(fee or 0)

    raw = f"{source or ''}|{date}|{tx_type}|{exchange}|{crypto}|{amount_n}|{value_n}|{fee_n}"
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()


def build_exchange_to_files_map():
    """
    Scan data/ to build exchange_name → [csv_filenames] mapping.
    Uses the same detection logic as the web app.
    """
    EXCHANGE_PATTERNS = [
        (r'binance_card',               'Binance Card'),
        (r'binance_otc',                'Binance OTC'),
        (r'binance_trade_history',      'Binance'),
        (r'binance',                    'Binance'),
        (r'coinbaseprime|coinbase_prime', 'Coinbase Prime'),
        (r'coinbase',                   'Coinbase'),
        (r'bitstamp',                   'Bitstamp'),
        (r'bitfinex',                   'Bitfinex'),
        (r'kraken',                     'Kraken'),
        (r'mtgox|mt_gox',              'Mt.Gox'),
        (r'revolut',                    'Revolut'),
        (r'wirex',                      'Wirex'),
        (r'trt',                        'TRT'),
        (r'changely',                   'changely'),
        (r'coinpal',                    'Coinpal'),
        (r'gdtre',                      'GDTRE'),
        (r'inheritance',                'Inheritance'),
        (r'otc',                        'OTC'),
    ]

    exchange_files = {}
    for filepath in sorted(glob.glob(os.path.join(DATA_DIR, '*.csv'))):
        basename = os.path.basename(filepath)
        if basename in ('eurusd.csv', 'template_manual_transactions.csv') or basename.startswith('sample_'):
            continue

        for pattern, name in EXCHANGE_PATTERNS:
            if re.search(pattern, basename.lower()):
                if name not in exchange_files:
                    exchange_files[name] = []
                exchange_files[name].append(basename)
                break

    return exchange_files


def backfill():
    if not os.path.exists(DATABASE_PATH):
        print(f"✗ Database not found: {DATABASE_PATH}")
        return

    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row

    try:
        # Check columns exist
        cols = [r[1] for r in conn.execute("PRAGMA table_info(transactions)").fetchall()]
        if 'source' not in cols or 'record_hash' not in cols:
            print("✗ Migration not applied yet. Run: python3 migrate_add_source_tracking.py")
            return

        exchange_files = build_exchange_to_files_map()
        print("Exchange → CSV file mapping:")
        for ex, files in sorted(exchange_files.items()):
            print(f"  {ex:25s} → {', '.join(files)}")

        conn.execute("BEGIN")

        # ── Phase 1: Backfill source ──────────────────────────────

        null_source = conn.execute(
            "SELECT COUNT(*) FROM transactions WHERE source IS NULL"
        ).fetchone()[0]

        print(f"\nRecords without source: {null_source:,}")

        if null_source > 0:
            updated_source = 0
            for exchange, files in exchange_files.items():
                if len(files) == 1:
                    # Single file for this exchange: assign directly
                    cursor = conn.execute(
                        "UPDATE transactions SET source = ? WHERE exchange_name = ? AND source IS NULL",
                        (files[0], exchange)
                    )
                    if cursor.rowcount > 0:
                        print(f"  ✓ {exchange:25s} → {files[0]:40s} ({cursor.rowcount:,} records)")
                        updated_source += cursor.rowcount
                else:
                    # Multiple files: we can't be sure which record came from which file
                    # Assign the exchange name as a placeholder
                    combined = f"[{'+'.join(files)}]"
                    cursor = conn.execute(
                        "UPDATE transactions SET source = ? WHERE exchange_name = ? AND source IS NULL",
                        (combined, exchange)
                    )
                    if cursor.rowcount > 0:
                        print(f"  ≈ {exchange:25s} → {combined:40s} ({cursor.rowcount:,} records, multi-file)")
                        updated_source += cursor.rowcount

            # Handle records whose exchange has no CSV file (manual entries, Bybit, etc.)
            cursor = conn.execute(
                "UPDATE transactions SET source = 'manual_entry' WHERE source IS NULL"
            )
            if cursor.rowcount > 0:
                print(f"  · {'(no CSV)':25s} → {'manual_entry':40s} ({cursor.rowcount:,} records)")
                updated_source += cursor.rowcount

            print(f"\n  Source backfill: {updated_source:,} records updated")

        # ── Phase 2: Backfill imported_at ─────────────────────────

        null_imported = conn.execute(
            "SELECT COUNT(*) FROM transactions WHERE imported_at IS NULL"
        ).fetchone()[0]

        if null_imported > 0:
            now = datetime.now().isoformat()
            conn.execute(
                "UPDATE transactions SET imported_at = ? WHERE imported_at IS NULL",
                (now,)
            )
            print(f"  Imported_at backfill: {null_imported:,} records → {now[:19]}")

        # ── Phase 3: Compute record_hash ──────────────────────────

        null_hash = conn.execute(
            "SELECT COUNT(*) FROM transactions WHERE record_hash IS NULL"
        ).fetchone()[0]

        print(f"\nRecords without hash: {null_hash:,}")

        if null_hash > 0:
            rows = conn.execute("""
                SELECT id, source, transaction_date, transaction_type,
                       exchange_name, cryptocurrency, amount, total_value, fee_amount
                FROM transactions
                WHERE record_hash IS NULL
            """).fetchall()

            updated_hash = 0
            for row in rows:
                h = compute_record_hash(
                    row['source'], row['transaction_date'], row['transaction_type'],
                    row['exchange_name'], row['cryptocurrency'],
                    row['amount'], row['total_value'], row['fee_amount']
                )
                conn.execute("UPDATE transactions SET record_hash = ? WHERE id = ?", (h, row['id']))
                updated_hash += 1

            print(f"  Hash backfill: {updated_hash:,} records computed")

        conn.commit()

        # ── Verify ────────────────────────────────────────────────

        print("\n" + "=" * 60)
        print("VERIFICATION")
        print("=" * 60)

        total = conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
        with_source = conn.execute("SELECT COUNT(*) FROM transactions WHERE source IS NOT NULL").fetchone()[0]
        with_hash = conn.execute("SELECT COUNT(*) FROM transactions WHERE record_hash IS NOT NULL").fetchone()[0]
        with_imported = conn.execute("SELECT COUNT(*) FROM transactions WHERE imported_at IS NOT NULL").fetchone()[0]

        print(f"  Total:        {total:,}")
        print(f"  With source:  {with_source:,}  {'✓' if with_source == total else '✗'}")
        print(f"  With hash:    {with_hash:,}  {'✓' if with_hash == total else '✗'}")
        print(f"  With date:    {with_imported:,}  {'✓' if with_imported == total else '✗'}")

        # Check for duplicate hashes
        dupes = conn.execute("""
            SELECT record_hash, COUNT(*) as n
            FROM transactions
            WHERE record_hash IS NOT NULL
            GROUP BY record_hash
            HAVING n > 1
            ORDER BY n DESC
            LIMIT 10
        """).fetchall()

        if dupes:
            print(f"\n  ⚠ Found {len(dupes)} duplicate hash groups:")
            for d in dupes[:5]:
                # Show what the duplicates are
                examples = conn.execute("""
                    SELECT source, exchange_name, transaction_date, transaction_type, amount
                    FROM transactions WHERE record_hash = ?
                """, (d['record_hash'],)).fetchall()
                print(f"    Hash {d['record_hash'][:12]}... ({d['n']}x):")
                for ex in examples:
                    print(f"      {ex['source']:30s} {ex['exchange_name']:15s} {ex['transaction_date'][:19]} {ex['transaction_type']} {ex['amount']}")
        else:
            print(f"\n  ✓ No duplicate hashes found")

        # Source distribution
        print(f"\nSource distribution:")
        sources = conn.execute("""
            SELECT source, COUNT(*) as n
            FROM transactions
            GROUP BY source
            ORDER BY n DESC
        """).fetchall()
        for s in sources:
            print(f"  {s['source']:45s} {s['n']:>7,} records")

    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    print(f"\n✓ Backfill complete")


if __name__ == "__main__":
    print("=" * 60)
    print("  Backfill: source + record_hash for existing records")
    print("=" * 60)
    print()
    backfill()

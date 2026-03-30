"""
Import Utilities — shared by all importers.

Provides:
  - compute_record_hash()  — deterministic SHA256 from record fields
  - delete_by_source()     — surgical delete by source file (not exchange)
  - import_and_verify()    — atomic delete+insert+verify with connection lifecycle
"""

import hashlib
import logging
import sqlite3

logger = logging.getLogger(__name__)


def compute_record_hash(source, date, tx_type, exchange, crypto, amount, value, fee):
    """
    Deterministic SHA256 hash from core record fields.
    Same inputs → same hash, always.
    """
    try:
        amount_n = f"{float(amount):.8f}"
    except (ValueError, TypeError):
        amount_n = str(amount)
    try:
        value_n = f"{float(value):.2f}"
    except (ValueError, TypeError):
        value_n = str(value)
    try:
        fee_n = f"{float(fee):.2f}"
    except (ValueError, TypeError):
        fee_n = str(fee or 0)

    raw = f"{source or ''}|{date}|{tx_type}|{exchange}|{crypto}|{amount_n}|{value_n}|{fee_n}"
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()


def delete_by_source(conn, source_filename):
    """
    Delete all transactions originating from a specific CSV file.
    Returns the number of deleted records.
    """
    cursor = conn.execute(
        "DELETE FROM transactions WHERE source = ?",
        (source_filename,)
    )
    return cursor.rowcount


def import_and_verify(db_path, source, insert_fn, group_by_crypto=False):
    """
    Atomic delete+insert+verify with proper connection lifecycle.

    Args:
        db_path: path to SQLite database
        source: source filename for delete_by_source and verification
        insert_fn: callable(conn) that performs all INSERTs and returns count
        group_by_crypto: if True, verification groups by cryptocurrency too

    Returns:
        number of inserted records

    The entire delete+insert is wrapped in a single transaction.
    On error, the transaction is rolled back and the connection is closed.
    """
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("BEGIN")

        deleted = delete_by_source(conn, source)
        logger.info("Deleted %d previous records for %s", deleted, source)
        print(f"\n  Deleted {deleted} previous records for {source}")

        inserted = insert_fn(conn)

        conn.commit()
        logger.info("Inserted %d transactions from %s", inserted, source)
        print(f"  Inserted: {inserted:,} transactions")

        # Verification query (read-only, after commit)
        group_cols = "transaction_type, cryptocurrency" if group_by_crypto else "transaction_type"
        cursor = conn.execute(f"""
            SELECT
                {group_cols},
                COUNT(*) as count,
                SUM(amount) as total_crypto,
                SUM(total_value) as total_eur,
                SUM(fee_amount) as total_fees
            FROM transactions
            WHERE source = ?
            GROUP BY {group_cols}
        """, (source,))

        print("\n" + "=" * 80)
        print("VERIFICATION")
        print("=" * 80)

        for row in cursor.fetchall():
            if group_by_crypto:
                tx_type, crypto, count, amt, eur, fees = row
                print(f"\n{tx_type} ({crypto}):")
            else:
                tx_type, count, amt, eur, fees = row
                print(f"\n{tx_type}:")
            print(f"  Transactions: {count:,}")
            print(f"  Amount: {amt:.8f}")
            print(f"  EUR: {eur:,.2f}")
            print(f"  Fees: {fees:.2f}")

        print("\n" + "=" * 80)
        print("SUCCESS!")

        return inserted

    except Exception:
        conn.rollback()
        logger.error("Import failed for %s, rolled back", source, exc_info=True)
        raise
    finally:
        conn.close()

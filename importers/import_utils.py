"""
Import Utilities — shared by all importers.

Provides:
  - compute_record_hash()  — deterministic SHA256 from record fields
  - post_import_update()   — set source/imported_at/record_hash on NULL records
  - delete_by_source()     — surgical delete by source file (not exchange)
"""

import hashlib
import sqlite3
from datetime import datetime


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


def post_import_update(db_path, source_filename, exchange_name=None):
    """
    After an importer runs, update records that have NULL source/hash.
    This is the bridge for importers that haven't been updated yet:
    they INSERT without source/hash, and this function fills them in.

    Args:
        db_path: path to SQLite database
        source_filename: CSV filename to assign as source
        exchange_name: if set, only update records for this exchange
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    now = datetime.now().isoformat()

    # Set source and imported_at on NULL records
    if exchange_name:
        conn.execute(
            "UPDATE transactions SET source = ?, imported_at = ? "
            "WHERE exchange_name = ? AND source IS NULL",
            (source_filename, now, exchange_name)
        )
    else:
        conn.execute(
            "UPDATE transactions SET source = ?, imported_at = ? "
            "WHERE source IS NULL",
            (source_filename, now)
        )

    # Compute hash for records without one
    rows = conn.execute("""
        SELECT id, source, transaction_date, transaction_type,
               exchange_name, cryptocurrency, amount, total_value, fee_amount
        FROM transactions
        WHERE record_hash IS NULL
    """).fetchall()

    for row in rows:
        h = compute_record_hash(
            row['source'], row['transaction_date'], row['transaction_type'],
            row['exchange_name'], row['cryptocurrency'],
            row['amount'], row['total_value'], row['fee_amount']
        )
        conn.execute("UPDATE transactions SET record_hash = ? WHERE id = ?", (h, row['id']))

    conn.commit()
    updated = len(rows)
    conn.close()
    return updated

"""
Import Utilities — shared by all importers.

Provides:
  - compute_record_hash()  — deterministic SHA256 from record fields
  - delete_by_source()     — surgical delete by source file (not exchange)
"""

import hashlib


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

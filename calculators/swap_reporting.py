"""Deferred swap disposals — the rows a reader of `sale_lot_matches` cannot see.

`sale_lot_matches` is the register of **realized gains**, not the register of
disposals. Under `tax_rules.swap_realizes = False` the SELL leg of a
crypto-to-crypto swap consumes lots and writes no match row at all: the
acquisition date and the cost of those lots travel to the asset received
through `swap_lot_transfers` instead (the `deferred_sale` branch of
`calculators/crypto_fifo_tracker.py`). That is correct — a deferred permuta is
not a taxable event — but it means every report that reads `sale_lot_matches`
as "the disposals" is short by exactly these rows, while every report that
reads `transactions` counts them at their full value.

This module holds the one definition those reports share, so that the wording
can differ per report and the definition cannot.

**Read from the database, never from `config.SWAP_REALIZES`.** A report is read
next to figures that are already in the database, and those were calculated
under whatever rule was in force when the engine last ran — not necessarily the
one `config.py` carries at the moment the report is printed.

**Positive evidence, not a missing row.** "A swap SELL with no match row" is
true of every swap leg in a database whose FIFO has never been run, so counting
that alone would tell the reader of an uncalculated database that its figures
exclude deferred disposals — a false explanation of why they are missing. A
disposal that actually deferred has rows in `swap_lot_transfers` for its swap
group; the engine refuses a deferred disposal it cannot cover with lots, so
there is no completed deferral without them.
"""

ZERO = {'count': 0, 'amount': 0.0, 'total_value': 0.0}

_SQL = """
    SELECT COUNT(*),
           COALESCE(SUM(t.amount), 0),
           COALESCE(SUM(t.total_value), 0)
    FROM transactions t
    WHERE t.transaction_type = 'SELL'
      AND t.swap_group_id IS NOT NULL
      AND t.swap_group_id <> ''
      AND NOT EXISTS (SELECT 1 FROM sale_lot_matches m
                      WHERE m.sale_transaction_id = t.id)
      AND EXISTS (SELECT 1 FROM swap_lot_transfers s
                  WHERE s.swap_group_id = t.swap_group_id)
"""


def _has_table(conn, table):
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone() is not None


def _has_column(conn, table, column):
    return column in [r[1] for r in conn.execute(f'PRAGMA table_info({table})')]


def deferred_disposals(conn, cryptocurrency=None, year=None):
    """How many disposals deferred, and the quantity and value they carry.

    Returns `{'count': int, 'amount': float, 'total_value': float}`, with
    `amount` and `total_value` taken from `transactions` — the same columns the
    `transactions`-based sections of a report sum, so the two agree by
    construction.

    `cryptocurrency` narrows to the asset **given up**. `year` narrows on
    `transaction_date`, the same calendar year a report is asked for.

    A database older than the `swap_group_id` or `swap_lot_transfers`
    migrations answers zero rather than raising: it cannot hold a deferred
    disposal, so zero is the true answer and not a swallowed error. Reports run
    on old databases and must still print.

    Rows are read positionally, so nothing here depends on the caller having
    set `row_factory` (same reason as `swap_dependency_order`).
    """
    if not _has_table(conn, 'swap_lot_transfers'):
        return dict(ZERO)
    if not _has_column(conn, 'transactions', 'swap_group_id'):
        return dict(ZERO)

    sql = _SQL
    params = []
    if cryptocurrency is not None:
        sql += '      AND t.cryptocurrency = ?\n'
        params.append(cryptocurrency)
    if year is not None:
        sql += '      AND t.transaction_date >= ? AND t.transaction_date < ?\n'
        params.extend([f'{int(year)}-01-01', f'{int(year) + 1}-01-01'])

    cur = conn.execute(sql, params)
    try:
        count, amount, total_value = cur.fetchone()
    finally:
        cur.close()
    return {'count': count, 'amount': float(amount), 'total_value': float(total_value)}

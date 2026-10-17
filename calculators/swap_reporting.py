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

**Both directions.** `deferred_disposals` and `realized_swap_disposals` are a
pair, and each is evidence of the rule that wrote it: only a
`swap_realizes = False` run defers a swap disposal, only a `True` run realizes
one. `config.tax_rule_banner()` compares the configured setting against both,
because the dangerous state is symmetric — figures from a `False` run under a
setting now `True`, and figures from a `True` run under a setting now `False`.
The engine's DELETE is per cryptocurrency, so a database recalculated for only
some assets after a change of the setting holds both at once.
"""

ZERO = {'count': 0, 'amount': 0.0, 'total_value': 0.0}

# Every swap disposal, before either kind of evidence narrows it down.
_SWAP_DISPOSALS = """
    SELECT COUNT(*),
           COALESCE(SUM(t.amount), 0),
           COALESCE(SUM(t.total_value), 0)
    FROM transactions t
    WHERE t.transaction_type = 'SELL'
      AND t.swap_group_id IS NOT NULL
      AND t.swap_group_id <> ''
"""

# It deferred: no row in the register of realized gains, and transfers on
# record for its group — which only a swap_realizes = False run writes.
_DEFERRED = """      AND NOT EXISTS (SELECT 1 FROM sale_lot_matches m
                      WHERE m.sale_transaction_id = t.id)
      AND EXISTS (SELECT 1 FROM swap_lot_transfers s
                  WHERE s.swap_group_id = t.swap_group_id)
"""

# It realized: a row in that register — which only a swap_realizes = True run
# writes for a swap leg. The mirror image, and the evidence that figures in
# the database came from the declared rule.
_REALIZED = """      AND EXISTS (SELECT 1 FROM sale_lot_matches m
                  WHERE m.sale_transaction_id = t.id)
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
    """
    # Only this half needs the table: the query joins it for its evidence, and
    # without it no run under deferral was ever possible — the engine refuses
    # that combination before it writes anything.
    if not _has_table(conn, 'swap_lot_transfers'):
        return dict(ZERO)
    return _count(conn, _DEFERRED, cryptocurrency, year)


def realized_swap_disposals(conn, cryptocurrency=None, year=None):
    """The same question from the other side: swap disposals that realized.

    Evidence that figures in the database came from a `swap_realizes = True`
    run, in the same shape as `deferred_disposals`. The two are the pair
    `config.tax_rule_banner()` compares the configured setting against: either
    one, found where the setting says it should not be, means the figures were
    produced under the other rule and the setting changed afterwards.

    Not symmetric by accident — the engine's DELETE is per cryptocurrency, so a
    database recalculated for only some assets after a change of the setting
    carries both kinds at once, and a guard covering one direction would pass
    it in silence.
    """
    return _count(conn, _REALIZED, cryptocurrency, year)


def _count(conn, evidence, cryptocurrency=None, year=None):
    """Shared body. Rows are read positionally, so nothing here depends on the
    caller having set `row_factory` (same reason as `swap_dependency_order`).

    The `swap_group_id` column is the one prerequisite both halves share:
    without it a row cannot be identified as a swap leg at all, so zero is the
    true answer. The `swap_lot_transfers` table is a prerequisite of the
    deferred half only, and is checked there — checking it here would answer
    zero for the realized half on a database that predates the migration,
    hiding the very disagreement the banner exists to name.
    """
    if not _has_column(conn, 'transactions', 'swap_group_id'):
        return dict(ZERO)

    sql = _SWAP_DISPOSALS + evidence
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

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

**Three kinds of evidence, and what they establish together.** Each is a trace
of the rule that wrote it: only a `swap_realizes = False` run defers a swap
disposal (`deferred_disposals`) or builds a lot that inherited its acquisition
date and cost (`inherited_lots`); only a `True` run realizes a swap disposal
(`realized_swap_disposals`). Found together, all three clean, they establish
that **no disposal and no inherited lot on record contradicts the setting
named** — which is what `config.tax_rule_banner()` asserts, and no more than
that.

Two disposal evidences are not enough on their own, and this was measured, not
reasoned: both look only at the leg that gives up. An asset *received* and left
unrecalculated after a change of the setting keeps its inherited lots in
`fifo_lots`, and the engine's `DELETE FROM swap_lot_transfers` — which runs
under `True` as well — takes away the last standing trace the moment the give
side is recomputed. `inherited_lots` is what sees that state.

**The limit of the third evidence, stated.** It recognises an inherited lot by
its acquisition date differing from the date of the swap's BUY leg. A swap that
happened on the same day as the purchase it consumed inherits that same day, so
the two dates coincide and the lot is indistinguishable from an ordinary one.
The evidence is therefore **sufficient and not necessary**: finding one proves
a deferred run wrote it, finding none does not prove none exists.
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
# record for its group FROM THIS LEG'S OWN ASSET — which only a
# swap_realizes = False run writes.
#
# `s.from_cryptocurrency = t.cryptocurrency` is defensive, not a live fix: on
# the group alone, a group carrying two SELL legs in different assets would
# report the second as deferred on the first one's transfer. The importer
# cannot produce that — verified upstream 2026-10-17: value_row emits exactly
# one SELL and one BUY per normalized row, row_ref is unique per row and folded
# into the group id, and no other file writes the column. Kept because the
# alternative is a count that is wrong without being loud.
_DEFERRED = """      AND NOT EXISTS (SELECT 1 FROM sale_lot_matches m
                      WHERE m.sale_transaction_id = t.id)
      AND EXISTS (SELECT 1 FROM swap_lot_transfers s
                  WHERE s.swap_group_id = t.swap_group_id
                    AND s.from_cryptocurrency = t.cryptocurrency)
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

    Where this one is found under a setting of `False`, it establishes that the
    rows it counts came from a `True` run: the engine writes a match row for a
    swap leg on that path only.
    """
    return _count(conn, _REALIZED, cryptocurrency, year)


# A lot whose acquisition came from a swap's BUY leg but whose purchase_date is
# not that transaction's date: the engine writes that only on the deferral path,
# where the date travels from the lot consumed on the other side. See the limit
# in this module's docstring.
_INHERITED_LOTS = """
    SELECT COUNT(*), COALESCE(SUM(l.original_amount), 0)
    FROM fifo_lots l
    JOIN transactions t ON l.purchase_transaction_id = t.id
    WHERE t.transaction_type = 'BUY'
      AND t.swap_group_id IS NOT NULL AND t.swap_group_id <> ''
      AND l.purchase_date <> t.transaction_date
"""


def inherited_lots(conn):
    """Lots that took their acquisition date and cost across a deferred swap.

    The evidence the two disposal counts cannot give, because it lives on the
    asset **received**: `{'count': int, 'amount': float}`.

    No `cryptocurrency` or `year` filter, deliberately. The asset here is the
    one received, not the one given up, so the same argument name would mean the
    opposite of what it means in the other two functions — and a lot has no
    disposal year to filter on. The only caller needs the count.

    `amount` sums `original_amount` across whatever cryptocurrencies inherited,
    so it is **not a quantity in any unit** and must not be printed aggregated.
    It is here for a caller that has narrowed to one asset itself.
    """
    if not _has_column(conn, 'transactions', 'swap_group_id'):
        return {'count': 0, 'amount': 0.0}
    if not _has_table(conn, 'fifo_lots'):
        return {'count': 0, 'amount': 0.0}

    cur = conn.execute(_INHERITED_LOTS)
    try:
        count, amount = cur.fetchone()
    finally:
        cur.close()
    return {'count': count, 'amount': float(amount)}


def _count(conn, evidence, cryptocurrency=None, year=None):
    """Shared body. Rows are read positionally, so nothing here depends on the
    caller having set `row_factory` (same reason as `swap_dependency_order`).

    The `swap_group_id` column is the one prerequisite both halves share:
    without it a row cannot be identified as a swap leg at all, so zero is the
    true answer. The `swap_lot_transfers` table is a prerequisite of the
    deferred half only, and is checked there — checking it here would answer
    zero for the realized half on a database that predates the migration,
    hiding the very disagreement the banner exists to name.

    `sale_lot_matches` is the other shared prerequisite: both evidences read it,
    and without it neither a realized nor a deferred disposal can be on record,
    so zero is again the true answer. Unguarded, a report would raise on its
    first line instead of printing.
    """
    if not _has_column(conn, 'transactions', 'swap_group_id'):
        return dict(ZERO)
    if not _has_table(conn, 'sale_lot_matches'):
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

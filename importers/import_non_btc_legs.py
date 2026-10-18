#!/usr/bin/env python3
"""Import the non-BTC legs of an exchange export.

Additive by design: it writes only non-BTC rows, under a source tag of its own
(`<filename>#non-btc`), so re-importing the legs can never delete the BTC rows
that another importer wrote from the same file.

Usage:
    python3 importers/import_non_btc_legs.py <filepath> <shape> [exchange_name]

    shape: trade_history | account_history | deposit_withdrawal
"""
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import DATABASE_PATH
from importers.crypto_prices import CryptoPrices
from importers.ecb_rates import ECBRates
from importers.import_utils import compute_record_hash, import_and_verify
from importers.non_btc_readers import (
    read_account_history,
    read_deposit_withdrawal,
    read_trade_history,
)
from importers.non_btc_valuation import value_row

SOURCE_SUFFIX = '#non-btc'

READERS = {
    'trade_history': read_trade_history,
    'account_history': read_account_history,
    'deposit_withdrawal': read_deposit_withdrawal,
}


class Rates:
    """The project's rate sources behind the interface valuation expects.

    A missing rate aborts the import for crypto prices, and for any fiat
    other than EUR/USD (doc/project_guidelines.md §Conversione valute): a
    leg valued with a guessed rate is worse than no leg. USD is the one
    exception, and it is pre-existing behaviour this class inherits rather
    than a guarantee it makes itself: `ECBRates.get_rate()` falls back to
    the nearest available date (with only a print, not a raise) when the
    exact day's rate is missing. A future reader should not assume "aborts
    on any missing rate" covers that path too.
    """

    def __init__(self):
        self._ecb = ECBRates()
        self._crypto = CryptoPrices()

    def fiat_to_eur(self, amount, currency, when):
        """Keep these parameter names: value_row calls by keyword on purpose.

        The plan once had the crypto call's arguments swapped, and only the
        test double's parameter names turned it into a TypeError instead of a
        silently wrong figure (task 5 review). Renaming a parameter here
        breaks the call loudly, which is the point.
        """
        if currency == 'EUR':
            return amount
        if currency != 'USD':
            raise ValueError(
                f'no rate source for fiat {currency}: add one before importing '
                f'an export denominated in it rather than treating it as EUR '
                f'(doc/project_guidelines.md §Conversione valute)')
        return self._ecb.usd_to_eur(amount, datetime.fromisoformat(when))

    def crypto_to_eur(self, coin, amount, when):
        value = self._crypto.crypto_to_eur(coin, amount, datetime.fromisoformat(when))
        if value is None:
            raise ValueError(f'no price for {coin} on {when}: import aborted')
        return value


def _print_convert_warning(converts):
    """Announce every withdrawal the reader recorded as a sale.

    A withdrawal export cannot tell a real sale from a transfer that merely
    carries a valuation (see non_btc_readers.read_account_history); printing
    the list is the only safeguard against silently taxing a transfer, or
    silently missing a real sale. Kept separate from the zero-proceeds
    warning below even though both iterate `rows`, because they answer two
    different questions a human checks against two different things: this
    one against the receiving account, the other against "was this really
    just a transfer".
    """
    print(f'  ⚠ {len(converts)} withdrawal row(s) recorded as a SALE — confirm each '
          f'against the receiving account:', file=sys.stderr)
    for r in converts:
        print(f"      {r['when'][:10]}  {r['give_amount']:.8f} {r['give_asset']} "
              f"→ {r['take_amount']:.2f} {r['take_asset']}", file=sys.stderr)


def _print_zero_proceeds_warning(zero_out):
    """Announce every outgoing transfer the import recorded as non-taxable.

    This lists the *whole* class, not a filtered-down set of doubtful rows —
    on a real export that can run to dozens of lines, most of them a plain
    'Send' or an ordinary 'Withdraw' that was never ambiguous. It cannot be
    narrower: which export label produced a given TRANSFER_OUT (a plain
    send vs. a withdrawal whose fiat subtotal happened to come back blank)
    is a fact of the reader's classification, not of the normalized row
    this function receives — non_btc_readers deliberately does not carry
    that provenance into `kind`, and widening that contract just for this
    warning is not worth it. So the header must say what the list actually
    is (everything in this category) rather than implying a shortlist of
    suspects it cannot produce.
    """
    print(f'  ⚠ {len(zero_out)} outgoing transfer(s) recorded as non-taxable — none of these '
          f'produced a disposal; confirm that is right:', file=sys.stderr)
    for r in zero_out:
        print(f"      {r['when'][:10]}  {r['give_amount']:.8f} {r['give_asset']} out",
              file=sys.stderr)


def import_non_btc_legs(filepath, exchange_name, shape, db_path=None, rates=None):
    """Read one export, write its non-BTC legs. Returns the number of rows.

    `rates` is a seam, not an optional nicety: the real `Rates()` eagerly
    constructs `ECBRates()` and `CryptoPrices()`, which read CSV caches by a
    path relative to the working directory and may reach out to the network
    when that cache looks stale. A unit test must not depend on either, so
    the real source is built only when the caller does not supply one —
    production use (the CLI entry point below) leaves it as the default;
    tests pass a fake with `fiat_to_eur`/`crypto_to_eur`.
    """
    if shape not in READERS:
        raise ValueError(f'unknown shape {shape!r}, expected one of {sorted(READERS)}')

    source = os.path.basename(filepath) + SOURCE_SUFFIX
    imported_at = datetime.now().isoformat()
    rates = rates if rates is not None else Rates()

    with open(filepath, encoding='utf-8-sig') as fh:
        rows = list(READERS[shape](fh, exchange_name))

    # row_ref must be unique per row: value_row folds it into the swap id, and
    # the real export has 74 crypto-to-crypto trades sharing one timestamp with
    # no order id, two pairs of which are identical on both quantities. Without
    # it, distinct swaps would share an inheritance key (task 5 refuses a swap
    # with a blank row_ref rather than minting a colliding id).
    #
    # Each row is valued on its own, and a failure is re-raised naming the
    # row (its index and when/kind — never an amount, per §3.3) rather than
    # just the figures _apply_fee already names. "fee of 5.0 with no asset"
    # does not localize anything on a real export where several rows share
    # that amount; the row context is cheap to add here and wrong to add
    # inside the pure valuation layer, which has no concept of "which row in
    # this import".
    txs = []
    for i, row in enumerate(rows):
        try:
            row_txs = list(value_row(row, rates, row_ref=f'{source}#{i}'))
        except ValueError as exc:
            raise ValueError(f'row {i} ({row["when"]}, {row["kind"]}): {exc}') from exc
        txs.extend(tx for tx in row_txs if tx['cryptocurrency'] != 'BTC')

    print(f'  {len(rows)} rows read, {len(txs)} non-BTC legs to write')

    converts = [r for r in rows if r['kind'] == 'CONVERT']
    if converts:
        _print_convert_warning(converts)

    zero_out = [r for r in rows
                if r['kind'] == 'TRANSFER_OUT' and r['give_amount'] > 0 and not r['take_asset']]
    if zero_out:
        _print_zero_proceeds_warning(zero_out)

    def do_inserts(conn):
        cursor = conn.cursor()
        for tx in txs:
            cursor.execute("""
                INSERT INTO transactions (
                    transaction_date, transaction_type, exchange_name, cryptocurrency,
                    amount, price_per_unit, total_value, fee_amount, fee_currency,
                    currency, transaction_id, notes, source, imported_at, record_hash,
                    swap_group_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                tx['transaction_date'], tx['transaction_type'], tx['exchange_name'],
                tx['cryptocurrency'], tx['amount'], tx['price_per_unit'],
                tx['total_value'],
                # Always 0.0, never the export's own fee column: value_row has
                # already folded any real fee into `amount` or `total_value`
                # above (see non_btc_valuation._tx). Writing the raw fee here
                # too would double-count it — once inside the quantity or the
                # cost, once again as a fee of its own.
                tx['fee_amount'],
                # Every figure above is already converted to EUR by value_row
                # (doc/project_guidelines.md §Conversione valute), so the
                # stored value genuinely is in EUR — this is not a mislabel.
                # What is lost is which fiat the row was originally
                # denominated in (EUR or USD): the same tradeoff every other
                # importer in this project already makes (e.g.
                # import_mtgox_with_fees.py keeps `original_currency` only
                # for its console sample, never for the DB). `notes` at least
                # keeps the row's kind, so an /audit trace is not purely a
                # bare EUR figure with no provenance.
                'EUR', 'EUR',
                tx['transaction_id'],
                f"{tx['kind']} leg from {os.path.basename(filepath)}",
                source, imported_at,
                compute_record_hash(source, tx['transaction_date'], tx['transaction_type'],
                                    tx['exchange_name'], tx['cryptocurrency'],
                                    tx['amount'], tx['total_value'], tx['fee_amount']),
                tx['swap_group_id'],
            ))
        return len(txs)

    return import_and_verify(db_path or DATABASE_PATH, source, do_inserts)


if __name__ == '__main__':
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)
    if len(sys.argv) > 3:
        cli_exchange_name = sys.argv[3]
    else:
        cli_exchange_name = 'Unknown'
        print("  note: no exchange_name given — tagging every row 'Unknown' "
              "(pass it as a third argument to avoid this)", file=sys.stderr)
    import_non_btc_legs(sys.argv[1], cli_exchange_name, sys.argv[2])

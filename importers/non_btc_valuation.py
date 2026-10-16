"""Valuation rules for non-BTC legs (spec §5).

Pure: a normalized row plus a rate source in, transaction dicts out. The rate
source is injected so tests do not need the network and so the caller decides
whether a missing rate is fatal (it is: see import_non_btc_legs).
"""
import hashlib
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from calculators.crypto_fifo_tracker import DUST_THRESHOLD

FIAT = ('EUR', 'USD', 'GBP', 'CHF')
DUST = float(DUST_THRESHOLD)


def _swap_id(row, row_ref):
    """Stable id for the two legs of one swap: same inputs, same id, always.

    `row_ref` is the caller's own uniqueifier for this row (e.g.
    ``f'{source}#{index}'``) — not a field of the normalized row itself. It
    is mandatory for a crypto-to-crypto swap (see the refusal in
    `value_row`): a real export this was checked against has 74
    crypto-to-crypto trades sharing one second-resolution timestamp, no
    order-id column at all (so `ref` is blank on every one of them), and two
    pairs of rows with identical quantities on both sides. Every field this
    function could otherwise hash agrees across those rows, so hashing them
    alone collapses four distinct swaps onto two shared ids — a silently
    shared lot-inheritance key once `swap_group_id` starts carrying cost
    basis across a deferred swap (tasks 8+). `row_ref` is the one thing
    guaranteed to differ row to row, so it is folded into the hash too.
    """
    raw = (f"{row['when']}|{row['exchange']}|{row['give_asset']}|{row['give_amount']}"
           f"|{row['take_asset']}|{row['take_amount']}|{row['ref']}|{row_ref}")
    return 'swap-' + hashlib.sha256(raw.encode('utf-8')).hexdigest()[:16]


def _tx(row, tx_type, crypto, amount, total_value, swap_group_id=None):
    return {
        'transaction_date': row['when'],
        'transaction_type': tx_type,
        'cryptocurrency': crypto,
        'amount': amount,
        'price_per_unit': (total_value / amount) if amount else 0.0,
        'total_value': total_value,
        # Always 0.0: any fee this function could act on has already been
        # folded into `amount` (taken out of what arrived) or into
        # `total_value` (added to what the purchase cost) below. Writing the
        # export's real fee into this column too would double-count it —
        # once inside the quantity or the cost, once again as a fee. Task 6
        # must write this field as-is, not "restore" the original fee.
        'fee_amount': 0.0,
        'exchange_name': row['exchange'],
        'transaction_id': row['ref'],
        'swap_group_id': swap_group_id,
    }


def value_row(row, rates, row_ref=''):
    """Return the transactions a normalized row produces: 0, 1 or 2.

    `row_ref` identifies this row uniquely within its import (its position
    in the file is enough, e.g. ``f'{source}#{index}'``). It is required —
    and this function refuses without it — only for a crypto-to-crypto swap,
    the one case that produces a `swap_group_id` two legs must share: see
    `_swap_id` for why a shared id is a real risk on real data. A single-leg
    BUY/SELL never gets a `swap_group_id`, so `row_ref` is irrelevant there
    and may be omitted.
    """
    if row['kind'] in ('TRANSFER_IN', 'TRANSFER_OUT'):
        return []  # moving your own asset is neither acquisition nor disposal

    give, take = row['give_asset'], row['take_asset']
    give_amt, take_amt = row['give_amount'], row['take_amount']
    if max(give_amt, take_amt) <= DUST:
        return []

    give_is_fiat, take_is_fiat = give in FIAT, take in FIAT

    # Where the fee sits decides what it does. Taken out of what arrives, it
    # is already missing from the account, so it reduces the quantity acquired.
    # Charged on what you hand over, it is part of what the purchase cost. Paid
    # in a third asset it cannot be allocated without valuing that asset too,
    # which this function has no mandate to do: it refuses.
    fee_asset, fee_amt = row['fee_asset'], row['fee_amount']
    if fee_amt > DUST and not fee_asset:
        raise ValueError(f'fee of {fee_amt} with no asset: cannot tell what was charged')
    if fee_amt > DUST and fee_asset not in (give, take):
        raise ValueError(
            f'fee charged in {fee_asset}, which is neither leg ({give}/{take}): '
            f'allocating it would need a price for {fee_asset} too'
        )
    if fee_amt > DUST and fee_asset == take:
        take_amt -= fee_amt
        if take_amt <= DUST:
            # The row survives the dust check above (it tests the *gross*
            # amounts), but a fee this large leaves ~nothing of the leg it
            # was taken from. Emitting it would attach a real, non-zero cost
            # to a lot of ~zero quantity: a price_per_unit this function
            # cannot report honestly, and a lot FIFO can never fully
            # dispose of downstream. An export where the fee swallows the
            # whole leg is an inconsistency to stop on, not a number to
            # round away.
            raise ValueError(
                f'fee of {fee_amt} {fee_asset} consumes the whole {take} leg '
                f'({row["take_amount"]} {take}): nothing would be left to value'
            )
    give_total = give_amt + (fee_amt if fee_amt > DUST and fee_asset == give else 0.0)

    if give_is_fiat and not take_is_fiat:
        value = rates.fiat_to_eur(give_total, give, row['when'])
        return [_tx(row, 'BUY', take, take_amt, value)]

    if take_is_fiat and not give_is_fiat:
        # The fee, if charged in the fiat received, is already out of take_amt.
        value = rates.fiat_to_eur(take_amt, take, row['when'])
        return [_tx(row, 'SELL', give, give_amt, value)]

    if not give_is_fiat and not take_is_fiat:
        if not row_ref:
            raise ValueError(
                'crypto-to-crypto swap needs a row_ref to build a unique swap_group_id: '
                'same-second timestamps and a blank ref column collide on real exports, '
                'so this refuses rather than risk sharing one id between two swaps'
            )
        value = rates.crypto_to_eur(give, give_total, row['when'])
        group = _swap_id(row, row_ref)
        return [
            _tx(row, 'SELL', give, give_amt, value, group),
            _tx(row, 'BUY', take, take_amt, value, group),
        ]

    return []  # fiat ↔ fiat is not a crypto leg

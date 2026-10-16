"""Readers for the non-BTC legs of exchange exports.

Each reader takes an open text file and yields normalized rows (the contract is
documented in doc/plans/2026-10-16-non-btc-legs.md). Pure functions: no DB, no
network, no config. Unknown shapes raise ValueError — a leg guessed wrong is
worse than an import that stops.
"""
import csv
import re
from datetime import datetime, timezone

# Assets that may appear as the quote side of a pair, e.g. 'BTCUSDT' -> BTC/USDT.
# Matched longest-first (see split_pair) so that 'BTCUSDT' splits into
# BTC/USDT and not BTC/USD + 'T', and 'XBUSD' splits into X/BUSD and not
# X-minus-last-letter/USD. The list itself need not be pre-sorted.
KNOWN_QUOTES = ('USDT', 'USDC', 'BUSD', 'EUR', 'USD', 'BTC', 'ETH')

VALID_SIDES = ('BUY', 'SELL')

_AMOUNT_RE = re.compile(r'^\s*(-?\d+(?:\.\d+)?)\s*([A-Za-z]*)\s*$')


def _utc(value, fmt='%Y-%m-%d %H:%M:%S'):
    """Export timestamps are UTC without offset; emit explicit ISO 8601 UTC.

    Stamps the given UTC wall-clock time with ``.replace(tzinfo=...)`` rather
    than converting it. Every format seen so far carries no offset, so this is
    safe; a future format parsed with ``%z`` would need its offset applied
    (e.g. via ``astimezone``), not discarded by this replace.
    """
    return datetime.strptime(value.strip(), fmt).replace(tzinfo=timezone.utc).isoformat()


def split_amount(raw):
    """'0.0024BTC' → (0.0024, 'BTC'); '110.00' → (110.0, '').

    Always returns a positive amount regardless of the sign in `raw` — a
    caller that needs the sign (e.g. to tell a deposit from a withdrawal)
    must read it from `raw` itself before calling this.

    Rejects any comma outright rather than guessing whether it is a decimal
    separator or a thousands separator: '1.234,56' and '1,234.56' both raise.
    None of the exports this project imports ever use a comma in a number,
    so nothing real is lost, and a wrong guess here would be a silently
    wrong tax figure. Scientific notation ('1.63934E-5', as seen in some
    Price columns) is also rejected — not a shape this function parses.
    """
    m = _AMOUNT_RE.match(str(raw))
    if not m:
        raise ValueError(f'unparsable amount: {raw!r}')
    number, unit = m.groups()
    return abs(float(number)), unit.upper()


def split_pair(pair):
    """'BTCUSDT' → ('BTC', 'USDT'). Raises on a pair we do not recognise.

    Tries the longest matching quote first, so the result does not depend on
    the order KNOWN_QUOTES happens to be written in (e.g. both 'BUSD' and its
    suffix 'USD' are candidates for 'XBUSD' — the longer one must win).
    """
    pair = pair.strip().upper()
    for quote in sorted(KNOWN_QUOTES, key=len, reverse=True):
        if pair.endswith(quote) and len(pair) > len(quote):
            return pair[: -len(quote)], quote
    raise ValueError(f'unknown pair, cannot split into assets: {pair!r}')


def read_trade_history(fh, exchange):
    """Trade-history export: one row per fill, pair + side + suffixed amounts."""
    for row in csv.DictReader(fh):
        base, quote = split_pair(row['Pair'])
        executed, _ = split_amount(row['Executed'])
        amount, _ = split_amount(row['Amount'])
        fee_amount, fee_asset = split_amount(row['Fee'] or '0')
        side = (row['Side'] or '').strip().upper()
        if side == 'SELL':
            give_asset, give_amount, take_asset, take_amount = base, executed, quote, amount
        elif side == 'BUY':
            give_asset, give_amount, take_asset, take_amount = quote, amount, base, executed
        else:
            raise ValueError(
                f"unknown Side {row['Side']!r}: expected one of {VALID_SIDES} "
                f"(Date(UTC)={row.get('Date(UTC)')!r}, Order No.={row.get('Order No.')!r}) "
                f"— a guessed leg is worse than a stopped import"
            )
        yield {
            'when': _utc(row['Date(UTC)']),
            'kind': 'TRADE',
            'give_asset': give_asset,
            'give_amount': give_amount,
            'take_asset': take_asset,
            'take_amount': take_amount,
            'fee_asset': fee_asset,
            'fee_amount': fee_amount,
            'exchange': exchange,
            'ref': (row.get('Order No.') or '').strip(),
        }


# Fiat assets never create a lot by themselves in this reader: a fiat
# deposit/withdrawal row is bank plumbing, not a crypto leg.
FIAT = ('EUR', 'USD', 'GBP', 'CHF')

# Transaction Type labels, matched exactly rather than by substring. A
# substring test ('Sell' in kind_raw) would also match a hypothetical label
# like 'Failed Sell' or 'Sell cancelled' and misclassify it as a real sale;
# a closed vocabulary only recognises the labels this reader has actually
# seen, and anything else falls through to the final `else` and raises.
_SELL_LABELS = ('Sell', 'Advanced Trade Sell')
_BUY_LABELS = ('Buy', 'Advanced Trade Buy')

_FIAT_RE = re.compile(r'^\s*-?\d+(?:\.\d+)?\s*$')


def _eur(raw, field='', allow_blank=False):
    """'€1395.0' → 1395.0. Raises on anything ambiguous, blank included.

    Same discipline as split_amount: a comma cannot be told apart from a
    decimal separator, so '€1.395,00' must raise instead of silently becoming
    1.39500 — a thousand times wrong, straight into a tax figure. Task 2
    shipped exactly that bug; do not reintroduce it here.

    A blank cell is only 0.0 where the caller declares it legitimate
    (`allow_blank=True`): the exports leave the fiat subtotal empty on rows
    that have no fiat side at all, i.e. transfers. Everywhere else a blank is
    an error, because "blank means zero" is how a real disposal gets silently
    demoted to a transfer, or a real trade recorded with zero proceeds — the
    same failure shape as the comma bug, moved up into business logic.
    """
    cleaned = str(raw).replace('€', '').replace('$', '').strip()
    if not cleaned:
        if allow_blank:
            return 0.0
        raise ValueError(f'blank {field or "fiat amount"}: a value is required on this row')
    if not _FIAT_RE.match(cleaned):
        raise ValueError(f'unparsable fiat amount in {field or "export"}: {raw!r}')
    return abs(float(cleaned))


def read_account_history(fh, exchange):
    """Account-history export: one row per movement, per asset."""
    for row in csv.DictReader(fh):
        asset = (row['Asset'] or '').strip().upper()
        if asset in FIAT:
            continue  # fiat deposits/withdrawals are bank plumbing
        kind_raw = (row['Transaction Type'] or '').strip()
        qty = abs(float(row['Quantity Transacted']))
        when = _utc(row['Timestamp'][:19], fmt='%Y-%m-%d %H:%M:%S')
        base = {
            'when': when, 'fee_asset': '',
            'fee_amount': _eur(row.get('Fees and/or Spread'), field='Fees and/or Spread',
                               allow_blank=True),
            'exchange': exchange, 'ref': (row.get('ID') or '').strip(),
        }
        # The fiat side is required exactly where the row has one. A blank
        # is tolerated only where the row has no fiat side by construction
        # (transfers); everywhere else a blank must raise, not default to
        # zero. This is independent of whether fiat_value ends up used below
        # — Subtotal is validated on every row so an ambiguous value (a
        # decimal comma) is caught even on a row where fiat_value happens
        # not to feed into the yielded row (e.g. a Receive).
        needs_fiat = kind_raw in ('Withdrawal',) or kind_raw in _SELL_LABELS or kind_raw in _BUY_LABELS
        fiat_value = _eur(row['Subtotal'], field='Subtotal', allow_blank=not needs_fiat)
        if kind_raw in ('Receive', 'Deposit'):
            # A fiat 'Deposit' never reaches here: fiat assets are skipped
            # above, by asset, not by label. This branch only fires for a
            # non-fiat asset, e.g. an export variant that labels an inbound
            # crypto transfer 'Deposit' instead of 'Receive'.
            yield {**base, 'kind': 'TRANSFER_IN', 'give_asset': '', 'give_amount': 0.0,
                   'take_asset': asset, 'take_amount': qty}
        elif kind_raw in ('Send',):
            yield {**base, 'kind': 'TRANSFER_OUT', 'give_asset': asset, 'give_amount': qty,
                   'take_asset': '', 'take_amount': 0.0}
        elif kind_raw in _SELL_LABELS:
            yield {**base, 'kind': 'TRADE', 'give_asset': asset, 'give_amount': qty,
                   'take_asset': 'EUR', 'take_amount': fiat_value}
        elif kind_raw in _BUY_LABELS:
            yield {**base, 'kind': 'TRADE', 'give_asset': 'EUR', 'give_amount': fiat_value,
                   'take_asset': asset, 'take_amount': qty}
        elif kind_raw == 'Withdrawal':
            # Crypto leaving with a fiat subtotal is recorded as a sale: that is
            # what it was on this account. The export cannot tell a sale from a
            # transfer carrying a valuation, so the importer prints every row
            # classified this way for confirmation against the bank side
            # (decision of 2026-10-16; see task 6). A blank subtotal cannot
            # reach this branch — _eur raises — so only a real zero means
            # transfer.
            if fiat_value > 0:
                yield {**base, 'kind': 'CONVERT', 'give_asset': asset, 'give_amount': qty,
                       'take_asset': 'EUR', 'take_amount': fiat_value}
            else:
                yield {**base, 'kind': 'TRANSFER_OUT', 'give_asset': asset,
                       'give_amount': qty, 'take_asset': '', 'take_amount': 0.0}
        else:
            raise ValueError(f'unknown account-history type: {kind_raw!r}')

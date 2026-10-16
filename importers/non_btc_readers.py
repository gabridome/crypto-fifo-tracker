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

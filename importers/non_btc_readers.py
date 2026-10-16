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

_AMOUNT_RE = re.compile(r'^\s*(-?[\d.,]+)\s*([A-Za-z]*)\s*$')


def _utc(value, fmt='%Y-%m-%d %H:%M:%S'):
    """Export timestamps are UTC without offset; emit explicit ISO 8601 UTC."""
    return datetime.strptime(value.strip(), fmt).replace(tzinfo=timezone.utc).isoformat()


def split_amount(raw):
    """'0.0024BTC' → (0.0024, 'BTC'); '110.00' → (110.0, '')."""
    m = _AMOUNT_RE.match(str(raw))
    if not m:
        raise ValueError(f'unparsable amount: {raw!r}')
    number, unit = m.groups()
    return abs(float(number.replace(',', ''))), unit.upper()


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
                f"(row {row!r}) — a guessed leg is worse than a stopped import"
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

"""Readers for the non-BTC legs of exchange exports.

Each reader takes an open text file and yields normalized rows (the contract is
documented in doc/plans/2026-10-16-non-btc-legs.md). Pure functions: no DB, no
network, no config. Unknown shapes raise ValueError — a leg guessed wrong is
worse than an import that stops.

A zero in the normalized row means two different things depending on which
pair of fields it sits in, and a reader of this module must tell them apart
rather than infer it:
- `give_asset='' `/`take_asset=''` paired with the matching `*_amount=0.0`
  means *there is no such leg* — a transfer has only one side, by
  construction, not a side that happens to be zero.
- `fee_asset=<currency>` paired with `fee_amount=0.0` means *there is a fee,
  and it is zero* — the currency is a property of the row (where the fee
  would be denominated if there were one), independent of the amount, so
  `fee_asset` is never blanked out just because `fee_amount` is 0.0.
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
# deposit/withdrawal row is bank plumbing, not a crypto leg. It also doubles
# as the set of currencies the 'Price Currency' column is allowed to name.
FIAT = ('EUR', 'USD', 'GBP', 'CHF')

# Transaction Type labels, matched exactly rather than by substring. A
# substring test ('Sell' in kind_raw) would also match a hypothetical label
# like 'Failed Sell' or 'Sell cancelled' and misclassify it as a real sale;
# a closed vocabulary only recognises the labels this reader has actually
# seen, and anything else falls through to the final `else` and raises.
_SELL_LABELS = ('Sell', 'Advanced Trade Sell')
_BUY_LABELS = ('Buy', 'Advanced Trade Buy')


def _eur(raw, field='', allow_blank=False):
    """'€1395.0' → 1395.0. Raises on anything ambiguous, blank included.

    Delegates the number grammar to split_amount instead of re-encoding
    "unambiguous decimal" in a second regex: a comma cannot be told apart
    from a decimal separator, so '€1.395,00' must raise instead of silently
    becoming 1.39500 — a thousand times wrong, straight into a tax figure.
    Task 2 shipped exactly that bug via a different code path; routing both
    through the same grammar means a future fix to the rule cannot land in
    one and miss the other. split_amount also rejects scientific notation,
    which a bespoke fiat regex previously did not forbid explicitly.

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
    try:
        amount, unit = split_amount(cleaned)
    except ValueError:
        raise ValueError(f'unparsable fiat amount in {field or "export"}: {raw!r}') from None
    if unit:
        raise ValueError(f'unexpected unit in fiat amount in {field or "export"}: {raw!r}')
    return amount


def read_account_history(fh, exchange):
    """Account-history export: one row per movement, per asset.

    Columns read as row[...] ('Asset', 'Transaction Type', 'Quantity
    Transacted', 'Timestamp', 'Subtotal') are assumed present in every
    export of this shape — a missing one means this is not the export this
    reader targets, and KeyError should say so loudly. Columns read as
    row.get(...) ('ID', 'Price Currency', 'Fees and/or Spread') are the ones
    known to vary across export variants or to be legitimately absent, and
    default to '' rather than raising.
    """
    for row in csv.DictReader(fh):
        asset = (row['Asset'] or '').strip().upper()
        if asset in FIAT:
            continue  # fiat deposits/withdrawals are bank plumbing

        # The fiat side of this row — EUR, USD, GBP or CHF — comes from the
        # export, never from an assumption. Saving a USD subtotal tagged
        # 'EUR' is exactly the silent mislabelling doc/project_guidelines.md
        # §Conversione valute forbids, and the valuation layer (task 5) is
        # built to receive the real currency code and convert it, not to be
        # handed 'EUR' unconditionally.
        fiat_ccy = (row.get('Price Currency') or '').strip().upper()
        if fiat_ccy not in FIAT:
            raise ValueError(f'unexpected Price Currency {row.get("Price Currency")!r} '
                             f'(ID={row.get("ID")!r})')

        kind_raw = (row['Transaction Type'] or '').strip()

        try:
            qty, qty_unit = split_amount(row['Quantity Transacted'])
        except ValueError as exc:
            raise ValueError(
                f'unparsable Quantity Transacted: {row["Quantity Transacted"]!r} '
                f'(ID={row.get("ID")!r}, Timestamp={row.get("Timestamp")!r})'
            ) from exc
        if qty_unit:
            # split_amount returns the suffix, if any, instead of raising on
            # one — this column has none, so a unit here means the row
            # contradicts its own 'Asset' column (e.g. Asset=USDC with
            # Quantity Transacted=1500.0BTC). Discarding qty_unit would
            # accept that silently; raising is the same discipline _eur
            # already applies to its own stray-unit case.
            raise ValueError(
                f'unexpected unit in Quantity Transacted: {row["Quantity Transacted"]!r} '
                f'(ID={row.get("ID")!r}, Timestamp={row.get("Timestamp")!r})'
            )

        ts_raw = row['Timestamp']
        if not ts_raw.endswith(' UTC'):
            # A fixed-width slice ([:19]) would silently misparse a timestamp
            # with fractional seconds or a different suffix into the wrong
            # instant. Splitting on the literal suffix instead means a shape
            # we don't recognise raises here, rather than landing quietly on
            # the wrong second.
            raise ValueError(
                f'unexpected Timestamp format (expected a " UTC" suffix): {ts_raw!r} '
                f'(ID={row.get("ID")!r})'
            )
        when = _utc(ts_raw[: -len(' UTC')], fmt='%Y-%m-%d %H:%M:%S')

        base = {
            'when': when,
            # Same column, same currency as the fiat leg: a fee is quoted in
            # the venue's fiat, not invented as EUR.
            'fee_asset': fiat_ccy,
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
                   'take_asset': fiat_ccy, 'take_amount': fiat_value}
        elif kind_raw in _BUY_LABELS:
            yield {**base, 'kind': 'TRADE', 'give_asset': fiat_ccy, 'give_amount': fiat_value,
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
                       'take_asset': fiat_ccy, 'take_amount': fiat_value}
            else:
                yield {**base, 'kind': 'TRANSFER_OUT', 'give_asset': asset,
                       'give_amount': qty, 'take_asset': '', 'take_amount': 0.0}
        else:
            raise ValueError(
                f'unknown account-history type: {kind_raw!r} '
                f'(ID={row.get("ID")!r}, Timestamp={row.get("Timestamp")!r})'
            )


# Deposit/withdrawal export: 'Type' names the direction where it has one,
# 'Description' is the finer label ('Convert', 'Withdrawal', ...). Both are
# checked rather than trusting one and ignoring the other: a 'Withdraw' row
# with a positive quantity, or a Type/Description pair that disagree, is a
# contradictory row, not a row this reader gets to interpret by picking a
# side. 'FIAT' carries no fixed direction of its own — it marks either leg
# of a Convert, one leg negative (given up) and one positive (received).
_TYPE_DIRECTIONS = {
    'DEPOSIT': 1,
    'WITHDRAW': -1,
    'TRANSFER IN': 1,
    'TRANSFER OUT': -1,
}
VALID_TYPES = ('FIAT',) + tuple(_TYPE_DIRECTIONS)

# The observed gap between the two legs of one conversion is on the order of
# seconds — the fixture's own pair is 10 seconds apart and straddles a minute
# boundary (09:59:50 / 10:00:00), which is exactly what a truncated-minute
# bucket gets wrong. This window is deliberately an order of magnitude wider
# than that observed gap, so a boundary straddle cannot split a real pair.
# It is a judgement call, not a figure derived from any specification or
# from a real export (none is reproduced here — this repo is public).
# Both ways of getting the window wrong still end in a raise, never an
# invented conversion: too narrow splits one real pair into two one-leg
# clusters, each reported as unpaired; too wide merges two unrelated
# conversions into one cluster whose leg count cannot resolve to one each
# way, also reported as unpaired, naming both coins.
MAX_CONVERT_GAP_SECONDS = 120


def _locator(row):
    """The (Coin=, Time=) locator shared by every raise in this reader.

    Coin is read the same way the main loop reads it — stripped and
    upper-cased — so an error message never disagrees with the row it is
    describing; building the locator twice, slightly differently, is how
    that drift happens.
    """
    coin = (row.get('Coin') or '').strip().upper()
    return f'(Coin={coin!r}, Time={row.get("Date & Time(UTC)")!r})'


def _signed_qty(raw, row):
    """The quantity of a deposit/withdrawal row, sign included.

    Here the sign *is* the direction — negative leaves the account, positive
    enters it — so this cannot use `split_amount` directly: that helper returns
    a positive amount by contract. It reuses its grammar, which refuses an
    ambiguous comma, a stray unit and scientific notation, and then puts the
    sign back. A bare `float()` would accept shapes the rest of the module
    rejects, and this column decides whether an asset came in or went out.
    """
    amount, unit = split_amount(raw)
    if unit:
        raise ValueError(f'unexpected unit in QTY: {raw!r} {_locator(row)}')
    return -amount if str(raw).strip().startswith('-') else amount


def _cluster_converts(legs):
    """Group Convert legs — (coin, signed qty, iso timestamp) — by elapsed gap.

    A merged global bucket keyed on a truncated minute can silently cross-wire
    two unrelated conversions whose legs happen to fall into the same bucket
    with a balanced (1 out, 1 in) count — a wrong CONVERT row that never
    raises. Sorting chronologically and splitting on an actual elapsed-time
    gap avoids the minute-boundary failure without introducing that risk: a
    cluster that ends up with anything other than exactly one leg each way
    still raises in the caller, naming the coins and the time, never the
    amounts.

    Known limitation: each leg is compared to the *previous* leg, not to the
    cluster's start, so a run of three or more conversions in quick
    succession — each leg within the window of its neighbour, but the whole
    run spanning much more than the window — collapses into a single
    cluster, which then raises on the leg count. That is still safe (it
    raises rather than mispairing), but such a file cannot be imported as-is;
    it has to be split by hand into smaller chunks first.
    """
    ordered = sorted(legs, key=lambda leg: leg[2])
    groups = []
    for leg in ordered:
        if groups:
            prev_when = datetime.fromisoformat(groups[-1][-1][2])
            gap = (datetime.fromisoformat(leg[2]) - prev_when).total_seconds()
            if gap <= MAX_CONVERT_GAP_SECONDS:
                groups[-1].append(leg)
                continue
        groups.append([leg])
    return groups


def read_deposit_withdrawal(fh, exchange):
    """Deposit/withdrawal export. A conversion is two Convert legs close in time.

    Rows are read in full before anything is yielded, because a Convert's two
    legs can appear in either order and a CONVERT row cannot be built until
    both have been seen. Everything is then sorted by `when` before yielding,
    so the output is chronological regardless of the order Convert pairs
    happened to resolve in — useful to anything downstream that assumes a
    reader's rows arrive in time order, as the other two readers's do. This
    means the whole file is held in memory at once (O(n) in row count),
    unlike `read_trade_history` and `read_account_history`, which stream one
    row at a time — a deliberate tradeoff for this shape, not an oversight.

    The 'Account Balance' column is read by nothing in this function. It is
    the natural signal for a future integrity check — does the balance move
    by exactly the row's quantity, for that asset, row over row — but no
    such check exists yet.
    """
    lines = fh.read().splitlines()
    if lines and lines[0].startswith('UID'):
        lines = lines[1:]
    rows = list(csv.DictReader(lines))

    convert_legs = []
    out_rows = []
    for row in rows:
        coin = (row['Coin'] or '').strip().upper()
        qty = _signed_qty(row['QTY'], row)
        when = _utc(row['Date & Time(UTC)'])
        desc = (row['Description'] or '').strip()
        type_raw = (row['Type'] or '').strip().upper()
        locator = _locator(row)

        if type_raw not in VALID_TYPES:
            raise ValueError(f'unknown Type {row["Type"]!r} {locator}')

        if type_raw == 'FIAT':
            if desc != 'Convert':
                raise ValueError(
                    f'Type=Fiat with unexpected Description {desc!r} {locator}')
            convert_legs.append((coin, qty, when))
            continue

        # The sign is not inferred from the row's purpose — it is checked
        # against the direction 'Type' itself claims. A 'Withdraw' row with
        # a positive QTY, or a 'Deposit' with a negative one, contradicts
        # its own Type column and must stop the import, exactly like an
        # unknown Side in read_trade_history: a guessed direction is worse
        # than no direction at all.
        expected_sign = _TYPE_DIRECTIONS[type_raw]
        actual_sign = 1 if qty > 0 else (-1 if qty < 0 else 0)
        if actual_sign != expected_sign:
            raise ValueError(
                f'Type={row["Type"]!r} contradicts the sign of QTY {locator}')

        if type_raw == 'DEPOSIT':
            if not desc.startswith('Deposit'):
                raise ValueError(
                    f'Type=Deposit with unexpected Description {desc!r} {locator}')
            kind = 'TRANSFER_IN'
        elif type_raw == 'WITHDRAW':
            if not desc.startswith('Withdraw'):
                raise ValueError(
                    f'Type=Withdraw with unexpected Description {desc!r} {locator}')
            kind = 'TRANSFER_OUT'
        else:
            # TRANSFER IN / TRANSFER OUT — Description is checked the same
            # way as Deposit/Withdraw: 'Type' and 'Description' must agree,
            # not just 'Type' alone, or a row where the export itself is
            # internally inconsistent would pass silently.
            if not desc.startswith('Transfer'):
                raise ValueError(
                    f'Type={row["Type"]!r} with unexpected Description {desc!r} {locator}')
            kind = 'TRANSFER_IN' if type_raw == 'TRANSFER IN' else 'TRANSFER_OUT'

        if kind == 'TRANSFER_IN':
            out_rows.append({'when': when, 'kind': kind, 'give_asset': '', 'give_amount': 0.0,
                              'take_asset': coin, 'take_amount': abs(qty),
                              'fee_asset': '', 'fee_amount': 0.0,
                              'exchange': exchange, 'ref': ''})
        else:
            out_rows.append({'when': when, 'kind': kind, 'give_asset': coin,
                              'give_amount': abs(qty), 'take_asset': '', 'take_amount': 0.0,
                              'fee_asset': '', 'fee_amount': 0.0,
                              'exchange': exchange, 'ref': ''})

    for group in _cluster_converts(convert_legs):
        given = [leg for leg in group if leg[1] < 0]
        taken = [leg for leg in group if leg[1] > 0]
        if len(given) != 1 or len(taken) != 1:
            # Named: the cluster's time and the coins involved. Never the
            # quantities — this is exactly the kind of message that ends up
            # pasted into a GitHub issue on a public repo.
            coins = sorted({leg[0] for leg in group})
            raise ValueError(
                f'unpaired convert near {group[0][2]}: '
                f'{len(given)} leg(s) out, {len(taken)} leg(s) in, coins={coins!r}')
        (give_asset, give_qty, _), (take_asset, take_qty, when) = given[0], taken[0]
        out_rows.append({'when': when, 'kind': 'CONVERT', 'give_asset': give_asset,
                          'give_amount': abs(give_qty), 'take_asset': take_asset,
                          'take_amount': take_qty, 'fee_asset': '', 'fee_amount': 0.0,
                          'exchange': exchange, 'ref': ''})

    out_rows.sort(key=lambda r: r['when'])
    yield from out_rows

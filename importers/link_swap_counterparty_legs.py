#!/usr/bin/env python3
"""Give the swap leg another importer wrote the swap_group_id of its pair.

Why this exists. `importers/import_non_btc_legs.py` filters out every BTC
row, because the BTC ledger belongs to the BTC importers and a re-import of
the legs must never be able to touch it. So a BTC->USDT swap reaches the
database as a BUY leg alone, carrying a `swap_group_id` whose SELL leg was
written by a different importer — one that does not write that column at
all. Under `tax_rules.swap_realizes = False` the engine needs both legs
under one id and refuses the group outright rather than guess
(`calculators/crypto_fifo_tracker.py:474-481`). This script reconciles the
two import paths; it does not change either of them.

What it writes. One column, `transactions.swap_group_id`, on rows where it
is NULL, one UPDATE per row, inside one transaction. No INSERT, no DELETE,
no other column. `compute_record_hash` (`importers/import_utils.py`) hashes
source, date, type, exchange, crypto, amount, value and fee — not
`swap_group_id` — so an updated row keeps the hash it was imported with,
and no figure changes. Deliberately not named `import_*`: it imports
nothing, and it cannot stand in for a missing row.

Not an importer, but the same CLI contract, and the same arguments. The
export, the shape and the exchange name must be **the ones
`import_non_btc_legs` was run with**: `_swap_id` hashes the exchange name,
the row's position in the file and the source tag, so a different argument
computes a different id. That is checked, not assumed — see the first
refusal below.

How the pairing works, and why it is by position. On the export this was
built for, the crypto-to-crypto trades share a single second-resolution
timestamp and carry no order id, and several of them dispose of the same
quantity. Pairing on (instant, quantity) is therefore ambiguous, and
ambiguity here means attaching one swap's cost basis to another swap's lot.
What does hold is order: the counterparty rows sit in the database with
contiguous ids, and their quantities in id order are the quantities of the
export's rows in file order, element by element. So the pairing is
positional — and that order is a precondition this script checks before it
writes anything:

  * the number of counterparty rows for one (asset, instant) must equal the
    number of the export's unlinked crypto-to-crypto rows there;
  * their quantities must agree element by element, in that order.

If either fails, nothing is written and the refusal says which of the two
fell and where. Positional pairing without that check would be an
assumption dressed as a fact; with it, it is a checked precondition.

Scope. It links the **disposal** leg: the row for the asset given up, which
is the leg the engine reads by `swap_group_id`. A swap whose *received*
asset is the one the non-BTC importer dropped is a different gap, and this
script does not fill it — its SELL leg is already on record, so the group is
reported as already linked and its missing BUY leg surfaces instead in the
engine's own orphan check (`calculators/crypto_fifo_tracker.py:366-378`).

Usage:
    python3 importers/link_swap_counterparty_legs.py <filepath> <shape> \\
        <exchange_name> <counterparty_source> [--db PATH] [--dry-run]

    shape:                trade_history | account_history | deposit_withdrawal
    counterparty_source:  the `source` value the other importer tagged its rows
                          with (SELECT DISTINCT source FROM transactions)
    --dry-run:            report what would be written, write nothing
    --db PATH:            a database other than the configured one
"""
import os
import sqlite3
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import DATABASE_PATH
from importers.import_non_btc_legs import READERS, SOURCE_SUFFIX
from importers.non_btc_valuation import DUST, FIAT, _apply_fee, _swap_id

# The engine reads the asset given up by swap_group_id on the SELL row
# (calculators/crypto_fifo_tracker.py:474-481), so the SELL row is the one
# this script may link — and the only kind of row it will accept as a
# candidate.
COUNTERPARTY_TYPE = 'SELL'


def _swap_leg(row, row_ref):
    """`(disposed quantity, swap_group_id)` for a crypto-to-crypto row, else None.

    The two figures come from the valuation layer's own functions —
    `_apply_fee` for the quantity that actually left the account (a fee paid
    in the asset given up is a disposal of it too) and `_swap_id` for the id
    — so neither can drift from what `import_non_btc_legs` wrote.

    What is re-expressed here is only *which* rows are crypto-to-crypto
    swaps, because `value_row` cannot be called from this script: for such a
    row it needs a rate source, and this script values nothing and must not
    reach for the network to decide a pairing. The steps below are
    `value_row`'s own, in its order — including `_apply_fee` *before* the
    fiat test, so that a fee this project cannot allocate stops this script
    on the same row it stops the import on, instead of being stepped over as
    "not a swap". `test_the_swap_leg_agrees_with_value_row` and
    `test_a_fee_in_a_third_asset_stops_the_run_even_on_a_row_with_a_fiat_leg`
    (tests/test_link_swap_counterparty_legs.py) hold this to it row for row,
    rather than by eye.
    """
    if row['kind'] in ('TRANSFER_IN', 'TRANSFER_OUT'):
        return None
    if max(row['give_amount'], row['take_amount']) <= DUST:
        return None
    _, give_total = _apply_fee(row['give_asset'], row['give_amount'],
                               row['take_asset'], row['take_amount'],
                               row['fee_asset'], row['fee_amount'])
    if row['give_asset'] in FIAT or row['take_asset'] in FIAT:
        return None
    return give_total, _swap_id(row, row_ref)


def _instant_key(raw, where):
    """A timestamp reduced to its UTC second, for comparing two import paths.

    An aware timestamp is converted, so an export written with an offset and
    one written in UTC still meet. A naive one is compared as the same wall
    clock: every importer in this project stamps UTC, but a bare string
    cannot prove what it meant, so this is a declared assumption and not a
    conversion. Sub-second precision is dropped — the exports this pairs
    against are second-resolution.

    Both the 'T' and the space separator parse, so the two import paths do
    not have to agree on which one they wrote (checked on the interpreter in
    `venv/`). The original parse error is kept on the exception chain rather
    than quoted in the message, so the message names the value as the
    database holds it.
    """
    try:
        when = datetime.fromisoformat(str(raw).strip())
    except ValueError as exc:
        raise ValueError(f'unparsable transaction_date on {where}: {raw!r}') from exc
    if when.tzinfo is not None:
        when = when.astimezone(timezone.utc).replace(tzinfo=None)
    return when.replace(microsecond=0).isoformat()


def _read_swaps(filepath, exchange_name, shape):
    """Every crypto-to-crypto row of the export, in file order, with its swap id.

    `row_ref` is rebuilt exactly as `import_non_btc_legs` builds it — the
    same source tag, the same index — because it is hashed into the id. The
    source tag is imported from that module rather than spelled again here
    for the same reason.
    """
    if shape not in READERS:
        raise ValueError(f'unknown shape {shape!r}, expected one of {sorted(READERS)}')

    source = os.path.basename(filepath) + SOURCE_SUFFIX
    with open(filepath, encoding='utf-8-sig') as fh:
        rows = list(READERS[shape](fh, exchange_name))

    swaps, without = [], 0
    for i, row in enumerate(rows):
        try:
            leg = _swap_leg(row, f'{source}#{i}')
        except ValueError as exc:
            raise ValueError(f'row {i} ({row["when"]}, {row["kind"]}): {exc}') from exc
        if leg is None:
            without += 1
            continue
        give_total, group_id = leg
        swaps.append({
            'index': i,
            'when': row['when'],
            'instant': _instant_key(row['when'], where=f'export row {i}'),
            'give_asset': row['give_asset'],
            'take_asset': row['take_asset'],
            'give_total': give_total,
            'group_id': group_id,
        })
    return swaps, without


def _candidates(conn, counterparty_source, asset):
    """The other importer's disposal rows for one asset, bucketed by UTC second.

    Ordered by id, which is the order the positional pairing relies on, and
    kept in that order inside each bucket.
    """
    buckets = {}
    for row in conn.execute(
            'SELECT id, amount, swap_group_id, transaction_date FROM transactions '
            'WHERE source = ? AND cryptocurrency = ? AND transaction_type = ? '
            'ORDER BY id', (counterparty_source, asset, COUNTERPARTY_TYPE)):
        key = _instant_key(row['transaction_date'], where=f'transactions row {row["id"]}')
        buckets.setdefault(key, []).append(row)
    return buckets


def _plan(conn, swaps, counterparty_source):
    """`(updates, already_linked)` — or a refusal, before anything is written.

    `updates` is a list of `(row id, swap_group_id)`; `already_linked` counts
    the export's swaps whose disposal leg is already on record.
    """
    already = 0
    pending = {}
    for swap in swaps:
        legs = {row['transaction_type']: row['n'] for row in conn.execute(
            'SELECT transaction_type, COUNT(*) AS n FROM transactions '
            'WHERE swap_group_id = ? GROUP BY transaction_type', (swap['group_id'],))}
        if legs.get(COUNTERPARTY_TYPE):
            # The disposal leg of this swap is on record under this id: either
            # the non-BTC importer wrote it itself (neither asset was BTC) or
            # a previous run of this script linked it. Nothing to do, and the
            # reason this script is idempotent.
            already += 1
            continue
        if not legs.get('BUY'):
            raise ValueError(
                f'no BUY leg on record for the swap id computed from export row '
                f"{swap['index']} ({swap['when']}, {swap['give_asset']} -> "
                f"{swap['take_asset']}). The id is hashed from the exchange name, the "
                f'shape and the row position, so this means the arguments given here '
                f'are not the ones importers/import_non_btc_legs.py was run with — or '
                f'that it was never run on this export. Writing the id anyway would '
                f'link the disposal leg to a group with no acquisition in it.')
        pending.setdefault((swap['give_asset'], swap['instant']), []).append(swap)

    updates = []
    by_asset = {}
    for (asset, instant), group in pending.items():
        if asset not in by_asset:
            by_asset[asset] = _candidates(conn, counterparty_source, asset)
        rows = by_asset[asset].get(instant, [])
        if len(rows) != len(group):
            raise ValueError(
                f'swap leg count mismatch for {asset} at {instant}: the export has '
                f'{len(group)} unlinked crypto-to-crypto row(s) there, the database has '
                f'{len(rows)} {COUNTERPARTY_TYPE} row(s) of {asset} under source '
                f'{counterparty_source!r}. The pairing is positional, so it is only '
                f'defined when the two sequences are the same length.')
        # strict=True cannot fire after the check above; it is there so that a
        # future edit which drops or weakens that check gets a loud ValueError
        # from zip rather than a silently truncated pairing.
        for position, (row, swap) in enumerate(zip(rows, group, strict=True)):
            if abs(row['amount'] - swap['give_total']) > DUST:
                raise ValueError(
                    f'swap leg quantity sequence mismatch for {asset} at {instant}: '
                    f'position {position} (export row {swap["index"]}, database row '
                    f'{row["id"]}) disposes of a different quantity on the two sides. '
                    f'The quantity compared is the gross disposed — a fee paid in the '
                    f'asset given up included, as importers/non_btc_valuation.py '
                    f'records it. Quantities are not printed here: this repo is public.')
            current = row['swap_group_id']
            if current is None:
                updates.append((row['id'], swap['group_id']))
            elif current == swap['group_id']:
                # Not reachable while the check above skips every swap whose
                # disposal leg is already on record under its id — a row
                # carrying this id *is* that leg. Kept so that the refusal
                # below can call the id different as a fact it compared, not
                # as an inference from "not NULL". No test covers this
                # branch, for the same reason.
                already += 1
            else:
                raise ValueError(
                    f'database row {row["id"]} ({asset} at {instant}, position '
                    f'{position}) already carries a different swap_group_id than the '
                    f'one computed from export row {swap["index"]}. This script only '
                    f'fills the column in where it is empty; it never overwrites an '
                    f'id, whatever wrote it.')
    return updates, already


def link_swap_counterparty_legs(filepath, exchange_name, shape, counterparty_source,
                                db_path=None, dry_run=False):
    """Link the counterparty disposal legs of one export. Returns a summary dict.

    Reads and writes inside a single transaction with an explicit BEGIN and a
    ROLLBACK on the error path (doc/code_guidelines.md §1.1): every
    precondition is checked against the rows this transaction read, and a
    refusal anywhere leaves the database exactly as it was — including the
    groups already planned before it. With `dry_run` no UPDATE is issued at
    all, so a read-only run takes no write lock on the database.
    """
    swaps, without = _read_swaps(filepath, exchange_name, shape)
    print(f'  {len(swaps)} crypto-to-crypto row(s) in the export, '
          f'{without} row(s) with no swap to link')

    conn = sqlite3.connect(db_path or DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute('BEGIN')
        updates, already = _plan(conn, swaps, counterparty_source)
        if dry_run:
            conn.rollback()
        else:
            for row_id, group_id in updates:
                conn.execute('UPDATE transactions SET swap_group_id = ? WHERE id = ?',
                             (group_id, row_id))
            conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    verb = 'would be updated (dry run: nothing written)' if dry_run else 'updated'
    print(f'  {len(updates)} row(s) {verb}')
    print(f'  {already} swap(s) already linked — their disposal leg was on record')
    print(f'  {without} export row(s) left alone — no crypto-to-crypto swap on them')
    return {'updated': len(updates), 'already_linked': already,
            'rows_without_swap': without, 'dry_run': dry_run}


def _parse_args(argv):
    """`(positionals, dry_run, db_path)`. An unknown option stops the run."""
    positionals, dry_run, db_path = [], False, None
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == '--dry-run':
            dry_run = True
        elif arg == '--db':
            if i + 1 >= len(argv):
                raise ValueError('--db needs a path')
            db_path = argv[i + 1]
            i += 1
        elif arg.startswith('-'):
            raise ValueError(f'unknown option {arg!r}')
        else:
            positionals.append(arg)
        i += 1
    if len(positionals) != 4:
        raise ValueError(f'expected 4 positional arguments, got {len(positionals)}')
    return positionals, dry_run, db_path


if __name__ == '__main__':
    try:
        cli_positionals, cli_dry_run, cli_db_path = _parse_args(sys.argv[1:])
    except ValueError as exc:
        print(f'  {exc}', file=sys.stderr)
        print(__doc__)
        sys.exit(1)
    cli_filepath, cli_shape, cli_exchange_name, cli_counterparty_source = cli_positionals
    link_swap_counterparty_legs(cli_filepath, cli_exchange_name, cli_shape,
                                cli_counterparty_source, db_path=cli_db_path,
                                dry_run=cli_dry_run)

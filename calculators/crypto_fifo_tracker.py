"""
Cryptocurrency FIFO Transaction Tracker
Compatible with new database structure
"""

import sqlite3
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
import pandas as pd
from typing import Optional
from zoneinfo import ZoneInfo

from config import SWAP_REALIZES  # imported by name so tests can monkeypatch it on this module


DUST_THRESHOLD = Decimal('1e-8')  # Single threshold for all dust comparisons


def _to_eur(value):
    """Round a Decimal value to 2 decimal places for EUR currency totals
    (cost_basis, proceeds, gain_loss, fees)."""
    return float(value.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP))


def _to_price(value):
    """Round a Decimal value to 8 decimal places for unit prices.
    Use for purchase_price_per_unit / sale_price_per_unit so sub-EUR tokens
    (e.g. SHIB at EUR 0.00001234) are not flattened to 0.00."""
    return float(value.quantize(Decimal('0.00000001'), rounding=ROUND_HALF_UP))


def _acquisition_key(iso_date):
    """Comparable key for a purchase date, whatever form it is stored in.

    Both forms are present in the production database — the great majority
    with a '+00:00' offset, a minority with no offset at all, and some of the
    first kind carrying microseconds as well; all of them parse with
    fromisoformat (verified read-only, 2026-10-17). fromisoformat gives an
    aware datetime for an offset and a naive one without, and comparing the two
    raises TypeError. A naive timestamp is read as UTC, the same assumption the
    holding-period code already makes when it compares .date().
    """
    dt = datetime.fromisoformat(iso_date)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _insert_lot_by_acquisition_date(mem_lots, lot_ptr, lot):
    """Insert keeping mem_lots ordered by acquisition date from lot_ptr on.

    Appending is right for a purchase, because purchases arrive in date order.
    It is wrong for an inherited lot: that lot carries the *original*
    acquisition date — older than lots already in the list — while it only
    enters this asset's ledger at the swap. The consumption loop walks the list
    in order, so appending would make a later sale match the newer lot first:
    FIFO by availability instead of FIFO by acquisition date, with a different
    cost basis and a different holding period, and therefore a different
    exempt/taxable split.

    Positions before lot_ptr are exhausted lots, so the insertion point is
    never before it and the pointer stays valid. A lot already partially
    consumed can end up after the inherited one, which is correct: the older
    lot would have gone first had it been available.
    """
    key = _acquisition_key(lot['purchase_date'])
    j = lot_ptr
    while j < len(mem_lots) and _acquisition_key(mem_lots[j]['purchase_date']) <= key:
        j += 1
    mem_lots.insert(j, lot)


class CryptoFIFOTracker:
    def __init__(self, db_path='crypto_fifo.db'):
        """Initialize SQLite database connection"""
        self.db_path = db_path
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        self.cursor = self.conn.cursor()
        self.timezone = ZoneInfo('Europe/Lisbon')
        
        # Performance optimizations
        self.cursor.execute("PRAGMA journal_mode=WAL")
        self.cursor.execute("PRAGMA synchronous=NORMAL")
        self.cursor.execute("PRAGMA cache_size=10000")
        
        self._ensure_fifo_tables()
    
    def _ensure_fifo_tables(self):
        """Create FIFO tracking tables if they don't exist"""
        
        # FIFO lots table
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS fifo_lots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                purchase_transaction_id INTEGER NOT NULL,
                cryptocurrency TEXT NOT NULL,
                purchase_date TEXT NOT NULL,
                original_amount REAL NOT NULL,
                remaining_amount REAL NOT NULL,
                purchase_price_per_unit REAL NOT NULL,
                cost_basis REAL NOT NULL,
                exchange_name TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (purchase_transaction_id) REFERENCES transactions(id)
            )
        ''')
        
        # Sale lot matches table
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS sale_lot_matches (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sale_transaction_id INTEGER NOT NULL,
                fifo_lot_id INTEGER NOT NULL,
                sale_date TEXT NOT NULL,
                purchase_date TEXT NOT NULL,
                cryptocurrency TEXT NOT NULL,
                amount_sold REAL NOT NULL,
                purchase_price_per_unit REAL NOT NULL,
                sale_price_per_unit REAL NOT NULL,
                cost_basis REAL NOT NULL,
                proceeds REAL NOT NULL,
                gain_loss REAL NOT NULL,
                holding_period_days INTEGER NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (sale_transaction_id) REFERENCES transactions(id),
                FOREIGN KEY (fifo_lot_id) REFERENCES fifo_lots(id)
            )
        ''')
        
        # Create indexes
        self.cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_fifo_lots_crypto 
            ON fifo_lots(cryptocurrency, remaining_amount)
        ''')
        
        self.cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_sale_matches_date 
            ON sale_lot_matches(sale_date, cryptocurrency)
        ''')
        
        self.conn.commit()
    
    def calculate_fifo_lots(self, cryptocurrency: str = 'BTC', year: Optional[int] = None):
        """Calculate FIFO lots and matches for a cryptocurrency.

        Uses in-memory lot tracking for performance (60k+ sales would be
        extremely slow with per-sale DB queries).
        """

        print(f"\nCalculating FIFO for {cryptocurrency}...")

        # ---- prerequisites and validation: everything that can raise runs
        # ---- here, before the first DELETE below.

        # transactions.swap_group_id is read by the query below in every mode,
        # so task 1's migration is a prerequisite of this engine, not an option.
        if 'swap_group_id' not in [r[1] for r in
                                   self.cursor.execute('PRAGMA table_info(transactions)')]:
            raise ValueError(
                'This database has no transactions.swap_group_id column, which the '
                'FIFO engine now reads to tell a swap apart from a plain disposal. '
                'Run: python3 calculators/update_fifo_schema_swap_group.py <db>'
            )

        # swap_lot_transfers is NOT self-created here, unlike fifo_lots and
        # sale_lot_matches: a further copy of its DDL would be a further thing
        # that can drift from doc/schema.sql, and its CHECK constraints cannot
        # be added by a later ALTER TABLE. The engine requires the table
        # instead — and only on the deferral path, because the default
        # (realizing) path never writes it and a database that predates the
        # migration must keep working there.
        transfers_table_exists = self.cursor.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='swap_lot_transfers'"
        ).fetchone() is not None
        if not SWAP_REALIZES and not transfers_table_exists:
            raise ValueError(
                'tax_rules.swap_realizes is False, which needs the swap_lot_transfers '
                'table to carry lot inheritance across a deferred swap, and this '
                'database does not have it. Run: python3 '
                'calculators/update_fifo_schema_swap_transfers.py <db>'
            )

        if not SWAP_REALIZES:
            # Scaffolding, to be removed by task 11 together with the state it
            # warns about: both sides of a deferred swap are implemented (tasks
            # 9 and 10), but nothing orders the cryptocurrencies so that the
            # asset given up is always computed before the asset that inherits
            # from it. calculators/calculate_fifo.py:48-52 processes them
            # alphabetically (SELECT DISTINCT cryptocurrency ORDER BY
            # cryptocurrency), and its per-crypto except at :118-121 turns the
            # engine's refusal into a printed line and carries on.
            #
            # The refusal itself only happens while the swap has no transfer
            # row yet, and that is the dangerous part. Measured on a seeded
            # database: give side ZZZ, take side AAA, so the driver takes AAA
            # first. Run 1 in the correct order gives AAA a lot dated
            # 2024-01-10 at EUR 1000. An older, cheaper ZZZ purchase then
            # arrives (2022-01-10, EUR 500). Run 2 — alphabetical, which is
            # what the driver does — raises nothing, prints nothing, and
            # leaves AAA on the 2024 date and EUR 1000, while
            # swap_lot_transfers now says 2022-01-10 and EUR 500: the two
            # tables disagree and the inherited date is the previous run's.
            # On a 2025 disposal that is the difference between exempt and
            # taxable.
            bar = '!' * 70
            print(f"  {bar}")
            print('  WARNING: swap deferral is INCOMPLETE in this build.')
            print('  Both sides of a deferred swap work (tasks 9 and 10), but nothing')
            print('  computes the asset given up before the asset that inherits from it:')
            print('  a full run goes in alphabetical order. That wrong order refuses')
            print('  only while the swap has no transfer row yet. Once any previous run')
            print('  has written one, the asset received silently rebuilds its lots from')
            print('  those stale rows — the inherited acquisition date, the one that')
            print('  decides the 365 days, is then the previous run one, and fifo_lots')
            print('  and swap_lot_transfers are left disagreeing with each other.')
            print('  Do NOT use these numbers for any tax return. Task 11 orders the')
            print('  cryptocurrencies by dependency and removes this warning.')
            print(f"  {bar}")

        # Get all transactions for this crypto, ordered by date
        query = '''
            SELECT id, transaction_date, transaction_type, amount,
                   price_per_unit, total_value, fee_amount, fee_currency, exchange_name,
                   swap_group_id
            FROM transactions
            WHERE cryptocurrency = ?
            AND transaction_type IN ('BUY', 'SELL', 'DEPOSIT', 'WITHDRAWAL')
            ORDER BY transaction_date ASC, id ASC
        '''

        self.cursor.execute(query, (cryptocurrency,))
        transactions = self.cursor.fetchall()

        # Which asset each swap group delivers — needed on the give side, where
        # the other leg belongs to a different cryptocurrency.
        self.cursor.execute("""
            SELECT swap_group_id, cryptocurrency FROM transactions
            WHERE swap_group_id IS NOT NULL AND swap_group_id <> ''
            AND transaction_type = 'BUY'
        """)
        swap_takes = {}
        take_legs = {}
        for row in self.cursor.fetchall():
            group, asset = row['swap_group_id'], row['cryptocurrency']
            swap_takes.setdefault(group, asset)
            take_legs.setdefault(group, []).append(asset)

        # One swap group is one normalized row
        # (importers/non_btc_valuation.py:_swap_id), so a group with more than
        # one BUY leg is an import defect either way. Counting the legs is one
        # condition covering both shapes, with a message for each: two
        # overlapping conditions would leave it unclear which one fired.
        #   - legs in different assets: which asset inherits the cost basis is
        #     undefined. A dict comprehension would have picked a winner here
        #     in silence, and the query has no ORDER BY, so not even a stable
        #     one. (Which is also why the message is sorted below rather than
        #     left in row order: it is read by a human who then has to go and
        #     find those legs in an import.)
        #   - legs in the same asset: the take side reads this group's
        #     transfers once per leg, so the cost basis is inherited once per
        #     leg — two legs, twice the cost basis, in two plausible lots. A
        #     cost basis too high is a gain too low, i.e. tax declared short.
        # Fatal only under deferral, where those lots are built; on the default
        # path nothing reads either value and the already-declared figures must
        # not be stopped by a defect that cannot reach them.
        #
        # This is a defensive guard, not a live defect: checked upstream
        # (2026-10-17), importers/non_btc_valuation.py:value_row returns exactly
        # one SELL and one BUY per crypto-to-crypto row, is called once per row,
        # and folds that row's unique row_ref into the group id. Importing the
        # same file twice cannot produce it either — under the same source name
        # the exact-match delete_by_source clears the old rows first, under a
        # different one the row_ref differs and so does the group. What can
        # produce it is a hand-edited row, or a future reader that groups
        # several fills under one order id. The cost of letting it through is
        # the asset received inheriting the same cost basis once per leg.
        conflicts = []
        for group, assets in take_legs.items():
            if len(assets) < 2:
                continue
            distinct = sorted(set(assets))
            if len(distinct) > 1:
                conflicts.append(f'{group!r} has BUY legs in two different assets: '
                                 f'{", ".join(distinct)}')
            else:
                conflicts.append(f'{group!r} has {len(assets)} BUY legs, '
                                 f'all in {distinct[0]}')
        if conflicts:
            detail = '; '.join(sorted(conflicts))
            if not SWAP_REALIZES:
                raise ValueError(
                    f'Swap group(s) with more than one BUY leg: {detail}. '
                    'A swap delivers one asset once: the engine will neither pick '
                    'which asset inherits the cost basis nor hand it out twice. '
                    'Fix the import.'
                )
            print(f"  ⚠️  Warning: swap group(s) with more than one BUY leg: "
                  f"{detail}. Harmless while tax_rules.swap_realizes is True (nothing "
                  f"reads them), fatal under deferral — fix the import.")

        if not SWAP_REALIZES:
            # Checked here, before any DELETE, for two reasons: a raise between
            # the DELETEs and the commit would hand the next crypto's commit a
            # half-emptied table (doc/code_guidelines.md §1.1), and a SELL whose
            # lots cannot be consumed at all (no purchase on record) would never
            # reach a check placed inside the consumption loop.
            orphans = sorted({t['swap_group_id'] for t in transactions
                              if t['transaction_type'] == 'SELL' and t['swap_group_id']
                              and t['swap_group_id'] not in swap_takes})
            if orphans:
                raise ValueError(
                    f'Swap group(s) {", ".join(repr(g) for g in orphans)} dispose of '
                    f'{cryptocurrency} but their BUY leg was never imported. '
                    'Under deferral the cost basis of the lots they consume would '
                    'have nowhere to land: it would leave the chain silently, with '
                    'no realized gain and no inherited lot. Import the other leg '
                    'with importers/import_non_btc_legs.py, or set '
                    'tax_rules.swap_realizes = True in config.py.'
                )

        # ---- writes from here on: one transaction, explicit BEGIN, ROLLBACK on
        # ---- the error path (doc/code_guidelines.md §1.1). The driver
        # ---- (calculators/calculate_fifo.py:87-119) catches per crypto and
        # ---- carries on with this same connection, so a DELETE left
        # ---- uncommitted here would be made permanent by the next crypto's
        # ---- commit — this crypto silently reduced to zero lots.
        self.conn.execute('BEGIN')
        try:
            # Clear existing FIFO data for this crypto (atomic with the inserts below)
            self.cursor.execute('DELETE FROM sale_lot_matches WHERE cryptocurrency = ?', (cryptocurrency,))
            self.cursor.execute('DELETE FROM fifo_lots WHERE cryptocurrency = ?', (cryptocurrency,))

            # Idempotent per crypto, like the DELETEs above. Reached only when the
            # table is there: on the default path it may legitimately be missing.
            if transfers_table_exists:
                self.cursor.execute('DELETE FROM swap_lot_transfers WHERE from_cryptocurrency = ?',
                                    (cryptocurrency,))
            transfers_batch = []

            print(f"  Processing {len(transactions):,} transactions...")

            # In-memory FIFO lots: list of dicts, ordered by purchase_date
            mem_lots = []
            # Pointer to first lot with remaining > 0 (avoids re-scanning exhausted lots)
            lot_ptr = 0

            buys = 0
            sells = 0
            matches_batch = []  # batch INSERT for sale_lot_matches

            for trans in transactions:
                trans_type = trans['transaction_type']

                if trans_type == 'BUY' and trans['swap_group_id'] and not SWAP_REALIZES:
                    # Deferral: this asset was received in a swap. It does not get a
                    # new cost basis — it inherits one lot per lot consumed on the
                    # other side, with that lot's acquisition date.
                    #
                    # Nothing to make idempotent here: the DELETEs above cleared
                    # this crypto's fifo_lots, so these lots are rebuilt from
                    # scratch on every pass. The transfers being read belong to the
                    # asset given up (from_cryptocurrency), which the DELETE on
                    # swap_lot_transfers filters on, so this pass cannot destroy
                    # the rows it is reading.
                    self.cursor.execute('''
                        SELECT source_purchase_date, amount_from, cost_basis
                        FROM swap_lot_transfers
                        WHERE swap_group_id = ? AND to_cryptocurrency = ?
                        ORDER BY source_purchase_date ASC, id ASC
                    ''', (trans['swap_group_id'], cryptocurrency))
                    slices = self.cursor.fetchall()
                    if not slices:
                        # The absence of transfers does not establish WHY they
                        # are absent, and the first version of this message
                        # asserted one cause: that the asset given up had not
                        # been processed yet. Measured wrong on a second cause
                        # — an asset given up that IS processed and refuses (no
                        # purchase history, the guard in the SELL branch) also
                        # leaves no transfer, so this branch fires in the
                        # correct order too, telling the reader to do what they
                        # had just done. Which of the two it is is not
                        # established here: name the asset to look at, name
                        # both causes, pick neither.
                        self.cursor.execute("""
                            SELECT DISTINCT cryptocurrency FROM transactions
                            WHERE swap_group_id = ? AND transaction_type = 'SELL'
                            ORDER BY cryptocurrency
                        """, (trans['swap_group_id'],))
                        given_up = [r[0] for r in self.cursor.fetchall()]
                        raise ValueError(
                            f"swap {trans['swap_group_id']}: no lot transfer recorded "
                            f'for {cryptocurrency}. Given up in that swap: '
                            f"{', '.join(given_up) if given_up else 'nothing on record'}. "
                            'Either that asset has not been computed yet in this run — '
                            'the driver goes alphabetically, so the asset received can '
                            'come first — or computing it produced no transfer at all, '
                            'which it reports on its own side. This branch cannot tell '
                            'the two apart: compute the asset given up and read what it '
                            'says.'
                        )
                    received = Decimal(str(trans['amount']))
                    total_from = sum(Decimal(str(s['amount_from'])) for s in slices)
                    if total_from <= 0:
                        # Unreachable while the table carries its constraints:
                        # doc/schema.sql declares CHECK (amount_from > 0) on
                        # every row, and a CHECK cannot be added by a later
                        # ALTER TABLE, so an installation missing it stays
                        # missing it. Loud rather than plausible: allocating a
                        # share of zero would give the whole amount received to
                        # the last slice, carrying one slice cost basis out of
                        # several on a lot that looks ordinary.
                        raise ValueError(
                            f"swap {trans['swap_group_id']}: the lot transfers into "
                            f'{cryptocurrency} sum to {total_from} units. '
                            'CHECK (amount_from > 0) in doc/schema.sql makes that '
                            'impossible, so this is a violated schema invariant, not '
                            'an allocation to work around: check that '
                            'swap_lot_transfers was created with its constraints.'
                        )
                    allocated = Decimal('0')
                    for n, sl in enumerate(slices, 1):
                        share = Decimal(str(sl['amount_from'])) / total_from
                        # Last slice takes the remainder: the sum must equal what arrived.
                        qty = (received - allocated) if n == len(slices) else (received * share)
                        allocated += qty
                        cost = Decimal(str(sl['cost_basis']))
                        _insert_lot_by_acquisition_date(mem_lots, lot_ptr, {
                            'db_id': None,
                            'key': len(mem_lots),
                            'purchase_transaction_id': trans['id'],
                            'cryptocurrency': cryptocurrency,
                            'purchase_date': sl['source_purchase_date'],   # inherited
                            'original_amount': qty,
                            'remaining_amount': qty,
                            'purchase_price_per_unit': (cost / qty) if qty > 0 else Decimal('0'),
                            'cost_basis': cost,                            # inherited
                            'purchase_fee_total': Decimal('0'),
                            'exchange_name': trans['exchange_name'],
                        })
                    buys += 1
                    continue

                if trans_type == 'BUY':
                    amount = float(trans['amount'])
                    price_per_unit = float(trans['price_per_unit']) if trans['price_per_unit'] else 0

                    if price_per_unit > 0:
                        base_cost = Decimal(str(amount)) * Decimal(str(price_per_unit))
                    else:
                        base_cost = Decimal(str(trans['total_value'] or 0))

                    try:
                        fee_amount = Decimal(str(trans['fee_amount'])) if trans['fee_amount'] else Decimal('0')
                    except (KeyError, TypeError):
                        fee_amount = Decimal('0')
                    cost_basis = base_cost + fee_amount

                    mem_lots.append({
                        'db_id': None,  # assigned after INSERT
                        # Stable identity, not a position: a lot's index in
                        # mem_lots shifts whenever a lot is inserted rather than
                        # appended (deferral inserts in acquisition-date order),
                        # and every index already recorded in matches_batch /
                        # transfers_batch would then resolve to the wrong lot —
                        # wrong cost basis, wrong purchase date, no error.
                        # len(mem_lots) read before the append is a monotonic
                        # counter: unique within one crypto's pass, which is all
                        # the resolution below needs.
                        'key': len(mem_lots),
                        'purchase_transaction_id': trans['id'],
                        'cryptocurrency': cryptocurrency,
                        'purchase_date': trans['transaction_date'],
                        'original_amount': Decimal(str(amount)),
                        'remaining_amount': Decimal(str(amount)),
                        'purchase_price_per_unit': Decimal(str(price_per_unit)),
                        'cost_basis': cost_basis,
                        'purchase_fee_total': fee_amount,
                        'exchange_name': trans['exchange_name'],
                    })
                    buys += 1

                elif trans_type == 'SELL':
                    amount_to_sell = Decimal(str(trans['amount']))
                    sale_date = trans['transaction_date']
                    sale_price = Decimal(str(trans['price_per_unit'])) if trans['price_per_unit'] else Decimal('0')

                    if sale_price == 0 and trans['total_value']:
                        sale_price = Decimal(str(trans['total_value'])) / amount_to_sell

                    try:
                        sale_fee_total = Decimal(str(trans['fee_amount'])) if trans['fee_amount'] else Decimal('0')
                    except (KeyError, TypeError):
                        sale_fee_total = Decimal('0')
                    sale_amount_total = Decimal(str(trans['amount']))

                    sale_date_dt = datetime.fromisoformat(sale_date)

                    # One value for the whole sale, read before the loop: the
                    # check after it has to know whether this disposal defers
                    # even when the loop consumed no lot at all and so never
                    # reached the per-lot branch below.
                    deferred_sale = bool(trans['swap_group_id']) and not SWAP_REALIZES

                    i = lot_ptr
                    while i < len(mem_lots) and amount_to_sell > DUST_THRESHOLD:
                        lot = mem_lots[i]
                        if lot['remaining_amount'] <= Decimal('0'):
                            i += 1
                            continue

                        amount_from_lot = min(amount_to_sell, lot['remaining_amount'])

                        # Cost basis with proportional purchase fee
                        purchase_price = lot['purchase_price_per_unit']
                        cost_basis_base = amount_from_lot * purchase_price
                        purchase_fee_total = lot['purchase_fee_total']
                        purchase_amount_total = lot['original_amount']
                        purchase_fee_prop = (purchase_fee_total / purchase_amount_total * amount_from_lot
                                            if purchase_amount_total > 0 else 0)
                        cost_basis = cost_basis_base + purchase_fee_prop

                        # Proceeds with proportional sale fee
                        proceeds_base = amount_from_lot * sale_price
                        sale_fee_prop = (sale_fee_total / sale_amount_total * amount_from_lot
                                        if sale_amount_total > 0 else 0)
                        proceeds = proceeds_base - sale_fee_prop

                        gain_loss = proceeds - cost_basis

                        # Holding period: counted on calendar dates only (no TZ).
                        # `(sale_dt - purchase_dt).days` floors the timedelta and a purchase
                        # late in the day vs a sale early in the day exactly one calendar year
                        # later would return 364 instead of 365 (taxable instead of exempt).
                        # Calendar-date diff is timezone-independent and matches AT practice.
                        purchase_date_dt = datetime.fromisoformat(lot['purchase_date'])
                        holding_days = (sale_date_dt.date() - purchase_date_dt.date()).days

                        if deferred_sale:
                            # No realization: the acquisition date and cost travel to
                            # the asset received (spec §7, deferral regime).
                            # Indexed, not .get(): every deferred group was checked
                            # against swap_takes before the first DELETE, so a miss
                            # here is a broken invariant and must be loud, not a row
                            # with an empty to_cryptocurrency.
                            take_asset = swap_takes[trans['swap_group_id']]
                            transfers_batch.append((
                                trans['swap_group_id'],
                                lot['key'],        # resolved to the db id below
                                cryptocurrency,
                                take_asset,
                                sale_date,
                                lot['purchase_date'],
                                float(amount_from_lot),
                                _to_eur(cost_basis),
                            ))
                        else:
                            matches_batch.append((
                                trans['id'],       # sale_transaction_id
                                lot['key'],        # resolved to the db id below
                                sale_date,
                                lot['purchase_date'],
                                cryptocurrency,
                                float(amount_from_lot),
                                _to_price(purchase_price),
                                _to_price(sale_price),
                                _to_eur(cost_basis),
                                _to_eur(proceeds),
                                _to_eur(gain_loss),
                                holding_days,
                            ))

                        # Update in-memory lot
                        lot['remaining_amount'] -= amount_from_lot
                        amount_to_sell -= amount_from_lot

                        # Advance pointer past exhausted lots
                        if lot['remaining_amount'] <= Decimal('0') and i == lot_ptr:
                            lot_ptr = i + 1

                        i += 1

                    if amount_to_sell > DUST_THRESHOLD:
                        if deferred_sale:
                            # Fatal only under deferral, and a different defect
                            # from the orphan check before the DELETEs: there
                            # the BUY leg is missing, here it is the purchase
                            # history of the asset given up. The transfers
                            # written above cover only the part that found
                            # lots, while the take side spreads the whole
                            # amount received over them — the asset received
                            # inherits the full quantity against a fraction of
                            # the cost, in a lot that looks ordinary from where
                            # it lands. Understating the cost overstates the
                            # gain, so this one runs against the taxpayer; it
                            # is a wrong figure in a return either way.
                            raise ValueError(
                                f'Deferred swap {trans["swap_group_id"]!r} disposes of '
                                f'{amount_to_sell:.8f} {cryptocurrency} with no purchase '
                                f'on record (sale on {sale_date}). Under deferral the '
                                f'asset received would inherit the whole amount that '
                                f'arrived against only the cost of the part matched '
                                f'here: a lot that looks ordinary and carries too '
                                f'little cost basis. Import the missing {cryptocurrency} '
                                'purchases, or set tax_rules.swap_realizes = True in '
                                'config.py.'
                            )
                        print(f"  ⚠️  Warning: Sale on {sale_date} has {amount_to_sell:.8f} "
                              f"{cryptocurrency} unmatched (no purchase found)")
                    sells += 1

            # Bulk write to DB
            print(f"  Writing {len(mem_lots):,} lots and {len(matches_batch):,} matches to DB...")

            # INSERT all lots. Keyed by lot['key'], not by position: see the
            # comment on that field in the BUY branch.
            lot_db_ids = {}
            for lot in mem_lots:
                self.cursor.execute('''
                    INSERT INTO fifo_lots
                    (purchase_transaction_id, cryptocurrency, purchase_date,
                     original_amount, remaining_amount, purchase_price_per_unit,
                     cost_basis, purchase_fee_total, exchange_name)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    lot['purchase_transaction_id'], lot['cryptocurrency'],
                    lot['purchase_date'], float(lot['original_amount']),
                    float(lot['remaining_amount']), _to_price(lot['purchase_price_per_unit']),
                    _to_eur(lot['cost_basis']), _to_eur(lot['purchase_fee_total']),
                    lot['exchange_name'],
                ))
                lot_db_ids[lot['key']] = self.cursor.lastrowid

            if transfers_batch:
                print(f"  Writing {len(transfers_batch):,} swap lot transfers (deferral)...")
                # Replace the lot key with the real DB id, exactly as the matches
                # below do: the pointer is what makes the inheritance auditable back
                # to the lot it consumed, and a date alone does not identify a lot.
                for transfer in transfers_batch:
                    self.cursor.execute('''
                        INSERT INTO swap_lot_transfers
                        (swap_group_id, source_fifo_lot_id, from_cryptocurrency,
                         to_cryptocurrency, swap_date, source_purchase_date,
                         amount_from, cost_basis)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ''', (
                        transfer[0], lot_db_ids[transfer[1]],
                        transfer[2], transfer[3], transfer[4], transfer[5],
                        transfer[6], transfer[7],
                    ))

            # INSERT all matches (replace lot key with real DB id)
            for match in matches_batch:
                self.cursor.execute('''
                    INSERT INTO sale_lot_matches
                    (sale_transaction_id, fifo_lot_id, sale_date, purchase_date,
                     cryptocurrency, amount_sold, purchase_price_per_unit,
                     sale_price_per_unit, cost_basis, proceeds, gain_loss,
                     holding_period_days)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    match[0], lot_db_ids[match[1]],
                    match[2], match[3], match[4], match[5],
                    match[6], match[7], match[8], match[9],
                    match[10], match[11],
                ))

            self.conn.commit()

            print(f"  ✓ Processed {buys:,} purchases, {sells:,} sales")

            # Show summary
            remaining_lots = [l for l in mem_lots if l['remaining_amount'] > Decimal('0')]
            remaining_total = sum(l['remaining_amount'] for l in remaining_lots)
            if remaining_lots:
                print(f"  Remaining: {remaining_total:.8f} {cryptocurrency} in {len(remaining_lots)} lots")
        except Exception:
            self.conn.rollback()
            raise

    def generate_holding_report(self, year: int, cryptocurrency: str = 'BTC',
                               min_holding_days: int = 0) -> pd.DataFrame:
        """Generate comprehensive holding period report"""
        
        query = """
            SELECT
                slm.sale_date,
                slm.purchase_date,
                slm.holding_period_days,
                ROUND(CAST(slm.holding_period_days AS REAL) / 365.25, 2) as holding_period_years,
                slm.amount_sold,
                slm.purchase_price_per_unit as purchase_price,
                slm.sale_price_per_unit as sale_price,
                slm.cost_basis,
                slm.proceeds,
                slm.gain_loss,
                t_sale.exchange_name as sale_exchange,
                t_purchase.exchange_name as purchase_exchange,
                t_purchase.transaction_id as purchase_tx_id,
                t_sale.transaction_id as sale_tx_id,
                CASE
                    WHEN slm.holding_period_days >= 365 THEN 'LONG_TERM'
                    ELSE 'SHORT_TERM'
                END as tax_classification
            FROM sale_lot_matches slm
            JOIN transactions t_sale ON slm.sale_transaction_id = t_sale.id
            JOIN fifo_lots fl ON slm.fifo_lot_id = fl.id
            JOIN transactions t_purchase ON fl.purchase_transaction_id = t_purchase.id
            WHERE strftime('%Y', slm.sale_date) = ?
            AND slm.cryptocurrency = ?
            AND slm.holding_period_days >= ?
            ORDER BY slm.sale_date ASC, slm.purchase_date ASC
        """
        
        df = pd.read_sql_query(query, self.conn, params=(str(year), cryptocurrency, min_holding_days))
        return df
    
    def export_tax_report(self, year: int, output_file: str, cryptocurrency: str = 'BTC'):
        """Export comprehensive tax report"""
        
        print(f"\nGenerating tax report for {cryptocurrency} - Year {year}")
        df = self.generate_holding_report(year, cryptocurrency)
        
        if len(df) == 0:
            print(f"No sales found for {cryptocurrency} in {year}")
            return
        
        df.to_csv(output_file, index=False)
        
        # Calculate summaries
        long_term = df[df['holding_period_days'] >= 365]
        short_term = df[df['holding_period_days'] < 365]
        
        print(f"\n{'='*60}")
        print(f"  {cryptocurrency} TAX REPORT FOR {year} (EUR)")
        print(f"{'='*60}")
        
        print(f"\nHolding Period Breakdown:")
        print(f"  Long-term (≥1 year):  {len(long_term):,} transactions ({len(long_term)/len(df)*100:.1f}%)")
        print(f"  Short-term (<1 year): {len(short_term):,} transactions ({len(short_term)/len(df)*100:.1f}%)")
        
        if 'gain_loss' in df.columns and df['gain_loss'].notna().any():
            total_gain = df['gain_loss'].sum()
            long_term_gain = long_term['gain_loss'].sum()
            short_term_gain = short_term['gain_loss'].sum()
            
            print(f"\nFinancial Summary (EUR):")
            print(f"  Total gain/loss:      €{total_gain:,.2f}")
            print(f"  Long-term gain/loss:  €{long_term_gain:,.2f}")
            print(f"  Short-term gain/loss: €{short_term_gain:,.2f}")
        
        print(f"\n{'='*60}")
        print(f"Report exported to: {output_file}")
        print(f"{'='*60}\n")
    
    def get_current_holdings(self, cryptocurrency: str = 'BTC') -> pd.DataFrame:
        """Get current holdings (remaining FIFO lots)"""
        
        query = """
            SELECT 
                fl.purchase_date,
                fl.original_amount,
                fl.remaining_amount,
                fl.purchase_price_per_unit,
                fl.cost_basis * (fl.remaining_amount / fl.original_amount) as remaining_cost_basis,
                fl.exchange_name,
                t.transaction_id
            FROM fifo_lots fl
            JOIN transactions t ON fl.purchase_transaction_id = t.id
            WHERE fl.cryptocurrency = ?
            AND fl.remaining_amount > 0
            ORDER BY fl.purchase_date ASC
        """
        
        df = pd.read_sql_query(query, self.conn, params=(cryptocurrency,))
        return df
    
    def close(self):
        """Close database connection"""
        if hasattr(self, 'conn'):
            self.conn.close()


if __name__ == "__main__":
    tracker = CryptoFIFOTracker('crypto_fifo.db')
    tracker.close()

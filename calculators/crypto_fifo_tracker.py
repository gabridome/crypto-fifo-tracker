"""
Cryptocurrency FIFO Transaction Tracker
Compatible with new database structure
"""

import sqlite3
from datetime import datetime
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

        # Clear existing FIFO data for this crypto (atomic with subsequent inserts)
        self.cursor.execute('DELETE FROM sale_lot_matches WHERE cryptocurrency = ?', (cryptocurrency,))
        self.cursor.execute('DELETE FROM fifo_lots WHERE cryptocurrency = ?', (cryptocurrency,))

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
            WHERE swap_group_id IS NOT NULL AND transaction_type = 'BUY'
        """)
        swap_takes = {row['swap_group_id']: row['cryptocurrency'] for row in self.cursor.fetchall()}

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

                    deferred = bool(trans['swap_group_id']) and not SWAP_REALIZES
                    if deferred:
                        # No realization: the acquisition date and cost travel to
                        # the asset received (spec §7, deferral regime).
                        take_asset = swap_takes.get(trans['swap_group_id'])
                        if not take_asset:
                            raise ValueError(
                                f"Swap group {trans['swap_group_id']!r} disposes of "
                                f'{cryptocurrency} but its BUY leg was never imported. '
                                'Under deferral the cost basis of the lots consumed here '
                                'would have nowhere to land: it would leave the chain '
                                'silently, with no realized gain and no inherited lot. '
                                'Import the other leg with importers/import_non_btc_legs.py, '
                                'or set tax_rules.swap_realizes = True in config.py.'
                            )
                        transfers_batch.append((
                            trans['swap_group_id'],
                            i,                 # lot index (replaced with db_id later)
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
                            i,                 # lot index (replaced with db_id later)
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
                    print(f"  ⚠️  Warning: Sale on {sale_date} has {amount_to_sell:.8f} "
                          f"{cryptocurrency} unmatched (no purchase found)")
                sells += 1

        # Bulk write to DB
        print(f"  Writing {len(mem_lots):,} lots and {len(matches_batch):,} matches to DB...")

        # INSERT all lots
        lot_db_ids = []
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
            lot_db_ids.append(self.cursor.lastrowid)

        if transfers_batch:
            print(f"  Writing {len(transfers_batch):,} swap lot transfers (deferral)...")
            # Replace the lot index with the real DB id, exactly as the matches
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

        # INSERT all matches (replace lot index with real DB id)
        for match in matches_batch:
            lot_idx = match[1]
            self.cursor.execute('''
                INSERT INTO sale_lot_matches
                (sale_transaction_id, fifo_lot_id, sale_date, purchase_date,
                 cryptocurrency, amount_sold, purchase_price_per_unit,
                 sale_price_per_unit, cost_basis, proceeds, gain_loss,
                 holding_period_days)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                match[0], lot_db_ids[lot_idx],
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

"""
Cryptocurrency FIFO Transaction Tracker
Compatible with new database structure
"""

import sqlite3
from datetime import datetime
from decimal import Decimal
import pandas as pd
from typing import Optional, Dict
import pytz


class CryptoFIFOTracker:
    def __init__(self, db_path='crypto_fifo.db'):
        """Initialize SQLite database connection"""
        self.db_path = db_path
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        self.cursor = self.conn.cursor()
        self.timezone = pytz.timezone('Europe/Lisbon')
        
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
        """Calculate FIFO lots and matches for a cryptocurrency"""
        
        print(f"\nCalculating FIFO for {cryptocurrency}...")
        
        # Clear existing FIFO data for this crypto
        self.cursor.execute('DELETE FROM sale_lot_matches WHERE cryptocurrency = ?', (cryptocurrency,))
        self.cursor.execute('DELETE FROM fifo_lots WHERE cryptocurrency = ?', (cryptocurrency,))
        self.conn.commit()
        
        # Get all transactions for this crypto, ordered by date
        query = '''
            SELECT id, transaction_date, transaction_type, amount, 
                   price_per_unit, total_value, fee_amount, fee_currency, exchange_name
            FROM transactions
            WHERE cryptocurrency = ?
            AND transaction_type IN ('BUY', 'SELL', 'DEPOSIT', 'WITHDRAWAL')
            ORDER BY transaction_date ASC, id ASC
        '''
        
        self.cursor.execute(query, (cryptocurrency,))
        transactions = self.cursor.fetchall()
        
        print(f"  Processing {len(transactions)} transactions...")
        
        buys = 0
        sells = 0
        
        for trans in transactions:
            trans_type = trans['transaction_type']
            
            # ONLY BUY creates new FIFO lots (not DEPOSIT - those are just transfers)
            if trans_type == 'BUY':
                self._create_fifo_lot(trans, cryptocurrency)
                buys += 1
            # ONLY SELL consumes FIFO lots (not WITHDRAWAL - those are just transfers)
            elif trans_type == 'SELL':
                self._process_sale(trans, cryptocurrency)
                sells += 1
        
        self.conn.commit()
        
        print(f"  ✓ Processed {buys} purchases, {sells} sales")
        
        # Show summary
        self.cursor.execute('''
            SELECT COUNT(*) as lots, SUM(remaining_amount) as remaining
            FROM fifo_lots
            WHERE cryptocurrency = ? AND remaining_amount > 0
        ''', (cryptocurrency,))
        
        result = self.cursor.fetchone()
        if result and result['remaining'] is not None:
            print(f"  Remaining: {result['remaining']:.8f} {cryptocurrency} in {result['lots']} lots")
    
    def _create_fifo_lot(self, trans, cryptocurrency):
        """Create a new FIFO lot from a purchase"""
        
        amount = float(trans['amount'])
        price_per_unit = float(trans['price_per_unit']) if trans['price_per_unit'] else 0
        
        # Calculate base cost
        if price_per_unit > 0:
            base_cost = amount * price_per_unit
        else:
            base_cost = float(trans['total_value'] or 0)
        
        # Add purchase fee to cost basis (OPZIONE A)
        try:
            fee_amount = float(trans['fee_amount']) if trans['fee_amount'] else 0
        except (KeyError, TypeError):
            fee_amount = 0
        cost_basis = base_cost + fee_amount
        
        self.cursor.execute('''
            INSERT INTO fifo_lots 
            (purchase_transaction_id, cryptocurrency, purchase_date, 
             original_amount, remaining_amount, purchase_price_per_unit, 
             cost_basis, purchase_fee_total, exchange_name)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            trans['id'],
            cryptocurrency,
            trans['transaction_date'],
            amount,
            amount,  # Initially, remaining = original
            price_per_unit,
            cost_basis,
            fee_amount,  # Store total fee for proportional allocation
            trans['exchange_name']
        ))
    
    def _process_sale(self, sale_trans, cryptocurrency):
        """Process a sale using FIFO matching"""
        
        amount_to_sell = float(sale_trans['amount'])
        sale_date = sale_trans['transaction_date']
        sale_price = float(sale_trans['price_per_unit']) if sale_trans['price_per_unit'] else 0
        
        if sale_price == 0 and sale_trans['total_value']:
            sale_price = float(sale_trans['total_value']) / amount_to_sell
        
        # Get oldest lots with remaining amount (FIFO)
        self.cursor.execute('''
            SELECT * FROM fifo_lots
            WHERE cryptocurrency = ?
            AND remaining_amount > 0
            ORDER BY purchase_date ASC, id ASC
        ''', (cryptocurrency,))
        
        lots = self.cursor.fetchall()
        
        for lot in lots:
            if amount_to_sell <= 0:
                break
            
            # How much to take from this lot
            amount_from_lot = min(amount_to_sell, float(lot['remaining_amount']))
            
            # Calculate cost basis with proportional purchase fee
            purchase_price = float(lot['purchase_price_per_unit'])
            cost_basis_base = amount_from_lot * purchase_price
            
            # Proportional purchase fee allocation
            try:
                purchase_fee_total = float(lot['purchase_fee_total']) if lot['purchase_fee_total'] else 0
            except (KeyError, TypeError):
                purchase_fee_total = 0
            purchase_amount_total = float(lot['original_amount'])
            purchase_fee_proportional = (purchase_fee_total / purchase_amount_total) * amount_from_lot if purchase_amount_total > 0 else 0
            
            cost_basis = cost_basis_base + purchase_fee_proportional
            
            # Calculate proceeds with proportional sale fee
            proceeds_base = amount_from_lot * sale_price
            
            # Proportional sale fee allocation
            try:
                sale_fee_total = float(sale_trans['fee_amount']) if sale_trans['fee_amount'] else 0
            except (KeyError, TypeError):
                sale_fee_total = 0
            sale_amount_total = float(sale_trans['amount'])
            sale_fee_proportional = (sale_fee_total / sale_amount_total) * amount_from_lot if sale_amount_total > 0 else 0
            
            proceeds = proceeds_base - sale_fee_proportional
            
            # Calculate gain/loss
            gain_loss = proceeds - cost_basis
            
            # Calculate holding period
            purchase_date = datetime.fromisoformat(lot['purchase_date'])
            sale_date_dt = datetime.fromisoformat(sale_date)
            
            # Ensure both are timezone-aware or both naive
            if purchase_date.tzinfo is None and sale_date_dt.tzinfo is not None:
                purchase_date = purchase_date.replace(tzinfo=sale_date_dt.tzinfo)
            elif purchase_date.tzinfo is not None and sale_date_dt.tzinfo is None:
                sale_date_dt = sale_date_dt.replace(tzinfo=purchase_date.tzinfo)
            
            holding_days = (sale_date_dt - purchase_date).days
            
            # Record the match
            self.cursor.execute('''
                INSERT INTO sale_lot_matches
                (sale_transaction_id, fifo_lot_id, sale_date, purchase_date,
                 cryptocurrency, amount_sold, purchase_price_per_unit, 
                 sale_price_per_unit, cost_basis, proceeds, gain_loss, 
                 holding_period_days)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                sale_trans['id'],
                lot['id'],
                sale_date,
                lot['purchase_date'],
                cryptocurrency,
                amount_from_lot,
                purchase_price,
                sale_price,
                cost_basis,
                proceeds,
                gain_loss,
                holding_days
            ))
            
            # Update lot remaining amount
            new_remaining = float(lot['remaining_amount']) - amount_from_lot
            self.cursor.execute('''
                UPDATE fifo_lots
                SET remaining_amount = ?
                WHERE id = ?
            ''', (new_remaining, lot['id']))
            
            amount_to_sell -= amount_from_lot
        
        if amount_to_sell > 0.00000001:
            print(f"  ⚠️  Warning: Sale on {sale_date} has {amount_to_sell:.8f} {cryptocurrency} unmatched (no purchase found)")
    
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

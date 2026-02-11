"""
Flask Web Dashboard for Crypto FIFO Tracker
Minimal demo with real-time stats from SQLite database
"""
from flask import Flask, render_template
import sqlite3
from datetime import datetime

app = Flask(__name__)
DB = 'crypto_fifo.db'

@app.route('/')
def dashboard():
    conn = sqlite3.connect(DB)
    cursor = conn.cursor()

    cursor.execute("""
        SELECT 
            SUM(CASE WHEN transaction_type='BUY' THEN amount ELSE 0 END) - 
            SUM(CASE WHEN transaction_type='SELL' THEN amount ELSE 0 END)
        FROM transactions 
        WHERE cryptocurrency='BTC'
    """)
    balance = cursor.fetchone()[0] or 0
    
    # 2024 Sales Stats
    cursor.execute("""
        SELECT 
            COUNT(*) as count,
            SUM(amount_sold) as btc_sold,
            SUM(proceeds) as proceeds,
            SUM(cost_basis) as cost_basis,
            SUM(gain_loss) as gain_loss,
            SUM(CASE WHEN holding_period_days >= 365 THEN 1 ELSE 0 END) as long_term,
            SUM(CASE WHEN holding_period_days < 365 THEN 1 ELSE 0 END) as short_term,
            AVG(sale_price_per_unit) as avg_sale_price,
            AVG(purchase_price_per_unit) as avg_purchase_price,
            AVG(holding_period_days) as avg_holding_days
        FROM sale_lot_matches
        WHERE strftime('%Y', sale_date) = '2024'
        AND cryptocurrency='BTC'
    """)
    
    stats = cursor.fetchone()
    sales_count = stats[0] or 0
    btc_sold = stats[1] or 0
    proceeds = stats[2] or 0
    cost_basis = stats[3] or 0
    gain_loss = stats[4] or 0
    long_term_count = stats[5] or 0
    short_term_count = stats[6] or 0
    avg_sale_price = stats[7] or 0
    avg_purchase_price = stats[8] or 0
    avg_holding_days = stats[9] or 0
    
    conn.close()
    
    # Calculations
    long_term_pct = (long_term_count / sales_count * 100) if sales_count > 0 else 0
    short_term_pct = 100 - long_term_pct
    roi = ((proceeds - cost_basis) / cost_basis * 100) if cost_basis > 0 else 0
    gain_per_btc = (gain_loss / btc_sold) if btc_sold > 0 else 0
    
    return render_template('dashboard.html',
                         balance=balance,
                         sales_2024=sales_count,
                         btc_sold=btc_sold,
                         proceeds=proceeds,
                         cost_basis=cost_basis,
                         gain_loss=gain_loss,
                         long_term_count=long_term_count,
                         short_term_count=short_term_count,
                         long_term_pct=long_term_pct,
                         short_term_pct=short_term_pct,
                         avg_sale_price=avg_sale_price,
                         avg_purchase_price=avg_purchase_price,
                         avg_holding_days=avg_holding_days,
                         roi=roi,
                         gain_per_btc=gain_per_btc,
                         now=datetime.now())

if __name__ == '__main__':
    print("="*70)
    print("🚀 CRYPTO FIFO TRACKER - WEB DASHBOARD")
    print("="*70)
    print("\n✓ Starting Flask server...")
    print("✓ Database: crypto_fifo.db")
    print("\n📊 Dashboard URL: http://127.0.0.1:5000")
    print("\n⚠️  Press CTRL+C to stop\n")
    print("="*70)
    
    app.run(debug=True, host='127.0.0.1', port=5000)

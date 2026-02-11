"""
Generate Annual Summary Report
Usage: python3 generate_annual_summary.py [YEAR] [DB_PATH]
"""
import sqlite3
import sys
import os

def main():
    year = int(sys.argv[1]) if len(sys.argv) > 1 else 2025
    db_path = sys.argv[2] if len(sys.argv) > 2 else 'data/crypto_fifo.db'

    if not os.path.exists(db_path):
        print(f"✗ Database not found: {db_path}")
        return

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    print("=" * 80)
    print(f"  ANNUAL SUMMARY REPORT — {year}")
    print(f"  Database: {db_path}")
    print("=" * 80)

    # ── 1. Purchases in the year ──
    buy_rows = conn.execute("""
        SELECT
            cryptocurrency,
            COUNT(*) as num_buys,
            SUM(amount) as total_bought,
            SUM(total_value) as total_cost,
            SUM(fee_amount) as total_buy_fees,
            AVG(price_per_unit) as avg_price,
            MIN(price_per_unit) as min_price,
            MAX(price_per_unit) as max_price
        FROM transactions
        WHERE transaction_type = 'BUY'
          AND transaction_date >= ? AND transaction_date < ?
        GROUP BY cryptocurrency
        ORDER BY cryptocurrency
    """, (f'{year}-01-01', f'{year+1}-01-01')).fetchall()

    print(f"\n{'─'*80}")
    print(f"  ACQUISTI {year}")
    print(f"{'─'*80}")
    if buy_rows:
        for r in buy_rows:
            print(f"\n  {r['cryptocurrency']}:")
            print(f"    Operazioni:     {r['num_buys']:>10,}")
            print(f"    Quantità:       {r['total_bought']:>18.8f}")
            print(f"    Costo totale:   €{r['total_cost']:>14,.2f}")
            print(f"    Commissioni:    €{r['total_buy_fees']:>14,.2f}")
            print(f"    Prezzo medio:   €{r['avg_price']:>14,.2f}")
            print(f"    Prezzo min/max: €{r['min_price']:>,.2f} / €{r['max_price']:>,.2f}")
    else:
        print(f"\n  Nessun acquisto nel {year}")

    # ── 2. Sales in the year (from transactions) ──
    sell_rows = conn.execute("""
        SELECT
            cryptocurrency,
            COUNT(*) as num_sells,
            SUM(amount) as total_sold,
            SUM(total_value) as total_proceeds,
            SUM(fee_amount) as total_sell_fees,
            AVG(price_per_unit) as avg_price,
            MIN(price_per_unit) as min_price,
            MAX(price_per_unit) as max_price
        FROM transactions
        WHERE transaction_type = 'SELL'
          AND transaction_date >= ? AND transaction_date < ?
        GROUP BY cryptocurrency
        ORDER BY cryptocurrency
    """, (f'{year}-01-01', f'{year+1}-01-01')).fetchall()

    print(f"\n{'─'*80}")
    print(f"  VENDITE {year}")
    print(f"{'─'*80}")
    if sell_rows:
        for r in sell_rows:
            print(f"\n  {r['cryptocurrency']}:")
            print(f"    Operazioni:     {r['num_sells']:>10,}")
            print(f"    Quantità:       {r['total_sold']:>18.8f}")
            print(f"    Ricavo lordo:   €{r['total_proceeds']:>14,.2f}")
            print(f"    Commissioni:    €{r['total_sell_fees']:>14,.2f}")
            print(f"    Prezzo medio:   €{r['avg_price']:>14,.2f}")
            print(f"    Prezzo min/max: €{r['min_price']:>,.2f} / €{r['max_price']:>,.2f}")
    else:
        print(f"\n  Nessuna vendita nel {year}")

    # ── 3. FIFO Gains/Losses ──
    fifo_rows = conn.execute("""
        SELECT
            slm.cryptocurrency,
            COUNT(*) as num_matches,
            SUM(slm.amount_sold) as total_sold,
            SUM(slm.cost_basis) as total_cost_basis,
            SUM(slm.proceeds) as total_proceeds,
            SUM(slm.gain_loss) as total_gain_loss,
            -- Weighted average purchase price
            SUM(slm.amount_sold * slm.purchase_price_per_unit) / SUM(slm.amount_sold) as avg_purchase_price,
            -- Weighted average sale price
            SUM(slm.amount_sold * slm.sale_price_per_unit) / SUM(slm.amount_sold) as avg_sale_price,
            -- Exempt (>=365 days)
            SUM(CASE WHEN slm.holding_period_days >= 365 THEN slm.amount_sold ELSE 0 END) as exempt_amount,
            SUM(CASE WHEN slm.holding_period_days >= 365 THEN slm.gain_loss ELSE 0 END) as exempt_gain,
            SUM(CASE WHEN slm.holding_period_days >= 365 THEN 1 ELSE 0 END) as exempt_count,
            -- Taxable (<365 days)
            SUM(CASE WHEN slm.holding_period_days < 365 THEN slm.amount_sold ELSE 0 END) as taxable_amount,
            SUM(CASE WHEN slm.holding_period_days < 365 THEN slm.gain_loss ELSE 0 END) as taxable_gain,
            SUM(CASE WHEN slm.holding_period_days < 365 THEN 1 ELSE 0 END) as taxable_count,
            -- Holding period stats
            MIN(slm.holding_period_days) as min_holding,
            MAX(slm.holding_period_days) as max_holding,
            AVG(slm.holding_period_days) as avg_holding
        FROM sale_lot_matches slm
        WHERE slm.sale_date >= ? AND slm.sale_date < ?
        GROUP BY slm.cryptocurrency
        ORDER BY slm.cryptocurrency
    """, (f'{year}-01-01', f'{year+1}-01-01')).fetchall()

    print(f"\n{'─'*80}")
    print(f"  RISULTATO FIFO {year}")
    print(f"{'─'*80}")

    grand_exempt_gain = 0
    grand_taxable_gain = 0
    grand_total_gain = 0

    if fifo_rows:
        for r in fifo_rows:
            print(f"\n  {r['cryptocurrency']}:")
            print(f"    Match FIFO:          {r['num_matches']:>10,}")
            print(f"    Quantità venduta:    {r['total_sold']:>18.8f}")
            print(f"    Costo medio acq.:    €{r['avg_purchase_price']:>14,.2f}")
            print(f"    Prezzo medio vend.:  €{r['avg_sale_price']:>14,.2f}")
            print(f"    Cost basis totale:   €{r['total_cost_basis']:>14,.2f}")
            print(f"    Ricavo netto:        €{r['total_proceeds']:>14,.2f}")
            print(f"    Plus/minusvalenza:   €{r['total_gain_loss']:>14,.2f}")
            print(f"    Holding min/med/max: {r['min_holding']:,} / {r['avg_holding']:,.0f} / {r['max_holding']:,} giorni")
            print()
            print(f"    ┌─ ESENTE (≥365gg):  {r['exempt_count']:,} operazioni")
            print(f"    │  Quantità:         {r['exempt_amount']:>18.8f}")
            print(f"    │  Plus/minus:       €{r['exempt_gain']:>14,.2f}")
            print(f"    │")
            print(f"    └─ TASSABILE (<365): {r['taxable_count']:,} operazioni")
            print(f"       Quantità:         {r['taxable_amount']:>18.8f}")
            print(f"       Plus/minus:       €{r['taxable_gain']:>14,.2f}")

            grand_exempt_gain += r['exempt_gain'] or 0
            grand_taxable_gain += r['taxable_gain'] or 0
            grand_total_gain += r['total_gain_loss'] or 0
    else:
        print(f"\n  Nessuna vendita FIFO nel {year}")

    # ── 4. Per-exchange breakdown ──
    exchange_rows = conn.execute("""
        SELECT
            t.exchange_name,
            slm.cryptocurrency,
            COUNT(*) as num_ops,
            SUM(slm.amount_sold) as amount,
            SUM(slm.proceeds) as proceeds,
            SUM(slm.gain_loss) as gain_loss,
            SUM(CASE WHEN slm.holding_period_days >= 365 THEN slm.gain_loss ELSE 0 END) as exempt,
            SUM(CASE WHEN slm.holding_period_days < 365 THEN slm.gain_loss ELSE 0 END) as taxable
        FROM sale_lot_matches slm
        JOIN transactions t ON slm.sale_transaction_id = t.id
        WHERE slm.sale_date >= ? AND slm.sale_date < ?
        GROUP BY t.exchange_name, slm.cryptocurrency
        ORDER BY t.exchange_name, slm.cryptocurrency
    """, (f'{year}-01-01', f'{year+1}-01-01')).fetchall()

    if exchange_rows:
        print(f"\n{'─'*80}")
        print(f"  DETTAGLIO PER EXCHANGE {year}")
        print(f"{'─'*80}")
        print(f"\n  {'Exchange':<20} {'Crypto':<6} {'Op.':>6} {'Venduto':>14} {'Gain/Loss':>14} {'Esente':>14} {'Tassab.':>14}")
        print(f"  {'─'*20} {'─'*6} {'─'*6} {'─'*14} {'─'*14} {'─'*14} {'─'*14}")
        for r in exchange_rows:
            print(f"  {r['exchange_name']:<20} {r['cryptocurrency']:<6} {r['num_ops']:>6,} {r['amount']:>14.8f} €{r['gain_loss']:>13,.2f} €{r['exempt']:>13,.2f} €{r['taxable']:>13,.2f}")

    # ── 5. Current holdings ──
    holdings = conn.execute("""
        SELECT
            cryptocurrency,
            COUNT(*) as num_lots,
            SUM(remaining_amount) as total_remaining,
            SUM(cost_basis * remaining_amount / original_amount) as remaining_cost,
            MIN(purchase_date) as oldest_lot,
            MAX(purchase_date) as newest_lot
        FROM fifo_lots
        WHERE remaining_amount > 0.00000001
        GROUP BY cryptocurrency
        ORDER BY cryptocurrency
    """).fetchall()

    if holdings:
        print(f"\n{'─'*80}")
        print(f"  HOLDINGS RESIDUI (lotti FIFO aperti)")
        print(f"{'─'*80}")
        for r in holdings:
            avg_cost = r['remaining_cost'] / r['total_remaining'] if r['total_remaining'] > 0 else 0
            print(f"\n  {r['cryptocurrency']}:")
            print(f"    Lotti aperti:    {r['num_lots']:>10,}")
            print(f"    Quantità:        {r['total_remaining']:>18.8f}")
            print(f"    Cost basis:      €{r['remaining_cost']:>14,.2f}")
            print(f"    Costo medio:     €{avg_cost:>14,.2f}")
            print(f"    Lotto più vecchio: {r['oldest_lot'][:10]}")
            print(f"    Lotto più recente: {r['newest_lot'][:10]}")

    # ── 6. Grand totals ──
    print(f"\n{'═'*80}")
    print(f"  RIEPILOGO FINALE {year}")
    print(f"{'═'*80}")
    print(f"  Plus-valenza esente:    €{grand_exempt_gain:>14,.2f}")
    print(f"  Plus-valenza tassabile: €{grand_taxable_gain:>14,.2f}")
    print(f"  Plus-valenza totale:    €{grand_total_gain:>14,.2f}")
    tax = max(0, grand_taxable_gain * 0.28)
    print(f"  Imposta stimata (28%):  €{tax:>14,.2f}")
    print(f"{'═'*80}\n")

    conn.close()

if __name__ == "__main__":
    main()

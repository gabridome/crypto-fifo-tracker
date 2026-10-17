-- NOTE: this query reads `sale_lot_matches`, which is the register of
-- REALIZED gains, not of disposals. With `tax_rules.swap_realizes = False` a
-- crypto-to-crypto swap realizes nothing and writes no row there: the counts
-- and quantities below exclude those disposals, while a query over
-- `transactions` would include them at full value. See doc/compliance.md and
-- `calculators/swap_reporting.py`.
--
SELECT
    CASE WHEN holding_period_days >= 365 THEN 'Long-term (exempt)'
         ELSE 'Short-term (taxable)' END as category,
    COUNT(*) as matches,
    ROUND(SUM(amount_sold), 8) as btc_sold,
    ROUND(SUM(gain_loss), 2) as total_gain,
    CAST(AVG(holding_period_days) AS INTEGER) as avg_days_held
FROM sale_lot_matches
WHERE cryptocurrency = 'BTC'
GROUP BY category;

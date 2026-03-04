SELECT
    strftime('%Y', purchase_date) as purchase_year,
    COUNT(DISTINCT sale_transaction_id) as num_sales,
    ROUND(SUM(amount_sold), 8) as btc_sold,
    CAST(AVG(holding_period_days) AS INTEGER) as avg_hold_days,
    ROUND(AVG(holding_period_days) / 365.25, 1) as avg_hold_years,
    COUNT(CASE WHEN holding_period_days >= 365 THEN 1 END) as long_term_count,
    ROUND(SUM(CASE WHEN holding_period_days >= 365 THEN amount_sold ELSE 0 END), 8) as long_term_btc
FROM sale_lot_matches
WHERE cryptocurrency = 'BTC'
GROUP BY purchase_year
ORDER BY purchase_year;

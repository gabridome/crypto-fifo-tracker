SELECT
    strftime('%Y', sale_date) as sale_year,
    COUNT(*) as matches,
    ROUND(SUM(amount_sold), 8) as btc_sold,
    ROUND(SUM(gain_loss), 2) as total_gain,
    ROUND(SUM(CASE WHEN holding_period_days >= 365 THEN gain_loss ELSE 0 END), 2) as exempt_gain,
    ROUND(SUM(CASE WHEN holding_period_days < 365 THEN gain_loss ELSE 0 END), 2) as taxable_gain
FROM sale_lot_matches
WHERE cryptocurrency = 'BTC'
GROUP BY sale_year
ORDER BY sale_year;

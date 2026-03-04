SELECT 
    t.exchange_name,
    COUNT(DISTINCT slm.id) as sales,
    AVG(slm.gain_loss) as avg_gain_per_sale,
    SUM(slm.gain_loss) as total_gain,
    AVG(slm.holding_period_days) as avg_holding_days
FROM sale_lot_matches slm
JOIN transactions t ON slm.sale_transaction_id = t.id
WHERE slm.cryptocurrency = 'BTC'
GROUP BY t.exchange_name
ORDER BY total_gain DESC;
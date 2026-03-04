SELECT
    slm.sale_date,
    slm.purchase_date,
    ROUND(slm.amount_sold, 8) as btc,
    slm.holding_period_days as days_held,
    ROUND(slm.purchase_price_per_unit, 2) as buy_price,
    ROUND(slm.sale_price_per_unit, 2) as sell_price,
    ROUND(slm.gain_loss, 2) as gain_loss,
    CASE WHEN slm.holding_period_days >= 365 THEN 'exempt' ELSE 'taxable' END as status,
    t_sale.exchange_name as sold_on
FROM sale_lot_matches slm
JOIN transactions t_sale ON slm.sale_transaction_id = t_sale.id
WHERE slm.cryptocurrency = 'BTC'
ORDER BY slm.sale_date DESC, slm.purchase_date
LIMIT 50;

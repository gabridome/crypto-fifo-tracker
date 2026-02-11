SELECT 
    slm.sale_date,
    slm.purchase_date,
    slm.amount_sold as btc_amount,
    slm.holding_period_days as days_held,
    ROUND(slm.holding_period_days / 365.25, 2) as years_held,
    t_purchase.exchange_name as bought_from,
    t_sale.exchange_name as sold_on,
    slm.purchase_price_per_unit as purchase_price,
    slm.sale_price_per_unit as sale_price,
    slm.gain_loss,
    t_purchase.transaction_id as purchase_tx_id
FROM sale_lot_matches slm
JOIN transactions t_sale ON slm.sale_transaction_id = t_sale.id
JOIN fifo_lots fl ON slm.fifo_lot_id = fl.id
JOIN transactions t_purchase ON fl.purchase_transaction_id = t_purchase.id
WHERE strftime('%Y', slm.sale_date) = '2024'
AND CAST(strftime('%Y', slm.purchase_date) AS INTEGER) BETWEEN 2012 AND 2019
AND slm.holding_period_days >= 365
ORDER BY slm.sale_date, slm.purchase_date
LIMIT 100;
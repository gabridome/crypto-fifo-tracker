SELECT 
    purchase_date,
    remaining_amount as btc_available,
    purchase_price_per_unit as buy_price,
    CAST((JULIANDAY('now') - JULIANDAY(purchase_date)) AS INTEGER) as days_held,
    365 - (JULIANDAY('now') - JULIANDAY(purchase_date)) as days_to_longterm,
    exchange_name
FROM fifo_lots
WHERE remaining_amount > 0
AND cryptocurrency = 'BTC'
AND JULIANDAY('now') - JULIANDAY(purchase_date) < 365
ORDER BY days_to_longterm ASC
LIMIT 20;
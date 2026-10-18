-- NOTA: questa query legge `sale_lot_matches`, che è il registro delle
-- plusvalenze REALIZZATE, non delle cessioni. Con `tax_rules.swap_realizes =
-- False` una permuta cripto-cripto non realizza e non scrive nessuna riga lì:
-- i conteggi e le quantità qui sotto escludono quelle cessioni, mentre una
-- query su `transactions` le includerebbe al valore pieno. Vedi
-- doc/compliance.md e `calculators/swap_reporting.py`.
--
SELECT 
    strftime('%Y', purchase_date) as purchase_year,
    COUNT(DISTINCT sale_transaction_id) as num_sales,
    SUM(amount_sold) as btc_sold,
    AVG(holding_period_days) as avg_hold_days,
    ROUND(AVG(holding_period_days) / 365.25, 1) as avg_hold_years,
    COUNT(CASE WHEN holding_period_days >= 365 THEN 1 END) as long_term_count,
    SUM(CASE WHEN holding_period_days >= 365 THEN amount_sold ELSE 0 END) as long_term_btc
FROM sale_lot_matches slm
JOIN transactions t ON slm.sale_transaction_id = t.id
WHERE strftime('%Y', slm.sale_date) = '2024'
AND t.cryptocurrency = 'BTC'
GROUP BY purchase_year
ORDER BY purchase_year;
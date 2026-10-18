-- NOTA: questa query legge `sale_lot_matches`, che è il registro delle
-- plusvalenze REALIZZATE, non delle cessioni. Con `tax_rules.swap_realizes =
-- False` una permuta cripto-cripto non realizza e non scrive nessuna riga lì:
-- i conteggi e le quantità qui sotto escludono quelle cessioni, mentre una
-- query su `transactions` le includerebbe al valore pieno. Vedi
-- doc/compliance.md e `calculators/swap_reporting.py`.
--
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
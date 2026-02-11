SELECT
    exchange_name as exchange,
    SUM(CASE WHEN transaction_type='BUY' THEN amount ELSE 0 END) as acquistati,
    SUM(CASE WHEN transaction_type='SELL' THEN amount ELSE 0 END) as venduti,
    SUM(CASE WHEN transaction_type='BUY' THEN amount ELSE 0 END) -
    SUM(CASE WHEN transaction_type='SELL' THEN amount ELSE 0 END) as saldo_anno
FROM transactions
WHERE cryptocurrency='BTC'
GROUP BY exchange_name
ORDER BY saldo_anno DESC;
SELECT     strftime('%Y', transaction_date) as anno,
    SUM(CASE WHEN transaction_type='BUY' THEN amount ELSE 0 END) as acquistati,
    SUM(CASE WHEN transaction_type='SELL' THEN amount ELSE 0 END) as venduti,
    SUM(CASE WHEN transaction_type='BUY' THEN amount ELSE 0 END) -
    SUM(CASE WHEN transaction_type='SELL' THEN amount ELSE 0 END) as saldo_anno
	from transactions

where exchange_name='Revolut'
group by strftime('%Y', transaction_date)
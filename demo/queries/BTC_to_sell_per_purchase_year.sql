SELECT strftime('%Y', purchase_date) as anno_acquisto, sum(remaining_amount) from fifo_lots
where remaining_amount>0
and cryptocurrency='BTC'
group by anno_acquisto
order by purchase_date
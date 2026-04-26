-- BTC ESENTI: lotti vendibili in esenzione d'imposta (holding >= 365 giorni)
--
-- Per ogni lotto FIFO ancora aperto e maturato (>= 1 anno), mostra:
--   - data acquisto, exchange, BTC disponibili, prezzo unitario, giorni di holding
--   - cumulato_btc_esenti = somma incrementale (l'ultimo valore = totale vendibile esente)
--
-- L'ordine FIFO impone di vendere prima i lotti piu' vecchi.
-- Quindi: scorri dall'alto, somma fino a raggiungere la quantita' che vuoi vendere.

SELECT
    date(purchase_date) AS data_acquisto,
    exchange_name AS exchange,
    ROUND(remaining_amount, 8) AS btc_disponibili,
    ROUND(purchase_price_per_unit, 2) AS prezzo_acquisto_eur,
    CAST(JULIANDAY('now') - JULIANDAY(purchase_date) AS INTEGER) AS giorni_holding,
    ROUND(SUM(remaining_amount) OVER (ORDER BY purchase_date, id), 8) AS cumulato_btc_esenti
FROM fifo_lots
WHERE cryptocurrency = 'BTC'
  AND remaining_amount > 0
  AND JULIANDAY('now') - JULIANDAY(purchase_date) >= 365
ORDER BY purchase_date ASC, id ASC;

# Known Import Issues & Lessons Learned

> Catalogo dei problemi di parsing/import incontrati, con causa e soluzione.
> Aggiornare ogni volta che si risolve un nuovo problema di import.
> Ultima revisione: 2026-10-15.

---

## Per exchange

### Binance (`import_binance_with_fees.py`)

| Problema | Causa | Stato | Note |
|----------|-------|-------|------|
| Solo pair BTCEUR importati | L'importer filtra per BTCEUR/BTCUSDT/BTCBUSD. ETH e altri pair ignorati silenziosamente. | By design | Se l'utente ha tradato ETH su Binance, quei trade non entrano nel FIFO. |
| 74 fee USDT non convertite EUR nel parser | Il CSV parser della status page non converte le fee in USDT. Il DB è corretto (l'importer converte). | Display-only bug | Differenza: +€19.57 nella status page |
| Suffissi valuta nei campi | "0.02776BTC", "1731.99EUR" — l'importer li strippa con regex | Risolto | Pattern: `re.sub(r'[A-Z]+$', '', val)` |
| Gambe non-BTC dei trade non importate | L'importer dei trade tiene solo il lato BTC dei pair (filtro BTCEUR/BTCUSDT/BTCBUSD) | Risolto 2026-10-16 | `import_non_btc_legs.py` con shape `trade_history` scrive l'altra gamba, collegata alla prima da `swap_group_id` |

### Binance Card (`import_binance_card.py`)

| Problema | Causa | Stato | Note |
|----------|-------|-------|------|
| `abs()` su differenza negativa | Il parser della status page calcola `abs(differenza)` trasformando un bonus in una fee | Display-only bug | Differenza: +€95.83 nella status page |

### Bitfinex (`import_bitfinex_ecb.py`)

| Problema | Causa | Stato | Note |
|----------|-------|-------|------|
| Prezzi in USD | Bitfinex opera in USD. Serve conversione ECB per ogni riga. | Risolto | `ecb_rates.py` converte con tasso giornaliero |
| ECB rate mancante per weekend/festivi | Nessun tasso ECB nel weekend | Risolto | Fallback fino a 5 giorni (cerca il venerdì precedente) |
| Legacy importer con rate hardcoded | `import_bitfinex.py` (rimosso) usava `USD_TO_EUR_RATE = 1.28` | Risolto | File eliminato (2026-03-30), sostituito da `import_bitfinex_ecb.py` |

### Bitstamp (`import_bitstamp_with_fees.py`)

| Problema | Causa | Stato | Note |
|----------|-------|-------|------|
| Tipi "Market buy"/"Market sell" | Bitstamp usa sia "Buy"/"Sell" che "Market buy"/"Market sell" | Risolto | Mappati entrambi a BUY/SELL |

### Bybit (`import_bybit.py`)

| Problema | Causa | Stato | Note |
|----------|-------|-------|------|
| CSV mancante | Il file CSV di Bybit è sparito dalla cartella `data/`. C'è 1 BUY nel DB. | APERTO | Il file esisteva. Va ritrovato o l'export va rifatto dal portale Bybit. |
| Header UID nel CSV | La prima riga del CSV Bybit è `UID: <uid del conto>,...` — non è un header standard | Risolto | L'importer tiene la prima riga solo se è l'header (comincia con `Uid,`), altrimenti la salta. Il controllo è sul prefisso letterale, non sul numero: l'uid reale è un dato di conto e questo repo è pubblico, quindi qui e nel commento dell'importer sta un segnaposto |

### Coinbase (`import_coinbase_standalone.py`)

| Problema | Causa | Stato | Note |
|----------|-------|-------|------|
| Fee incluse nel totale | Coinbase include spread/fee nel "Total (inclusive of fees)" | Risolto | L'importer calcola: fee = Total - Subtotal |
| Formato data "Oct 15, 2024, 3:22 PM" | Non ISO standard | Risolto | Parser dedicato con `strptime` |
| Tipo "OTHER" non nel CHECK constraint | Transaction type sconosciuto genera errore DB | APERTO | L'importer dovrebbe skippare i tipi non riconosciuti con warning |
| File mensili multipli | Coinbase esporta un file per mese | By design | Ogni file importato separatamente con proprio `source` |

### Coinbase Prime (`import_coinbase_prime.py`)

| Problema | Causa | Stato | Note |
|----------|-------|-------|------|
| Prezzi in USD | Come Bitfinex, serve ECB | Risolto | Stesso pattern ecb_rates.py |
| Ordini `Cancelled` con riempimento parziale scartati | L'importer filtrava `status == 'Completed'`, ma Prime cancella un ordine **dopo** un fill parziale e addebita le commissioni: la quantità eseguita resta in `filled base quantity` | Risolto 2026-10-15 | Lo status non è la domanda giusta: ora il filtro è `filled base quantity > DUST_THRESHOLD` e lo status finisce nelle `notes`. Come accorgersene su un conto reale: il saldo di chiusura dell'estratto mensile combacia con la somma di *tutti* i buy riempiti, non con quella dei soli `Completed`. I SELL cancellati, almeno su questo export, hanno sempre fill zero: il filtro perdeva solo acquisti, quindi sottostimava quantità e cost basis, non i ricavi |
| Le gambe stablecoin dei trade non entrano | Il filtro `market == 'BTC/USD'` tiene solo il lato BTC | By design | Chi converte BTC in USDT/USDC vede registrata la vendita del BTC ma non l'acquisto della stablecoin, che resta senza lotto. Per il FIFO delle stablecoin servono gli export dell'exchange dove stanno e le gambe stablecoin dei trade Binance |

### Kraken (`import_kraken_with_fees.py`)

| Problema | Causa | Stato | Note |
|----------|-------|-------|------|
| Ledger con trade accoppiati | Ogni trade ha 2 righe (es. vendita EUR + acquisto BTC) — vanno paired per `refid` | Risolto | L'importer raggruppa per `refid` e ricostruisce il trade |
| Asset names non standard | XXBT→BTC, XETH→ETH, ZUSD→USD, ZEUR→EUR | Risolto | Mapping hardcoded nell'importer |
| Possibili trade mancanti | L'utente sospetta che il ledger CSV non sia completo | APERTO | Verificare se servono altri export (es. trades history vs ledgers) |

### Mt.Gox (`import_mtgox_with_fees.py`)

| Problema | Causa | Stato | Note |
|----------|-------|-------|------|
| Transazioni non-BTC | CSV contiene anche movimenti fiat (deposit, withdraw) | Risolto | L'importer filtra solo BTC buy/sell |
| Prezzi in USD | Convertiti via ECB | Risolto | |

### Revolut (`import_revolut.py`)

| Problema | Causa | Stato | Note |
|----------|-------|-------|------|
| Nessun problema noto | | | |

### TRT / TheRockTrading (`import_trt_with_fees.py`)

| Problema | Causa | Stato | Note |
|----------|-------|-------|------|
| Trade multi-riga | Un singolo trade può occupare più righe nel CSV | Risolto | L'importer raggruppa per timestamp |
| Solo pair BTC/EUR | Altri pair ignorati | By design | |

### Wirex (`import_wirex.py`)

| Problema | Causa | Stato | Note |
|----------|-------|-------|------|
| Valori EUR mancanti nel CSV | Wirex non esporta il controvalore EUR delle transazioni crypto | Risolto | Usa CryptoPrices (BTC/EUR giornaliero da CryptoCompare) |
| File annuali multipli | Un CSV per anno (2023, 2024, 2025) | By design | Ogni file importato separatamente |

### Standard CSV (`import_standard_csv.py`)

| Problema | Causa | Stato | Note |
|----------|-------|-------|------|
| USD salvati come EUR | Quando ECB non disponibile, i valori USD venivano salvati come EUR | Risolto (2026-03-30) | Ora l'import abortisce con `ValueError` |
| Crypto-to-crypto side mancante | Solo un lato del trade veniva registrato | Risolto | Ora crea sideA + sideB con EUR da CryptoPrices |
| Coinpal USD→EUR | Coinpal opera in USD ma il CSV non lo segnala | Risolto | Parser aggiornato per riconoscere Coinpal come USD |

### Gambe non-BTC (`import_non_btc_legs.py`)

Non è un exchange: è l'importer additivo delle cripto diverse da BTC e delle
stablecoin, con tre shape di export (`trade_history`, `account_history`,
`deposit_withdrawal`). Scrive sotto un `source` con suffisso `#non-btc`, così il
registro BTC resta intatto.

| Problema | Causa | Stato | Note |
|----------|-------|-------|------|
| Lotti stablecoin a parità 1,00 verso EUR | Lotto inserito a mano senza il tasso del giorno | Risolto 2026-10-16 | La valorizzazione usa il fiat **effettivamente pagato** sulla sede d'acquisto (`non_btc_valuation.value_row`), non il controvalore all'arrivo su un'altra sede: quello è una coincidenza del cambio di quel giorno, non un prezzo |
| Permuta differita in andata e ritorno fra due asset | Il motore calcola una cripto per volta, quindi non sa esprimere A→B in aprile e B→A in giugno | Limite noto, non raggiungibile oggi | `swap_dependency_order` rifiuta il ciclo invece di scegliere un lato. Vedi `doc/TODO.md` per cosa servirebbe a risolverlo |
| Lotti orfani (`fifo_lots.purchase_transaction_id` senza riga in `transactions`) | Una transazione cancellata dopo il calcolo FIFO, o un restore parziale | APERTO | Nessun passo del progetto li rileva: la `FOREIGN KEY` è dichiarata in `doc/schema.sql` ma SQLite non la applica senza `PRAGMA foreign_keys = ON`, che nessun file imposta. Registrato in `doc/TODO.md` |

---

## Problemi trasversali

| Problema | Causa | Stato | Note |
|----------|-------|-------|------|
| ECB rate fallback weekend | Nessun tasso ECB sabato/domenica/festivi | Risolto | Fallback fino a 5 giorni indietro in `ecb_rates.py` |
| Arrotondamenti ECB su fee BTC | Conversione USD→EUR su fee piccole (centesimi di BTC) produce differenze di arrotondamento | Fisiologico | Differenze nell'ordine di €0.01-€0.10 |
| Encoding BOM | CSV da Windows hanno BOM (byte order mark) all'inizio | Risolto | Tutti i parser usano `encoding='utf-8-sig'` |
| Dialect CSV | Alcuni exchange usano `;` come separatore, altri `,` | Risolto | `csv.Sniffer()` con fallback a `csv.excel` |

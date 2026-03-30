# TODO — Crypto FIFO Tracker

> Lista delle cose da fare, ordinata per priorità. Aggiornare ad ogni sessione.
> Ultima revisione: 2026-03-30.

---

## Priorità ALTA — Integrità e sicurezza

- [ ] **Atomicità DB negli importers**: wrappare delete+insert in transazione esplicita (tutti i 12 importers)
- [ ] **Atomicità DB nel FIFO engine**: rimuovere `conn.commit()` dopo DELETE in `crypto_fifo_tracker.py:97`
- [ ] **Migrare a Decimal** per valori EUR in `crypto_fifo_tracker.py` (gain_loss, cost_basis, proceeds, fees)
- [ ] **Round esplicito** su tutti i valori EUR prima di INSERT (minimo `round(..., 2)` ovunque)
- [ ] **Path traversal fix**: implementare `safe_path()` e applicare a upload/delete/download in `web/app.py`
- [ ] **Validazione input** nel manual entry (`web/app.py`): tipo, importo, data, exchange
- [ ] **Manual delete**: limitare a `WHERE source = 'web_manual_entry'`
- [ ] **Rimuovere `import_bitfinex.py`** (legacy, pericoloso — `import_bitfinex_ecb.py` lo sostituisce)
- [ ] **Fix USD→EUR fallback** in `import_standard_csv.py`: abortire se ECB non disponibile, non salvare USD come EUR

## Priorità MEDIA — Qualità codice

- [ ] **Eliminare `except Exception: pass`**: tutti i 12+ punti in `web/app.py` + bare `except:` negli importers
- [ ] **Connection lifecycle**: aggiungere `try/finally` o context manager a tutte le connessioni DB
- [ ] **Config path assoluti**: `DATABASE_PATH` e `ECB_RATES_FILE` derivati da `__file__`, non relativi
- [ ] **Config safe import**: wrap `get_profile()` per non crashare su `FIFO_COUNTRY` invalido
- [ ] **DB path da config**: `generate_irs_report.py` e `generate_annual_summary.py` usino `config.DATABASE_PATH`
- [ ] **Exchange country unificato**: una sola mappa in `config.py` (ISO + codici numerici AT)
- [ ] **Import path consistenti**: `from importers.ecb_rates` ovunque (non `from ecb_rates`)
- [ ] **Colonne mancanti**: aggiungere `fee_currency`/`currency` a Bitstamp, Kraken, Mt.Gox, TRT, Binance
- [ ] **Epsilon consistente**: un solo `DUST_THRESHOLD` in `crypto_fifo_tracker.py`
- [ ] **Unused imports**: rimuovere `Decimal`, `Dict` non usati; usare `zoneinfo` al posto di `pytz`

## Priorità MEDIA — Testing

- [ ] **Test sul FIFO reale**: riscrivere `test_fifo_workflow.py` per usare `crypto_fifo_tracker.py`, non il calculator embedded
- [ ] **Nuovi test per crypto_fifo_tracker.py**: edge case (vendita > disponibile, zero amount, prezzo negativo, dust)
- [ ] **Schema unico**: test e setup.sh devono leggere `doc/schema.sql`, non duplicare lo schema
- [ ] **Test esistenza file**: verificare che DB, config, schema.sql esistano
- [ ] **Portare a pytest**: migrare da framework custom a pytest per discovery e reporting standard

## Priorità MEDIA — Refactoring

- [ ] **Estrarre CSV parser**: `parse_csv_deep` / `parse_csv_rows` in `web/csv_parser.py` (eliminare ~500 righe duplicate)
- [ ] **Estrarre boilerplate importers**: funzione condivisa `import_and_verify()` per il pattern connect-delete-insert-verify-close
- [ ] **Context processor caching**: `get_wizard_status()` e `check_eurusd()` con TTL o lazy per pagine HTML only

## Priorità BASSA — Miglioramenti

- [ ] **CSRF protection**: aggiungere `flask-wtf` CSRFProtect
- [ ] **Secret key**: generare random, non hardcoded
- [ ] **Logging**: migrare da `print()` a `logging` module negli importers
- [ ] **`fmt_eur_filter`**: mostrare €0.00 per zero, '---' solo per None

---

## Operativo / Infrastruttura

- [ ] **Backup data/ su Google Drive**: rivedere o creare la procedura rclone per backup della directory `data/` (DB, CSV, report) su Google Drive
- [ ] **Manuale importatori**: scrivere documentazione per ogni importatore con analisi dei campi del file sorgente e mapping verso il DB
- [ ] **Semplificazione web UI**: rivedere le pagine web per semplificare il flow operativo (sessione dedicata)

---

## Completate

- [x] ~~Wirex EUR mancanti~~ — usa CryptoPrices (2026-03-03)
- [x] ~~Storico prezzi crypto~~ — CryptoCompare API integrato (2026-03-03)
- [x] ~~Crypto-to-crypto EUR~~ — import_standard_csv.py calcola EUR via CryptoPrices (2026-03-03)
- [x] ~~Coinpal USD→EUR~~ — convertito via ECB (2026-03-03)
- [x] ~~Source tracking~~ — migrate + backfill completati (2026-03-03)

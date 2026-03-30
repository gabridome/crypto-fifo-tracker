# TODO — Crypto FIFO Tracker

> Lista delle cose da fare, ordinata per priorità. Aggiornare ad ogni sessione.
> Ultima revisione: 2026-03-30.

---

## Priorità ALTA — Integrità e sicurezza

- [x] ~~**Atomicità DB negli importers**~~ — `import_and_verify()` in tutti i 13 importers (2026-03-30)
- [x] ~~**Atomicità DB nel FIFO engine**~~ — rimosso `conn.commit()` intermedio (2026-03-30)
- [ ] **Migrare a Decimal** per valori EUR in `crypto_fifo_tracker.py` (gain_loss, cost_basis, proceeds, fees)
- [ ] **Round esplicito** su tutti i valori EUR prima di INSERT (minimo `round(..., 2)` ovunque)
- [ ] **Path traversal fix**: implementare `safe_path()` e applicare a upload/delete/download in `web/app.py`
- [ ] **Validazione input** nel manual entry (`web/app.py`): tipo, importo, data, exchange
- [ ] **Manual delete**: limitare a `WHERE source = 'web_manual_entry'`
- [x] ~~**Rimuovere `import_bitfinex.py`**~~ — rimosso (2026-03-30)
- [x] ~~**Fix USD→EUR fallback**~~ — abort se ECB non disponibile (2026-03-30)

## Priorità MEDIA — Qualità codice

- [ ] **Eliminare `except Exception: pass`**: tutti i 12+ punti in `web/app.py` + bare `except:` negli importers
- [ ] **Connection lifecycle**: aggiungere `try/finally` o context manager a tutte le connessioni DB
- [x] ~~**Config path assoluti**~~ — `PROJECT_ROOT` + path assoluti (2026-03-30)
- [x] ~~**Config safe import**~~ — fallback a PT con warning (2026-03-30)
- [ ] **DB path da config**: `generate_irs_report.py` e `generate_annual_summary.py` usino `config.DATABASE_PATH`
- [ ] **Exchange country unificato**: una sola mappa in `config.py` (ISO + codici numerici AT)
- [x] ~~**Import path consistenti**~~ — `from importers.ecb_rates` in tutti i file (2026-03-30)
- [ ] **Colonne mancanti**: aggiungere `fee_currency`/`currency` a Bitstamp, Kraken, Mt.Gox, TRT, Binance
- [ ] **Epsilon consistente**: un solo `DUST_THRESHOLD` in `crypto_fifo_tracker.py`
- [ ] **Unused imports**: rimuovere `Decimal`, `Dict` non usati; usare `zoneinfo` al posto di `pytz`

## Priorità MEDIA — Testing

- [x] ~~**Test sul FIFO reale**~~ — usa `CryptoFIFOTracker` reale (2026-03-30)
- [x] ~~**Nuovi test edge case**~~ — sell > available, zero fee, dust, multi-lot (2026-03-30)
- [x] ~~**Schema unico**~~ — test leggono `doc/schema.sql` (2026-03-30)
- [x] ~~**Test esistenza file**~~ — schema, config, tracker, utils, guidelines (2026-03-30)
- [x] ~~**Portare a pytest**~~ — 33 test, tutti passano (2026-03-30)

## Priorità MEDIA — Refactoring

- [ ] **Estrarre CSV parser**: `parse_csv_deep` / `parse_csv_rows` in `web/csv_parser.py` (eliminare ~500 righe duplicate)
- [x] ~~**Estrarre boilerplate importers**~~ — `import_and_verify()` in `import_utils.py` (2026-03-30)
- [ ] **Context processor caching**: `get_wizard_status()` e `check_eurusd()` con TTL o lazy per pagine HTML only

## Priorità BASSA — Miglioramenti

- [ ] **CSRF protection**: aggiungere `flask-wtf` CSRFProtect
- [ ] **Secret key**: generare random, non hardcoded
- [ ] **Logging**: migrare da `print()` a `logging` module negli importers
- [ ] **`fmt_eur_filter`**: mostrare €0.00 per zero, '---' solo per None

---

## Operativo / Infrastruttura

- [ ] **Audit trail / tracciabilità fiscale**: pagina web (convertibile in PDF) che per ogni riga del report IRS (vendita, data vendita, acquisto, data acquisto, exchange, gain/loss) documenta la catena completa all'indietro: riga report → sale_lot_match → fifo_lot → transazione DB → record_hash → file CSV sorgente → riga nel file originale dell'exchange. L'obiettivo è rispondere all'Autoridade Tributária: "da dove viene questo numero?"
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

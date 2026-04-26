# TODO — Crypto FIFO Tracker

> Lista delle cose da fare, ordinata per priorità. Aggiornare ad ogni sessione.
> Ultima revisione: 2026-03-30.

---

## Priorità ALTA — Integrità e sicurezza

- [x] ~~**Atomicità DB negli importers**~~ — `import_and_verify()` in tutti i 13 importers (2026-03-30)
- [x] ~~**Atomicità DB nel FIFO engine**~~ — rimosso `conn.commit()` intermedio (2026-03-30)
- [x] ~~**Migrare a Decimal**~~ — Decimal per cost_basis/proceeds/gain_loss + _to_eur() rounding (2026-03-30)
- [x] ~~**Round esplicito**~~ — incluso nella migrazione Decimal (2026-03-30)
- [x] ~~**Path traversal fix**~~ — safe_path() + secure_filename su tutte le route (2026-03-30)
- [x] ~~**Validazione input**~~ — tipo, importo, data, exchange nel manual entry (2026-03-30)
- [x] ~~**Manual delete**~~ — `WHERE source = 'web_manual_entry'` (2026-03-30)
- [x] ~~**Rimuovere `import_bitfinex.py`**~~ — rimosso (2026-03-30)
- [x] ~~**Fix USD→EUR fallback**~~ — abort se ECB non disponibile (2026-03-30)

## Priorità MEDIA — Qualità codice

- [x] ~~**Eliminare `except Exception: pass`**~~ — 15 blocchi con logging (2026-03-30)
- [x] ~~**Connection lifecycle**~~ — try/finally su tutte le connessioni (2026-03-30)
- [x] ~~**Config path assoluti**~~ — `PROJECT_ROOT` + path assoluti (2026-03-30)
- [x] ~~**Config safe import**~~ — fallback a PT con warning (2026-03-30)
- [x] ~~**DB path da config**~~ — report generators usano config.DATABASE_PATH (2026-03-30)
- [x] ~~**Exchange country unificato**~~ — una mappa con iso + at_code + at_name (2026-03-30)
- [x] ~~**Import path consistenti**~~ — `from importers.ecb_rates` in tutti i file (2026-03-30)
- [x] ~~**Colonne mancanti**~~ — fee_currency/currency in tutti i 13 importers (2026-03-30)
- [x] ~~**Epsilon consistente**~~ — `DUST_THRESHOLD = Decimal('1e-8')` (2026-03-30)
- [x] ~~**Unused imports**~~ — rimossi Dict, usato zoneinfo al posto di pytz (2026-03-30)

## Priorità MEDIA — Testing

- [x] ~~**Test sul FIFO reale**~~ — usa `CryptoFIFOTracker` reale (2026-03-30)
- [x] ~~**Nuovi test edge case**~~ — sell > available, zero fee, dust, multi-lot (2026-03-30)
- [x] ~~**Schema unico**~~ — test leggono `doc/schema.sql` (2026-03-30)
- [x] ~~**Test esistenza file**~~ — schema, config, tracker, utils, guidelines (2026-03-30)
- [x] ~~**Portare a pytest**~~ — 33 test, tutti passano (2026-03-30)

## Priorità MEDIA — Refactoring

- [x] ~~**Estrarre CSV parser**~~ — web/csv_parser.py, app.py da 2574 a 1542 righe (2026-03-30)
- [x] ~~**Estrarre boilerplate importers**~~ — `import_and_verify()` in `import_utils.py` (2026-03-30)
- [x] ~~**Context processor caching**~~ — TTL 5s su inject_globals() (2026-03-30)

## Priorità BASSA — Miglioramenti

- [x] ~~**CSRF protection**~~ — flask-wtf CSRFProtect + auto-inject token (2026-03-30)
- [x] ~~**Secret key**~~ — os.urandom(24) (2026-03-30)
- [x] ~~**Logging**~~ — logging module in import_utils.py (2026-03-30)
- [x] ~~**`fmt_eur_filter`**~~ — mostra €0.00 per zero, '—' solo per None (2026-03-30)

---

## Operativo / Infrastruttura

- [x] ~~**Audit trail / tracciabilità fiscale**~~ — pagina /audit con drill-down per riga IRS, Print CSS (2026-03-30)
- [x] ~~**Backup data/ su Google Drive**~~ — `backup_drive.sh` con rclone (2026-03-30)
- [x] ~~**Manuale importatori**~~ — `doc/en/IMPORT_MAPPING.md` esisteva già (587 righe), aggiornato con source tracking (2026-03-30)
- [x] ~~**Semplificazione web UI**~~ — pagina unificata `/exchanges` (2026-03-30)
- [x] ~~**Enforcement layer**~~ — CLAUDE.md preamble, hooks, skill `before-coding`, git pre-commit (2026-04-26)

---

## Completate

- [x] ~~Wirex EUR mancanti~~ — usa CryptoPrices (2026-03-03)
- [x] ~~Storico prezzi crypto~~ — CryptoCompare API integrato (2026-03-03)
- [x] ~~Crypto-to-crypto EUR~~ — import_standard_csv.py calcola EUR via CryptoPrices (2026-03-03)
- [x] ~~Coinpal USD→EUR~~ — convertito via ECB (2026-03-03)
- [x] ~~Source tracking~~ — migrate + backfill completati (2026-03-03)

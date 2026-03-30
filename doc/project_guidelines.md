# Project Guidelines — Crypto FIFO Tracker

> Regole specifiche di questo progetto. Da leggere insieme a `doc/code_guidelines.md`.
> Ultima revisione: 2026-03-30.

---

## Precisione numerica

- **Valori EUR** (cost_basis, proceeds, gain_loss, fee_amount, total_value): `Decimal` con
  `quantize("0.01")` prima di ogni INSERT. Mai `float * float` per calcoli cumulativi.
- **Quantità crypto** (amount): float fino a 8 decimali — accettabile (non soggette a somme cumulative).
- **Dust threshold**: un unico `DUST_THRESHOLD = 1e-8` in `crypto_fifo_tracker.py` per tutti i confronti.

## Validazione input

Campi da validare nel manual entry e nelle API:
- `transaction_type`: `in ('BUY', 'SELL', 'DEPOSIT', 'WITHDRAWAL')`
- `amount`: positivo, numerico
- `total_value`: positivo, numerico
- `transaction_date`: formato ISO `YYYY-MM-DD`
- `exchange_name`: non vuoto
- `cryptocurrency`: non vuoto
- Operazione DELETE su transazioni manuali: `WHERE source = 'web_manual_entry'`

## Pattern importers

Tutti gli importers seguono il contratto CLI:
```
python3 importers/import_EXCHANGE.py <filepath> [exchange_name]
```

Pattern obbligatorio per ogni importer:
```python
conn = sqlite3.connect(DB_PATH)
try:
    conn.execute("BEGIN")
    delete_by_source(conn, source_filename)
    for row in rows:
        conn.execute("INSERT INTO transactions ...", params)
    conn.commit()
except Exception:
    conn.rollback()
    raise
finally:
    conn.close()
```

Colonne obbligatorie in ogni INSERT: `source`, `imported_at`, `record_hash`,
`currency`, `fee_currency`.

## Conversione valute

- Le transazioni USD devono essere convertite in EUR via `ecb_rates.py` (tassi ECB giornalieri).
- Se i tassi ECB non sono disponibili per la data: **abortire l'import**, non salvare USD come EUR.
- Import path: sempre `from importers.ecb_rates import ECBRates` (non `from ecb_rates`).

## Schema DB

- Fonte unica: `doc/schema.sql`
- I test leggono lo schema da lì — non duplicarlo
- Migrazioni in `update_fifo_schema.py` — idempotenti, con backup

## Test

```bash
python3 tests/test_fifo_workflow.py
```

I test devono importare il vero `calculators/crypto_fifo_tracker.py`, non
un calculator embedded nel file di test.

## Web app

- Porta: 5002 (macOS — 5000 occupata da AirPlay)
- `web/app.py` usa `subprocess.run()` per chiamare importers/calculators
- Path safety: usare `werkzeug.utils.secure_filename` + `safe_path()` su tutte le route con filename

## Deploy

Non applicabile (tool locale). Il codice è su GitHub (MIT license).

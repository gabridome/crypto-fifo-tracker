# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

---

# ⛔ STOP — MANDATORY PRE-FLIGHT (do not skip, do not summarise)

Before responding to ANY user request that involves reading, writing, or modifying
code in this repo, you MUST complete the following steps. The user has explicitly
asked to be alerted when these steps are skipped.

1. **Read `doc/code_guidelines.md`** in full (universal rules, 10 sections).
   If already read in this session, state so explicitly; otherwise open it now.
2. **Read `doc/project_guidelines.md`** (Decimal/EUR rules, importer pattern,
   port 5002, validation rules).
3. **Check `doc/TODO.md`** for relevant outstanding items.
4. **Invoke the project skill `before-coding`** (`.claude/skills/before-coding/SKILL.md`)
   for any non-trivial change. It encodes the 7-gate workflow:
   read → check TODO → brainstorm → branch → test first → ruff → pytest → verify.

## Hard rules — blocking, not advisory

- `ruff check .` MUST pass before every commit. Pre-commit hook rejects failures.
- `venv/bin/pytest tests/` MUST pass before every commit. Same enforcement.
- `except: pass` and `except Exception: pass` are FORBIDDEN. The hook greps the
  staged diff and rejects.
- TDD red-green-refactor is required for new behaviour: write the failing test
  FIRST, observe it fail, then implement. Invoke
  `superpowers:test-driven-development` before writing implementation code.
- Completion claims ("fatto", "done", "tests pass") REQUIRE fresh verification
  command output in the SAME message. Stale claims violate
  `superpowers:verification-before-completion`.

## Self-correction protocol

If you proceed with a code change without (a) reading the guidelines, (b) running
ruff+pytest, or (c) invoking the relevant superpowers skill: **stop, name the
step you skipped, restart from step 1**. Do not paper over the omission.

## Bypass

Pre-commit hook bypassable only with `ALLOW_DIRTY_COMMIT=1 git commit ...`,
logged to stderr — audit trail, not a hidden escape hatch. Use only when
explicitly authorised by the user.

---

# Project Overview

Open-source FIFO capital-gains tracker for cryptocurrency, designed for
Portuguese IRS tax filing (Anexo G1 + Anexo J). Imports transactions from
multiple exchanges, calculates FIFO gains/losses, classifies as exempt
(≥365 days) or taxable (<365 days), and generates Excel reports matching
the official Autoridade Tributária form layout.

**License:** MIT • **Language:** Python 3.11+ • **Database:** SQLite3
**Multi-country ready** via `config.py` profiles.

## Big-picture architecture

```
Exchange CSV files (data/)
    │
    ├── importers/import_*.py  (one per exchange + import_standard_csv.py)
    │       │
    │       └── importers/import_utils.py
    │           - compute_record_hash() — deterministic SHA256
    │           - delete_by_source()    — surgical DELETE by CSV filename
    │           - import_and_verify()   — atomic delete+insert+verify (BEGIN/COMMIT)
    │
    ├── importers/import_non_btc_legs.py  (non-BTC + stablecoin legs, additive)
    │       │   writes under `<file>#non-btc`, so the BTC ledger is untouched
    │       ├── importers/non_btc_readers.py    — the 3 export shapes → rows;
    │       │                                     an unknown shape raises
    │       └── importers/non_btc_valuation.py  — rows → transaction dicts;
    │                                             fiat actually paid, swap_group_id
    ▼
SQLite DB (data/crypto_fifo.db)
  transactions ── (BUY) ──> fifo_lots ──> sale_lot_matches (realized gains)
                                     └──> swap_lot_transfers (deferred swaps)
                                              │
    ▲                                         ▼
    │                              calculators/crypto_fifo_tracker.py
    │                              CryptoFIFOTracker.calculate_fifo_lots()
    │                              - Decimal arithmetic, _to_eur() / _to_price()
    │                              - DUST_THRESHOLD = Decimal('1e-8')
    │                              - holding period on calendar dates
    │
    │                              calculators/swap_reporting.py
    │                              deferred_disposals() — the one definition of
    │                              what a reader of sale_lot_matches cannot see
    │
    ▼
calculators/generate_irs_report.py
  get_daily_sales() → GROUP BY (sale_day, exchange, is_exempt)
  → Excel with Quadro 7 (exempt) + Quadro 9.4A (taxable)
```

**The data/ separation principle**: code is tracked in git; everything in
`data/` (DB, CSV exports, generated reports, backups) is gitignored except
`sample_transactions.csv` and `template_manual_transactions.csv`.

## Critical reference docs

- **`doc/code_guidelines.md`** — universal rules (atomicity, Decimal, no silent
  except, path safety, TDD, debugging, verification, Superpowers usage)
- **`doc/project_guidelines.md`** — project-specific (port 5002, importer
  pattern, validation rules)
- **`doc/TODO.md`** — prioritised task list, kept up to date
- **`doc/schema.sql`** — single source of truth for DB schema (test fixtures
  read from here; never duplicate the schema in code)
- **`doc/known_import_issues.md`** — catalog of per-exchange parsing gotchas
  (encoding, currency suffixes, multi-line trades, etc.)
- **`doc/compliance.md`** — the evidence method, public and third-party
  readable: what makes an import traceable, the naming convention, the local
  (gitignored) evidence register, and the fact that `tax_rules.swap_realizes`
  is a declared assumption. **No data** — no amounts, no account identifiers,
  no row counts, no names. `config.py` points at it from three comments.
- **`doc/en/`** + **`doc/pt/`** — end-user documentation in English/Portuguese

## Database schema (summary; full DDL in `doc/schema.sql`)

Four tables, all values in EUR:

- **`transactions`** — BUY/SELL/DEPOSIT/WITHDRAWAL from every source.
  Key invariants: `total_value` is gross (BEFORE fees); `fee_amount` is
  separate (never subtracted). Each row has `source` (CSV filename),
  `imported_at` (ISO), `record_hash` (SHA256 over normalised fields) for
  audit trail and rollback. `swap_group_id` links the two legs of one swap.
- **`fifo_lots`** — one row per BUY. Tracks `remaining_amount` and
  `purchase_fee_total`. Cost basis = total_value + fee.
- **`sale_lot_matches`** — the register of **realized gains**, not of
  disposals: one row per (*realizing* SELL × consumed lot), carrying
  `holding_period_days` (calendar-day diff, not timedelta) and proportional
  cost basis / proceeds / gain_loss. Under `tax_rules.swap_realizes = False` a
  swap's SELL leg consumes lots and writes **no row here** — so any query that
  reads this table as "the disposals" is incomplete by exactly those rows,
  while a query over `transactions` includes them at full value. The one
  definition of that difference is `calculators/swap_reporting.py`; the open
  readers still affected are listed in `doc/TODO.md`.
- **`swap_lot_transfers`** — one row per lot slice moved across a deferred
  swap: the acquisition date and cost travel to the asset received, the gain
  does not. Written only on the deferral path.

`record_hash` formula: `SHA256("{source}|{date}|{type}|{exchange}|{crypto}|{amount:.8f}|{value:.2f}|{fee:.2f}")`.

## FIFO algorithm — key invariants

1. Per cryptocurrency, BUY/SELL ordered by `(transaction_date, id)`
2. Each BUY → one `fifo_lot` with `remaining_amount = original_amount`
3. Each SELL consumes lots starting from oldest with remaining > 0
4. Per consumed lot: proportional cost_basis, proceeds, gain_loss
5. **Fees included in cost basis for buys**; sale fees proportionally subtracted from proceeds
6. **`holding_period_days`** computed on calendar dates only:
   `(sale_dt.date() - purchase_dt.date()).days` (timezone-independent;
   avoids the timedelta-floor 364/365 boundary bug)
7. **EUR precision**: `Decimal(str(x))` throughout; `_to_eur()` quantizes
   to 2 decimals for currency totals; `_to_price()` quantizes to 8 decimals
   for unit prices (so sub-EUR tokens like SHIB don't round to 0.00)

## Portuguese tax rules (config.py)

Art.º 10.º CIRS, Lei 24-D/2022:
- Holdings ≥ 365 days: **exempt** (still declare in Anexo G1, Quadro 7)
- Holdings < 365 days: **28%** flat (Anexo J, Quadro 9.4A for foreign exchanges)
- FIFO mandatory, all values EUR, daily aggregation per exchange per tax status
- Filing window: April 1 — June 30. Data retention: 7 years

Exchange→country map in `config.EXCHANGE_COUNTRIES` (ISO + AT numeric code +
name). `get_exchange_at_country()` returns `('000', 'Desconhecido')` for
unmapped exchanges; `generate_irs_report.warn_on_unknown_country_codes()`
emits a stderr banner before report generation.

## Web interface (Flask, port 5002 — 5000 is occupied by macOS ControlCenter)

`web/app.py` (~1780 lines) is a thin orchestration layer. It uses
`subprocess.run()` to call existing importer/calculator scripts, so
importers don't need to be rewritten as Flask views.

| Page | Purpose |
|------|---------|
| `/exchanges` | **Unified Collect+Import+Status**: per-exchange cards with file info, CSV↔DB comparison, import/re-import button, delete |
| `/exchange-docs/<exchange>` | Per-exchange field mapping, download instructions, known issues (read from `doc/known_import_issues.md`) |
| `/fifo` | Per-year gain/loss breakdown, current holdings; runs `calculate_fifo.py` |
| `/reports` | IRS Excel generation by year; SQL queries from `calculators/*.sql` |
| `/manual` | Manual transaction form for OTC, gifts, inheritance, airdrops |
| `/audit` | Per-IRS-row drilldown to source CSV + record_hash. Print CSS for PDF export |
| `/collect`, `/import`, `/status` | Legacy pages (kept for backward compatibility; superseded by `/exchanges`) |

The SQL query runner at `/reports/query/<filename>` runs `.sql` files from
`calculators/` (or `data/queries/` if exists) read-only, stripping `--`
comments before checking for SELECT/WITH start.

## Source tracking

Three columns added to `transactions`: `source`, `imported_at`, `record_hash`.
Use cases:
- **Dedup**: re-import deletes by `source`, then inserts
- **Audit**: recompute hash from CSV row → verify DB record unchanged
- **Rollback**: `DELETE FROM transactions WHERE source = 'wirex_2024.csv'`

All importers use `import_and_verify(db_path, source, insert_fn)` from
`importers/import_utils.py` — wraps `delete_by_source` + insert loop in a
single transaction with rollback on error.

## ECB and CryptoCompare prices

- **USD→EUR** via `data/eurusd.csv` (ECB official daily). `importers/ecb_rates.py`
  with 5-day weekend/holiday fallback. USD exchanges: Bitfinex, Coinbase Prime,
  Kraken, Mt.Gox.
- **Crypto unit prices** via `data/crypto_prices.csv` (CryptoCompare daily).
  `importers/crypto_prices.py` with same lookup pattern. Used for Wirex BTC
  card payments and crypto-to-crypto trade EUR valuation.

Importers ABORT if rates needed but file missing (no silent fallback to 1:1).

## Enforcement layer (4 + 1 levels)

1. **CLAUDE.md preamble** — Claude Code reads guidelines + invokes `before-coding`
2. **UserPromptSubmit hook** (`scripts/hooks/skill-reminder.py`) — injects
   reminders for keywords like "implement", "fix", "done"
3. **PreToolUse(Bash) hook** (`scripts/hooks/pre-commit-check.sh`) — blocks
   `git commit` from Claude on ruff/pytest fail or `except: pass` in diff
4. **OS-level git pre-commit** (`scripts/git-hooks/pre-commit` installed by
   `scripts/setup-enforcement.sh`) — same checks for direct terminal commits
5. *(consolidated/health on GitHub only)*: **GitHub Actions CI** as Layer 5
   (not present in this repo — crypto-fifo-tracker is local-only / OSS)

Setup after fresh clone: `./scripts/setup-enforcement.sh`. Idempotent.

---

# Common commands

## Setup (once per clone)

```bash
./setup.sh                         # creates venv, installs deps, initialises DB
source venv/bin/activate
./scripts/setup-enforcement.sh     # installs git pre-commit hook + ruff
```

## Run the web app (primary workflow)

```bash
python3 web/app.py
# Open http://127.0.0.1:5002
```

## CLI workflow (alternative)

```bash
python3 importers/import_<exchange>.py data/<file>.csv [exchange_name]
python3 calculators/calculate_fifo.py            # recalculate all FIFO lots
python3 calculators/generate_irs_report.py 2024  # generates data/reports/IRS_Crypto_FIFO_2024.xlsx
```

## Tests, lint, smoke

```bash
venv/bin/pytest tests/ -q                        # full suite (~50 tests)
venv/bin/pytest tests/test_irs_report.py -v      # one file
venv/bin/pytest tests/ -k "holding_period"       # one test pattern
ruff check .                                     # static analysis
ruff check . --fix                               # auto-fix safe issues

# Web-app smoke test (no DB writes)
python3 -c "
from web.app import app; app.config['TESTING']=True; c=app.test_client()
for r in ['/','/exchanges','/fifo','/reports','/manual','/audit']:
    print(f'{r:15s} -> {c.get(r,follow_redirects=True).status_code}')
"
```

## Backup data/ to Google Drive

```bash
./backup_drive.sh                   # rclone sync to gdrive:crypto-fifo-tracker/backups
./backup_drive.sh --dry-run         # preview without uploading
```

## Useful SQL queries (in `calculators/`, runnable from `/reports` page)

- `BTC_esenti_per_vendita.sql` — lots sellable tax-exempt today, with cumulative total
- `Maturing_lots.sql` — lots approaching 365-day boundary, ordered by days remaining
- `BTC_to_sell_per_purchase_year.sql` — remaining BTC by purchase year
- `transactions_per_exchange.sql`, `transactions_per_years.sql` — overview queries

## Development workflow (see also `before-coding` skill)

```bash
git checkout -b feature/NAME       # branch first for non-trivial changes
# ... TDD red-green-refactor ...
venv/bin/pytest tests/ -q && ruff check .
git commit -m "..."                # pre-commit hook re-runs both
git checkout main && git merge --no-ff feature/NAME
git push origin main
```

# Configuration

`config.py` — country profile, `DATABASE_PATH`, `PROJECT_ROOT`,
`EXCHANGE_COUNTRIES`. Env overrides:
- `FIFO_COUNTRY` (default `PT`)
- `FIFO_DB` (default `data/crypto_fifo.db` resolved from PROJECT_ROOT)

All paths in `config.py` are absolute, derived from `__file__`. No relative
paths in defaults (see code_guidelines.md §4.1).

# Dependencies

`flask`, `flask-wtf` (CSRF), `pandas`, `openpyxl`, `requests`. Dev: `pytest`,
`ruff`. Python 3.11+. Replaces `pytz` with stdlib `zoneinfo`.

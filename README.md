# Crypto FIFO Tracker

Open-source FIFO tracking system for cryptocurrency capital gains, designed for Portuguese IRS tax filing. Adaptable to other jurisdictions.

**[Documentação em Português](doc/pt/README.md)**

## Features

- **Web interface** — guided wizard (Collect → Import → Status → FIFO → Reports)
- **SQLite database** — single file, no server, portable
- **FIFO method** — mandatory under Portuguese tax law
- **Multi-exchange** — dedicated importers for Binance, Coinbase, Coinbase Prime, Bitstamp, Bitfinex, Kraken, Mt.Gox, TRT, Wirex, Revolut, Bybit, and a generic CSV importer
- **EUR currency** — USD→EUR conversion via official ECB historical rates
- **IRS reports** — Excel with Anexo G1 (exempt, ≥365 days) and Anexo J (taxable, <365 days)
- **Daily aggregation** — as required by Autoridade Tributária
- **Multi-country ready** — tax rules separated in `config.py`

## Try the demo

Run the demo to explore the application with realistic sample data — no real exchange files needed.

```bash
git clone https://github.com/gabridome/crypto-fifo-tracker.git
cd crypto-fifo-tracker

# Setup everything: venv, dependencies, demo data, FIFO calculation
python3 setup_demo.py

# Launch the web interface with the demo database (port 5003)
source venv/bin/activate
FIFO_DB=demo/DEMO_crypto_fifo.db FIFO_PORT=5003 python3 web/app.py
# Open http://127.0.0.1:5003
```

The demo creates 3 fictional exchanges (DEMO Alpha, DEMO Beta, DEMO Gamma) with
600 BUY and 300 SELL transactions spanning 2016–2025, using realistic BTC/EUR prices.
FIFO produces a mix of long-term (exempt) and short-term (taxable) gains.

> Demo data lives in `demo/`, your real data in `data/` — completely separate.
> Both web instances can run at the same time (ports 5002 and 5003).

## Production setup

```bash
# 1. Clone and initial setup
git clone https://github.com/YOUR_USERNAME/crypto-fifo-tracker.git
cd crypto-fifo-tracker
chmod +x setup.sh
./setup.sh              # creates venv, installs packages, initializes DB

# 2. Activate virtual environment
source venv/bin/activate

# 3. (Optional, recommended) install the enforcement layer
./scripts/setup-enforcement.sh   # installs ruff + git pre-commit hook

# 4. Launch the web interface
python3 web/app.py
# Open http://127.0.0.1:5002
```

### Enforcement layer (for contributors)

After `git clone`, run `./scripts/setup-enforcement.sh` once. It installs:

- `ruff` (Python linter) into the venv if missing
- A git `pre-commit` hook that **blocks** commits containing
  `except: pass`, ruff errors, or test failures
- Optionally, a symlink so the project's `before-coding` skill is available
  at user level for Claude Code

`.git/hooks/` is not versioned, so each fresh clone needs this step. To
bypass the hook in an emergency: `ALLOW_DIRTY_COMMIT=1 git commit ...` (logged).

The web interface guides you through the full workflow:

1. **Collect** — upload your exchange CSV files
2. **Import** — import each file into the database
3. **Status** — verify CSV ↔ DB consistency
4. **FIFO** — calculate FIFO lots and gains/losses
5. **Reports** — generate IRS Excel reports, run SQL queries

### Command-line workflow (alternative)

You can also run each step from the terminal:

```bash
# Import (one exchange at a time)
python3 importers/import_binance_with_fees.py data/binance.csv
python3 importers/verify_exchange_import.py "Binance"

# Calculate FIFO
python3 calculators/calculate_fifo.py

# Generate IRS report
python3 calculators/generate_irs_report.py 2025
```

## Project structure

```
crypto-fifo-tracker/
├── config.py                   ← Country/tax configuration
├── setup.sh                    ← Automated setup for production
├── generate_demo_data.py       ← Generate demo CSV files (900 transactions)
├── setup_demo.py               ← Build demo database from demo CSVs
├── web/
│   ├── app.py                  ← Flask web application
│   └── templates/              ← HTML templates (base, collect, import, status, fifo, reports, manual)
├── calculators/
│   ├── crypto_fifo_tracker.py  ← Core FIFO library
│   ├── calculate_fifo.py       ← FIFO calculation script
│   ├── generate_irs_report.py  ← IRS Excel report generator
│   └── *.sql                   ← SQL queries (runnable from Reports page)
├── importers/
│   ├── ecb_rates.py            ← USD→EUR conversion (ECB rates)
│   ├── crypto_prices.py        ← Crypto price lookup (CryptoCompare)
│   ├── import_standard_csv.py  ← Generic CSV importer
│   ├── import_binance_with_fees.py
│   └── ...                     ← One script per exchange
├── demo/                       ← Demo environment (separate from real data)
│   ├── DEMO_*.csv              ← Demo CSV files (tracked in git)
│   └── DEMO_crypto_fifo.db    ← Demo database (gitignored)
├── data/                       ← Your personal data (entirely gitignored)
│   ├── crypto_fifo.db          ← Production database
│   ├── eurusd.csv              ← ECB historical EUR/USD rates
│   ├── crypto_prices.csv       ← CryptoCompare daily prices
│   └── ...                     ← Your exchange CSV files
├── doc/
│   ├── schema.sql              ← Database DDL
│   ├── en/                     ← English documentation
│   └── pt/                     ← Portuguese documentation
└── tests/                      ← Test scripts
```

## How it works

### Import strategy: DELETE + reimport

Each importer reads a complete CSV file from one exchange and replaces all data for that exchange in the database. No risk of duplicates. One CSV file per exchange with the full transaction history.

### FIFO calculation

`calculate_fifo.py` processes all transactions from the very first one to the last, matching each SELL to the oldest available BUY (FIFO). It always recalculates the entire history — you cannot calculate only one year, because FIFO lots depend on the complete chain.

### Tax classification (Portugal)

| Holding period | Classification | Tax rate | Report form |
|----------------|---------------|----------|-------------|
| ≥ 365 days | Exempt | 0% | Anexo G1 Quadro 07 |
| < 365 days | Taxable | 28% flat | Anexo J Quadro 9.4A |

Exempt gains must still be declared. Failure to declare may result in the tax authority treating the funds as unjustified wealth.

### Fee handling

- `total_value` = gross amount (BEFORE fees)
- `fee_amount` = fee stored separately
- BUY: cost_basis = total_value + fee_amount
- SELL: proceeds = total_value − fee_amount

## Adapting to another country

Edit `config.py` to add your country's tax rules:

```python
COUNTRY = "DE"  # Switch active country

COUNTRY_PROFILES = {
    "DE": {
        "name": "Deutschland",
        "currency": "EUR",
        "timezone": "Europe/Berlin",
        "tax_rules": {
            "exempt_holding_days": 365,
            "short_term_rate": None,     # Taxed at personal income rate
            "declare_exempt": True,
        },
        ...
    },
}
```

See `CONTRIBUTING.md` for details on adding a new country or exchange.

## Documentation

| Document | Description |
|----------|-------------|
| [Quick start](doc/en/quickstart.md) | Step-by-step workflow |
| [Complete guide](doc/en/crypto_fifo_guide.md) | Import, FIFO, reports, verification queries |
| [Crypto-to-crypto](doc/en/crypto_to_crypto_guide.md) | Handling swaps (BTC→ETH, stablecoins) |
| [Audit trail](doc/en/audit_trail_guide.md) | Compliance, backups, data retention |
| [macOS setup](doc/en/macos_sqlite_setup_guide.md) | Setup from scratch on macOS |
| [Exchange CSV downloads](doc/en/howto_obtain_logs.md) | How to download data from each exchange |
| [Portuguese tax resources](doc/pt/recursos_fiscais.md) | Legislation, AT guides, deadlines |

## Requirements

- Python 3.11+
- No database server (SQLite is built into Python)
- Packages: `flask`, `pandas`, `pytz`, `openpyxl`, `requests`

## License

[MIT](LICENSE) — use it, modify it, share it.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines on adding exchanges, countries, or improvements.

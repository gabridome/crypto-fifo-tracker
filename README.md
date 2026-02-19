# Crypto FIFO Tracker

Open-source FIFO tracking system for cryptocurrency capital gains, designed for Portuguese IRS tax filing. Adaptable to other jurisdictions.

**[Documentação em Português](doc/pt/README.md)**

## Features

- **SQLite database** — single file, no server, portable
- **FIFO method** — mandatory under Portuguese tax law
- **Multi-exchange** — dedicated importers for Binance, Coinbase, Coinbase Prime, Bitstamp, Bitfinex, Kraken, Mt.Gox, TRT, Wirex, Revolut, and a generic CSV importer
- **EUR currency** — USD→EUR conversion via official ECB historical rates
- **IRS reports** — Excel with Anexo G1 (exempt, ≥365 days) and Anexo J (taxable, <365 days)
- **Daily aggregation** — as required by Autoridade Tributária
- **Multi-country ready** — tax rules separated in `config.py`

## Quick start

```bash
# 1. Clone and setup
git clone https://github.com/YOUR_USERNAME/crypto-fifo-tracker.git
cd crypto-fifo-tracker
chmod +x setup.sh
./setup.sh

# 2. Activate virtual environment
source venv/bin/activate

# 3. Place your exchange CSV files in data/

# 4. Import (one exchange at a time)
python3 importers/import_binance_with_fees.py
python3 importers/verify_exchange_import.py "Binance"

# 5. Calculate FIFO (~2 min for large datasets)
python3 calculators/calculate_fifo.py

# 6. Generate IRS report
python3 calculators/generate_irs_report.py 2025
```

## Project structure

```
crypto-fifo-tracker/
├── config.py                   ← Country/tax configuration
├── setup.sh                    ← Automated setup script
├── crypto_fifo.db              ← SQLite database (created by setup.sh)
├── calculators/
│   ├── crypto_fifo_tracker.py  ← Core FIFO library
│   ├── calculate_fifo.py       ← FIFO calculation script
│   └── generate_irs_report.py  ← IRS Excel report generator
├── importers/
│   ├── ecb_rates.py            ← USD→EUR conversion (ECB rates)
│   ├── import_binance_with_fees.py
│   ├── import_coinbase_prime.py
│   ├── import_bitstamp_with_fees.py
│   ├── import_kraken_with_fees.py
│   ├── import_standard_csv.py  ← Generic CSV importer
│   ├── verify_exchange_import.py
│   └── ...                     ← One script per exchange
├── data/
│   ├── eurusd.csv              ← ECB historical EUR/USD rates
│   ├── sample_transactions.csv ← Sample data for testing
│   └── ...                     ← Your exchange CSV files (gitignored)
├── reports/                    ← Generated reports (gitignored)
├── doc/
│   ├── en/                     ← English documentation
│   └── pt/                     ← Portuguese documentation
├── tests/                      ← Test scripts
└── backups/                    ← Database backups (gitignored)
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
- Packages: `pandas`, `pytz`, `openpyxl`, `requests`

## License

[MIT](LICENSE) — use it, modify it, share it.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines on adding exchanges, countries, or improvements.

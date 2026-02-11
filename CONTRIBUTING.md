# Contributing to Crypto FIFO Tracker

Thank you for your interest in contributing! This project helps people track cryptocurrency capital gains using the FIFO method for tax purposes.

## How to contribute

### Adding support for a new exchange

Each exchange has its own importer script in `importers/`. To add a new one:

1. **Create** `importers/import_EXCHANGE.py` following the pattern of existing importers
2. **Map** CSV columns to the `transactions` table fields (see `doc/IMPORT_MAPPING.md`)
3. **Handle fees** correctly:
   - `total_value` = gross amount BEFORE fees
   - `fee_amount` = fee stored separately
4. **Implement** the DELETE + reimport pattern (ask user before deleting)
5. **Add** a verification step at the end (transaction counts, totals)
6. **Document** the CSV download instructions in `doc/how_reports.md`
7. **Test** with sample data

Key rules for importers:
- All values must be in EUR. If the exchange uses USD, convert via `ecb_rates.py`
- Use the project timezone from `config.py`
- Script must run from the project root directory
- Never hardcode year filters — import the full CSV history

### Adding support for a new country

Tax rules are centralized in `config.py`. To add a new country:

1. **Add** a new entry to `COUNTRY_PROFILES` in `config.py`
2. **Set** the tax rules: exempt holding days, tax rate, whether exempt gains must be declared
3. **Set** the report format: form names, language, aggregation rules
4. **If needed**, create a country-specific report generator in `calculators/`
5. **Add** documentation in the country's language under `doc/<lang>/`

Example:
```python
"IT": {
    "name": "Italia",
    "currency": "EUR",
    "timezone": "Europe/Rome",
    "tax_rules": {
        "exempt_holding_days": 0,    # No holding period exemption
        "short_term_rate": 0.26,     # 26% flat rate
        "declare_exempt": False,
    },
    "report": {
        "exempt_form": None,
        "taxable_form": "Quadro RT",
        "language": "it",
        "aggregate_by_day": False,
    },
    ...
}
```

### Fixing bugs or improving existing code

1. **Fork** the repository
2. **Create** a branch: `git checkout -b fix/description`
3. **Make** your changes
4. **Test** with the sample data in `data/sample_*.csv`
5. **Submit** a pull request with a clear description

### Improving documentation

Documentation lives in `doc/`. Translations go in language-specific folders:
- `doc/en/` — English
- `doc/pt/` — Portuguese

## Code style

- Python 3.11+
- SQLite3 (no MySQL, no ORM)
- Use `?` placeholders (never f-strings) for SQL parameters
- All financial values in EUR
- Dates in ISO 8601 format
- Scripts run from the project root directory

## What NOT to commit

See `.gitignore`. In particular, **never commit**:
- `crypto_fifo.db` (personal financial data)
- `data/*.csv` (exchange transaction logs)
- `reports/*.xlsx` (generated reports)
- `backups/` and `supporting_documents/`

## License

By contributing, you agree that your contributions will be licensed under the MIT License.

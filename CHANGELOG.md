# Changelog

All notable changes to Crypto FIFO Tracker will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [1.0.0] — 2025-02-xx

### Added
- Initial public release
- SQLite database (single file, no server required)
- FIFO calculation engine supporting multiple cryptocurrencies
- Exchange importers: Binance, Coinbase, Coinbase Prime, Bitstamp, Bitfinex, Kraken, Mt.Gox, TRT, Wirex, Revolut, Binance Card, generic CSV
- USD→EUR conversion via ECB historical rates (`ecb_rates.py`)
- IRS report generator (Excel) with daily aggregation and Anexo G1 / Anexo J sheets
- Annual summary report (console)
- Import verification script
- Multi-country architecture via `config.py`
- Automated setup script (`setup.sh`)
- Documentation in English and Portuguese

### Notes
- Database migrated from MySQL to SQLite
- All values stored in EUR
- FIFO recalculates the entire history on each run (required for correctness)

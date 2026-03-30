"""
Crypto FIFO Tracker — Web Interface

A wizard-style local web app to guide the user through:
  1. Collect  — Upload exchange CSV files
  2. Import   — Import CSVs into the database
  3. Status   — Compare CSV files vs database
  4. FIFO     — Calculate FIFO lots and matches
  5. Reports  — Statistics, queries, Excel generation
  6. Manual   — Manual transaction entry (OTC, gifts, etc.)

Usage:
    python3 web/app.py
    Open http://127.0.0.1:5002
"""

import sqlite3
import csv
import os
import sys
import re
import glob
import json
import subprocess
import shutil
import hashlib
from datetime import datetime
from collections import defaultdict
import pytz
from flask import (Flask, render_template, request, redirect, url_for,
                   flash, jsonify, send_file)
from werkzeug.utils import secure_filename

# ── Project root detection ──────────────────────────────────
# web/app.py lives inside web/, so project root is one level up
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

try:
    from config import DATABASE_PATH, EXEMPT_HOLDING_DAYS, SHORT_TERM_RATE
except ImportError:
    DATABASE_PATH = os.path.join(PROJECT_ROOT, 'data', 'crypto_fifo.db')
    EXEMPT_HOLDING_DAYS = 365
    SHORT_TERM_RATE = 0.28

# DATA_DIR follows the database location: if FIFO_DB points to demo/,
# we serve CSV files from demo/ instead of data/
DATA_DIR = os.path.dirname(os.path.abspath(DATABASE_PATH))
REPORTS_DIR = os.path.join(DATA_DIR, 'reports')
BACKUPS_DIR = os.path.join(DATA_DIR, 'backups')

# SQL queries: use DATA_DIR/queries/ if it exists, otherwise calculators/
_custom_sql_dir = os.path.join(DATA_DIR, 'queries')
SQL_DIR = _custom_sql_dir if os.path.isdir(_custom_sql_dir) else os.path.join(PROJECT_ROOT, 'calculators')

app = Flask(__name__)
app.secret_key = 'crypto-fifo-local-dev'  # local only, no real security needed

# ── Helpers ─────────────────────────────────────────────────

def safe_path(base_dir, filename):
    """Return a safe path within base_dir, or raise ValueError on traversal attempt."""
    safe_name = secure_filename(filename)
    if not safe_name:
        raise ValueError(f"Invalid filename: {filename}")
    full_path = os.path.join(base_dir, safe_name)
    if not os.path.realpath(full_path).startswith(os.path.realpath(base_dir)):
        raise ValueError(f"Path traversal attempt: {filename}")
    return full_path


def get_db():
    """Get a database connection with Row factory."""
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def db_exists():
    return os.path.exists(DATABASE_PATH)

# Exchanges whose CSV data is in USD and requires EUR conversion via eurusd.csv
USD_EXCHANGES = {'Bitfinex', 'Coinbase Prime', 'Kraken', 'Mt.Gox'}

# Lazy-loaded crypto prices for status page (Wirex EUR valuation, etc.)
_crypto_prices_cache = None

def _get_crypto_prices():
    """Lazy-load CryptoPrices for status page parsers."""
    global _crypto_prices_cache
    if _crypto_prices_cache is None:
        prices_path = os.path.join(DATA_DIR, 'crypto_prices.csv')
        if os.path.exists(prices_path):
            try:
                from importers.crypto_prices import CryptoPrices
                _crypto_prices_cache = CryptoPrices(prices_path)
            except Exception:
                _crypto_prices_cache = False  # mark as attempted
    return _crypto_prices_cache if _crypto_prices_cache is not False else None

def check_eurusd():
    """
    Check eurusd.csv: existence, date coverage, freshness.
    Cross-reference with CSV files that need EUR conversion.
    Returns a dict with status info.
    """
    eurusd_path = os.path.join(DATA_DIR, 'eurusd.csv')
    result = {
        'exists': False,
        'path': eurusd_path,
        'rows': 0,
        'min_date': None,
        'max_date': None,
        'age_days': None,
        'needs_update': False,
        'gaps': [],          # exchanges whose dates fall outside eurusd coverage
        'ok': False,
    }

    if not os.path.exists(eurusd_path):
        # Check if any USD exchange CSVs exist
        for fp in glob.glob(os.path.join(DATA_DIR, '*.csv')):
            ex, _ = detect_exchange(os.path.basename(fp))
            if ex in USD_EXCHANGES:
                result['gaps'].append({
                    'exchange': ex,
                    'problem': 'eurusd.csv missing — cannot convert USD to EUR',
                })
        return result

    result['exists'] = True

    # Parse eurusd.csv to get date range
    try:
        rates = {}
        with open(eurusd_path, 'r', encoding='utf-8-sig') as f:
            reader = csv.DictReader(f)
            for row in reader:
                result['rows'] += 1
                # Try common column names
                date_str = None
                for col in ('Date', 'date', 'DATE'):
                    if col in row and row[col].strip():
                        date_str = row[col].strip()
                        break
                if date_str:
                    try:
                        d = datetime.strptime(date_str[:10], '%Y-%m-%d')
                        rates[date_str[:10]] = d
                    except ValueError:
                        pass

        if rates:
            all_dates = sorted(rates.values())
            result['min_date'] = all_dates[0].strftime('%Y-%m-%d')
            result['max_date'] = all_dates[-1].strftime('%Y-%m-%d')
            result['age_days'] = (datetime.now() - all_dates[-1]).days
            result['needs_update'] = result['age_days'] > 30

    except Exception:
        pass

    # Cross-reference with USD exchange CSVs
    for fp in sorted(glob.glob(os.path.join(DATA_DIR, '*.csv'))):
        basename = os.path.basename(fp)
        if basename == 'eurusd.csv' or basename.startswith('sample_'):
            continue
        ex, _ = detect_exchange(basename)
        if ex not in USD_EXCHANGES:
            continue

        # Get date range of this CSV (try deep parse, fallback to generic scan)
        parsed = parse_csv_deep(fp, ex)
        csv_min = parsed.get('min_date')
        csv_max = parsed.get('max_date')

        if not csv_min or not csv_max:
            csv_min, csv_max = _scan_csv_date_range(fp)

        if not csv_min or not csv_max:
            continue

        # Check coverage
        problems = []
        if result['min_date'] and csv_min < result['min_date']:
            problems.append(f'CSV starts {csv_min}, rates start {result["min_date"]}')
        if result['max_date'] and csv_max > result['max_date']:
            problems.append(f'CSV ends {csv_max}, rates end {result["max_date"]}')

        if problems:
            result['gaps'].append({
                'exchange': ex,
                'file': basename,
                'csv_range': f'{csv_min} → {csv_max}',
                'problem': '; '.join(problems),
            })

    result['ok'] = result['exists'] and not result['gaps'] and not result['needs_update']
    return result

def get_wizard_status():
    """Compute completion status for each wizard step."""
    status = {
        'collect': 'empty',
        'import': 'empty',
        'status': 'empty',
        'fifo': 'empty',
        'reports': 'empty',
    }

    # 1. Collect: are there CSV files in data/?
    csv_files = glob.glob(os.path.join(DATA_DIR, '*.csv'))
    exchange_csvs = [f for f in csv_files
                     if os.path.basename(f) not in ('eurusd.csv',)
                     and not os.path.basename(f).startswith('sample_')]
    if exchange_csvs:
        status['collect'] = 'complete'

    if not db_exists():
        return status

    conn = get_db()
    try:
        # 2. Import: are there transactions in DB?
        tx_count = conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
        if tx_count > 0:
            status['import'] = 'complete'
            status['status'] = 'partial'  # can always re-check

        # 3. FIFO: are there lots and matches?
        lot_count = conn.execute("SELECT COUNT(*) FROM fifo_lots").fetchone()[0]
        match_count = conn.execute("SELECT COUNT(*) FROM sale_lot_matches").fetchone()[0]
        if lot_count > 0 and match_count > 0:
            status['fifo'] = 'complete'
            status['reports'] = 'partial'  # can generate reports now

        # Check if reports exist
        if os.path.exists(REPORTS_DIR):
            xlsx_files = glob.glob(os.path.join(REPORTS_DIR, '*.xlsx'))
            if xlsx_files:
                status['reports'] = 'complete'

    except sqlite3.OperationalError:
        pass
    finally:
        conn.close()

    return status


# ── Exchange CSV detection ──────────────────────────────────

EXCHANGE_PATTERNS = [
    (r'binance_card',               'Binance Card',    'importers/import_binance_card.py'),
    (r'binance_otc',                'Binance OTC',     'importers/import_standard_csv.py'),
    (r'binance_trade_history',      'Binance',         'importers/import_binance_with_fees.py'),
    (r'binance',                    'Binance',         'importers/import_binance_with_fees.py'),
    (r'coinbaseprime|coinbase_prime', 'Coinbase Prime', 'importers/import_coinbase_prime.py'),
    (r'coinbase',                   'Coinbase',        'importers/import_coinbase_standalone.py'),
    (r'bitstamp',                   'Bitstamp',        'importers/import_bitstamp_with_fees.py'),
    (r'bitfinex',                   'Bitfinex',        'importers/import_bitfinex_ecb.py'),
    (r'kraken',                     'Kraken',          'importers/import_kraken_with_fees.py'),
    (r'mtgox|mt_gox',              'Mt.Gox',          'importers/import_mtgox_with_fees.py'),
    (r'revolut',                    'Revolut',         'importers/import_revolut.py'),
    (r'bybit',                      'Bybit',           'importers/import_bybit.py'),
    (r'wirex',                      'Wirex',           'importers/import_wirex.py'),
    (r'trt',                        'TRT',             'importers/import_trt_with_fees.py'),
    (r'changely',                   'changely',        'importers/import_standard_csv.py'),
    (r'coinpal',                    'Coinpal',         'importers/import_standard_csv.py'),
    (r'gdtre',                      'GDTRE',           'importers/import_standard_csv.py'),
    (r'inheritance',                'Inheritance',     'importers/import_standard_csv.py'),
    (r'otc',                        'OTC',             'importers/import_standard_csv.py'),
    (r'(?i)demo_alpha',             'DEMO Alpha',      'importers/import_standard_csv.py'),
    (r'(?i)demo_beta',              'DEMO Beta',       'importers/import_standard_csv.py'),
    (r'(?i)demo_gamma',             'DEMO Gamma',      'importers/import_standard_csv.py'),
    (r'(?i)demo',                   'DEMO',            'importers/import_standard_csv.py'),
]

EXCHANGE_INSTRUCTIONS = {
    'Binance': {
        'steps': [
            'Log into Binance',
            'Go to Orders → Trade History',
            'Click "Export" → select date range → Generate',
            'Download the CSV file',
        ],
        'url': 'https://www.binance.com/en/my/orders/exchange/tradeorder',
        'expected_file': 'binance_trade_history_all.csv',
    },
    'Coinbase': {
        'steps': [
            'Log into Coinbase',
            'Go to Taxes → Documents (or Settings → Activity)',
            'Click "Generate report" → Transaction history',
            'Download CSV',
        ],
        'url': 'https://www.coinbase.com/settings/reports',
        'expected_file': 'coinbase_history.csv',
    },
    'Coinbase Prime': {
        'steps': [
            'Log into Coinbase Prime',
            'Go to Reporting → Orders',
            'Select date range → Download CSV',
        ],
        'url': 'https://prime.coinbase.com',
        'expected_file': 'coinbaseprime_orders.csv',
    },
    'Bitstamp': {
        'steps': [
            'Log into Bitstamp',
            'Go to Transactions → All',
            'Click "Export" → CSV',
        ],
        'url': 'https://www.bitstamp.net/account/transactions/',
        'expected_file': 'bitstamp_history.csv',
    },
    'Kraken': {
        'steps': [
            'Log into Kraken',
            'Go to History → Export',
            'Select "Ledgers" → CSV → Submit',
        ],
        'url': 'https://www.kraken.com/u/history/export',
        'expected_file': 'kraken_ledgers.csv',
    },
    'Bitfinex': {
        'steps': [
            'Log into Bitfinex',
            'Go to Reports → Trades',
            'Select date range → Export CSV',
        ],
        'url': 'https://report.bitfinex.com',
        'expected_file': 'bitfinex_trades.csv',
    },
    'Revolut': {
        'steps': [
            'Open Revolut app or web',
            'Go to Crypto → Statement',
            'Select date range → Download CSV',
        ],
        'url': 'https://app.revolut.com',
        'expected_file': 'revolut_crypto.csv',
    },
}


# ── Per-exchange field mapping (CSV col → DB col) ──────────

EXCHANGE_FIELD_MAP = {
    'Binance': {
        'columns': [
            ('Date(UTC)',  'transaction_date', 'Parsed as ISO datetime'),
            ('Side',       'transaction_type',  'BUY or SELL'),
            ('Pair',       'cryptocurrency',    'BTCEUR → BTC (suffix stripped)'),
            ('Executed',   'amount',            '"0.02776BTC" → 0.02776'),
            ('Price',      'price_per_unit',    'Price per unit in EUR'),
            ('Amount',     'total_value',       '"1731.99EUR" → 1731.99 (gross)'),
            ('Fee',        'fee_amount',        '"1.73EUR" → 1.73 (separate)'),
        ],
        'notes': 'Only BTCEUR pairs are imported. Other pairs (ETHEUR, etc.) are skipped. Currency suffixes are stripped automatically.',
        'type_values': {'BUY': 'BUY', 'SELL': 'SELL'},
    },
    'Coinbase': {
        'columns': [
            ('Timestamp',              'transaction_date', 'Parsed from "Oct 15, 2024, 3:22 PM"'),
            ('Transaction Type',       'transaction_type',  'Buy, Sell, Advanced Trade Buy/Sell'),
            ('Asset',                  'cryptocurrency',    'BTC, ETH, etc.'),
            ('Quantity Transacted',    'amount',            'Crypto amount'),
            ('Spot Price at Transaction', 'price_per_unit', 'EUR price at time of trade'),
            ('Total (inclusive of fees)', 'total_value',    'Gross value including spread'),
            ('—',                      'fee_amount',        'Calculated from spread (Total - Subtotal)'),
        ],
        'notes': 'Coinbase includes spread/fees inside the total. The importer separates them. Only Buy/Sell types are imported.',
        'type_values': {'Buy': 'BUY', 'Advanced Trade Buy': 'BUY', 'Sell': 'SELL', 'Advanced Trade Sell': 'SELL'},
    },
    'Bitstamp': {
        'columns': [
            ('Datetime',   'transaction_date', 'Parsed as datetime'),
            ('Type',       'transaction_type',  'Buy/Sell/Market buy/Market sell'),
            ('Amount',     'amount',            'Crypto amount'),
            ('Value',      'total_value',       'EUR value'),
            ('Rate',       'price_per_unit',    'Price per unit'),
            ('Fee',        'fee_amount',        'Trading fee in EUR'),
        ],
        'notes': 'Both regular and market orders are imported.',
        'type_values': {'Buy': 'BUY', 'Market buy': 'BUY', 'Sell': 'SELL', 'Market sell': 'SELL'},
    },
    'Kraken': {
        'columns': [
            ('time',       'transaction_date', 'UTC timestamp'),
            ('type',       'transaction_type',  'buy/sell'),
            ('asset',      'cryptocurrency',    'XXBT → BTC, XETH → ETH'),
            ('amount',     'amount',            'Crypto amount'),
            ('—',          'price_per_unit',    'Derived from cost / amount'),
            ('cost',       'total_value',       'Total cost in EUR'),
            ('fee',        'fee_amount',        'Trading fee'),
        ],
        'notes': 'Kraken uses internal asset names (XXBT, XETH). The importer maps them to standard symbols.',
        'type_values': {'buy': 'BUY', 'sell': 'SELL'},
    },
    'Bitfinex': {
        'columns': [
            ('Date',       'transaction_date', 'Parsed as datetime'),
            ('Type',       'transaction_type',  'Positive amount = BUY, negative = SELL'),
            ('Amount',     'amount',            'Absolute value, sign determines type'),
            ('Price',      'price_per_unit',    'USD price → converted to EUR via ECB'),
            ('—',          'total_value',       'amount × EUR price'),
            ('Fee',        'fee_amount',        'USD fee → converted to EUR'),
        ],
        'notes': 'Bitfinex trades are in USD. The importer converts to EUR using ECB daily rates from eurusd.csv.',
        'type_values': {},
    },
    'Standard Format': {
        'columns': [
            ('transaction_date',   'transaction_date',  'ISO format (2024-01-15T10:00:00+00:00)'),
            ('transaction_type',   'transaction_type',   'BUY, SELL, DEPOSIT, WITHDRAWAL'),
            ('exchange_name',      'exchange_name',      'Source name (OTC, Gift, Inheritance, etc.)'),
            ('cryptocurrency',     'cryptocurrency',     'BTC, ETH, USDC, etc.'),
            ('amount',             'amount',             'Quantity of crypto'),
            ('price_per_unit',     'price_per_unit',     'EUR per unit (optional)'),
            ('total_value',        'total_value',        'Total EUR value'),
            ('fee_amount',         'fee_amount',         'Fee in EUR (optional, default 0)'),
            ('notes',              'notes',              'Free text (optional)'),
        ],
        'notes': 'Used for OTC, gifts, inheritance, airdrops, and any source without a dedicated importer. Download the template from the Collect page.',
        'type_values': {'BUY': 'BUY', 'SELL': 'SELL', 'DEPOSIT': 'DEPOSIT', 'WITHDRAWAL': 'WITHDRAWAL'},
    },
}


# ── CSV deep parser ────────────────────────────────────────

# Standard CSV format used by import_standard_csv.py
_STANDARD_CSV_RULES = {
    'date_col': 'transaction_date',
    'type_col': 'transaction_type',
    'amount_col': 'amount',
    'value_col': 'total_value',
    'fee_col': 'fee_amount',
    'buy_types': ['BUY'],
    'sell_types': ['SELL'],
    'strip_suffix': False,
    'currency_col': 'currency',
    'usd_currencies': ['USD'],
}

CSV_PARSE_RULES = {
    # All standard-CSV exchanges
    'Binance OTC': _STANDARD_CSV_RULES,
    'changely': _STANDARD_CSV_RULES,
    'Coinpal': _STANDARD_CSV_RULES,
    'GDTRE': _STANDARD_CSV_RULES,
    'Inheritance': _STANDARD_CSV_RULES,
    'DEMO Alpha': _STANDARD_CSV_RULES,
    'DEMO Beta': _STANDARD_CSV_RULES,
    'DEMO Gamma': _STANDARD_CSV_RULES,
    'OTC': _STANDARD_CSV_RULES,
    # Exchange-specific formats
    'Binance': {
        'date_col': 'Date(UTC)',
        'type_col': 'Side',
        'amount_col': 'Executed',
        'value_col': 'Amount',
        'fee_col': 'Fee',
        'buy_types': ['BUY'],
        'sell_types': ['SELL'],
        'strip_suffix': True,
        'pair_col': 'Pair',           # column that identifies the trading pair
        'usd_pairs': ['BTCUSDT', 'BTCBUSD'],  # pairs needing USD→EUR conversion
    },
    'Binance Card': {
        'date_col': 'datetime_tz_CET',
        'type_col': 'type',
        'amount_col': 'sent_amount',
        'value_col': 'received_amount',
        'fee_col': 'differenza',
        'buy_types': [],
        'sell_types': ['Sell'],
        'strip_suffix': False,
        'tz_source': 'CET',  # dates in CSV are CET, importer converts to UTC
    },
    'Coinbase': {
        'date_col': 'Timestamp',
        'type_col': 'Transaction Type',
        'amount_col': 'Quantity Transacted',
        'value_col': 'Subtotal',
        'fee_col': 'Fees and/or Spread',
        'buy_types': ['Buy', 'Advanced Trade Buy'],
        'sell_types': ['Sell', 'Advanced Trade Sell'],
        'strip_suffix': False,
        'asset_col': 'Asset',         # filter by asset
        'asset_filter': ['BTC'],       # only BTC (skip USDC etc.)
    },
    'Coinbase Prime': {
        'date_col': 'initiated time',
        'type_col': 'side',
        'amount_col': 'filled base quantity',
        'value_col': 'filled quote quantity',
        'fee_col': 'total fees and commissions',
        'buy_types': ['BUY'],
        'sell_types': ['SELL'],
        'strip_suffix': False,
        'all_usd': True,              # values in USD, convert via ECB
        'type_filter_col': 'status',   # only Completed orders
        'type_filter_val': 'Completed',
        'pair_col': 'market',
        'pair_filter': ['BTC/USD'],    # only BTC market
    },
    'Bitstamp': {
        'date_col': 'Datetime',
        'type_col': 'Sub Type',
        'amount_col': 'Amount',
        'value_col': 'Value',
        'fee_col': 'Fee',
        'buy_types': ['Buy'],
        'sell_types': ['Sell'],
        'strip_suffix': True,    # values have suffixes: "5.00 BTC", "45.48 USD"
        'all_usd': True,         # all monetary values are in USD, convert via ECB
        'type_filter_col': 'Type',  # only process rows where Type == 'Market'
        'type_filter_val': 'Market',
    },
    'Kraken': {
        'paired_ledger': True,     # each trade = 2 rows (BTC + EUR) joined by refid
        'date_col': 'time',
        'refid_col': 'refid',
        'asset_col': 'asset',
        'amount_col': 'amount',
        'fee_col': 'fee',
        'type_filter_col': 'type',
        'type_filter_val': 'trade',
        'crypto_asset': 'BTC',
        'fiat_asset': 'EUR',
    },
    'Bitfinex': {
        'date_col': 'DATE',
        'type_col': '_amount_sign',  # no type column; derive from AMOUNT sign
        'amount_col': 'AMOUNT',
        'value_col': 'PRICE',
        'fee_col': 'FEE',
        'buy_types': ['BUY'],
        'sell_types': ['SELL'],
        'strip_suffix': False,
        'value_is_unit_price': True,   # total = abs(amount) × price
        'fee_is_crypto': True,         # fee in BTC, convert: abs(fee) × price_eur
        'pair_col': 'PAIR',
        'pair_filter': ['BTC/EUR', 'BTC/USD'],
        'usd_pairs': ['BTC/USD'],
    },
    'Mt.Gox': {
        'date_col': 'Date',
        'type_col': 'Type',
        'amount_col': 'Bitcoins',
        'value_col': 'Money',
        'fee_col': 'Bitcoin_Fee',
        'buy_types': ['buy'],
        'sell_types': ['sell'],
        'strip_suffix': False,
        'fee_is_crypto': True,         # fee in BTC, convert via price
        'dedup_col': 'ID',            # skip duplicate rows by ID
        'currency_col': 'Currency',    # per-row currency detection
        'usd_currencies': ['USD'],     # rows with these currencies → convert via ECB
    },
    'Revolut': {
        'date_col': 'Date',
        'type_col': 'Type',
        'amount_col': 'Quantity',
        'value_col': 'Value',
        'fee_col': 'Fees',
        'buy_types': ['Buy'],
        'sell_types': ['Sell'],
        'strip_suffix': False,
        'asset_col': 'Symbol',
        'asset_filter': ['BTC'],
    },
    'TRT': {
        'grouped_trade': True,
        'date_col': 'Date',
        'type_col': 'Type',
        'currency_col': 'Currency',
        'value_col': 'Price (cents)',
        'desc_col': 'Description',
        'desc_filter': 'Trade Bitcoin with Euro',
        'cents_factor': 100,
        'satoshi_factor': 100_000_000,
        'buy_crypto_type': 'acquired_currency_from_fund',
        'buy_fiat_type': 'bought_currency_from_fund',
        'sell_crypto_type': 'released_currency_to_fund',
        'sell_fiat_type': 'sold_currency_to_fund',
        'fee_type': 'paid_commission',
        'crypto_asset': 'BTC',
        'fiat_asset': 'EUR',
    },
    'Bybit': {
        'date_col': 'Time(UTC)',
        'type_col': '_amount_sign',
        'amount_col': 'Quantity',
        'value_col': 'Filled Price',
        'fee_col': 'Fee Paid',
        'buy_types': [],
        'sell_types': [],
        'strip_suffix': False,
        'value_is_unit_price': True,
        'type_filter_col': 'Type',
        'type_filter_val': 'TRADE',
        'asset_col': 'Currency',
        'asset_filter': ['BTC'],
        'skip_first_line': True,
        'aggregate_by_time': True,  # Bybit CSV has individual fills; importer aggregates by timestamp
    },
    'Wirex': {
        'date_col': 'Completed Date',
        'type_col': 'Type',
        'amount_col': 'Amount',
        'value_col': None,
        'fee_col': None,
        'buy_types': [],
        'sell_types': ['Card Payment'],
        'strip_suffix': False,
        'asset_col': 'Account Currency',
        'asset_filter': ['BTC'],
    },
}


def _scan_csv_date_range(filepath):
    """
    Lightweight: scan any CSV for dates regardless of exchange.
    Tries all common date column names and date formats.
    Returns (min_date_str, max_date_str) or (None, None).
    """
    date_cols = ['Date', 'date', 'DATE', 'Date(UTC)', 'Timestamp', 'Datetime',
                 'time', 'Created at', 'Started Date', 'Transaction Date']
    dates = []

    try:
        with open(filepath, 'r', encoding='utf-8-sig') as f:
            sample = f.read(4096)
            f.seek(0)
            try:
                dialect = csv.Sniffer().sniff(sample)
            except csv.Error:
                dialect = csv.excel

            reader = csv.DictReader(f, dialect=dialect)
            if not reader.fieldnames:
                return None, None

            # Find the first matching date column
            headers = [h.strip() for h in reader.fieldnames]
            found_col = None
            for col in date_cols:
                if col in headers:
                    found_col = col
                    break
            if not found_col:
                return None, None

            for row in reader:
                row = {k.strip(): v for k, v in row.items() if k}
                d = _parse_date(row.get(found_col, ''))
                if d:
                    dates.append(d)

    except Exception:
        return None, None

    if dates:
        return min(dates).strftime('%Y-%m-%d'), max(dates).strftime('%Y-%m-%d')
    return None, None


def _strip_currency(val):
    """'0.027BTC' or '1731.99EUR' → float."""
    if not val:
        return 0.0
    return float(re.sub(r'[A-Za-z]+$', '', str(val).strip()) or 0)


def _safe_float(val):
    if not val or val == '':
        return 0.0
    try:
        cleaned = re.sub(r'[^\d.\-]', '', str(val))
        return float(cleaned) if cleaned else 0.0
    except (ValueError, TypeError):
        return 0.0


def _parse_date(s):
    if not s:
        return None
    s = str(s).strip()
    # Normalize unicode whitespace (Revolut uses U+202F narrow no-break space)
    s = re.sub(r'[\u00A0\u202F\u2009\u200A]+', ' ', s)
    # Strip non-ASCII chars (handles double-encoded UTF-8 from Revolut)
    s = re.sub(r'[^\x00-\x7F]+', ' ', s)
    s = re.sub(r'\s+', ' ', s).strip()
    # Strip trailing timezone abbreviations: "2016-02-27 17:42:24 UTC" → "2016-02-27 17:42:24"
    s = re.sub(r'\s+(?:UTC|GMT|CET|CEST)$', '', s)
    # Normalize abbreviated months with period: "Nov." → "Nov", "Dec." → "Dec"
    s_norm = re.sub(r'\b([A-Z][a-z]{2})\.\s', r'\1 ', s)
    for fmt in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%dT%H:%M:%S', '%Y-%m-%dT%H:%M:%S%z',
                '%Y-%m-%d-%H:%M:%S', '%Y-%m-%d', '%d/%m/%Y %H:%M:%S', '%d-%m-%Y %H:%M:%S',
                '%d %b %Y, %H:%M:%S', '%b %d, %Y, %I:%M:%S %p', '%b %d, %Y, %I:%M %p', '%b %d, %Y'):
        try:
            return datetime.strptime(s_norm, fmt)
        except (ValueError, TypeError):
            continue
    try:
        return datetime.fromisoformat(s_norm.replace('Z', '+00:00'))
    except (ValueError, TypeError):
        return None


def _parse_paired_ledger_deep(filepath, rules, result):
    """Parse paired-row ledger (Kraken) into aggregate stats."""
    from collections import defaultdict
    try:
        with open(filepath, 'r', encoding='utf-8-sig') as f:
            reader = csv.DictReader(f)
            trades = defaultdict(dict)
            filter_col = rules.get('type_filter_col')
            filter_val = rules.get('type_filter_val')
            crypto = rules['crypto_asset']
            fiat = rules['fiat_asset']

            for row in reader:
                row = {k.strip(): v for k, v in row.items() if k}
                result['total_rows'] += 1
                if filter_col and row.get(filter_col, '').strip() != filter_val:
                    continue
                refid = row.get(rules['refid_col'], '').strip()
                asset = row.get(rules['asset_col'], '').strip()
                amount = _safe_float(row.get(rules['amount_col'], ''))
                fee = _safe_float(row.get(rules['fee_col'], ''))
                d = _parse_date(row.get(rules['date_col'], ''))
                if asset == crypto:
                    trades[refid]['crypto_amount'] = amount
                    trades[refid]['date'] = d
                elif asset == fiat:
                    trades[refid]['fiat_amount'] = amount
                    trades[refid]['fiat_fee'] = fee
                    if 'date' not in trades[refid]:
                        trades[refid]['date'] = d

            dates = []
            for data in trades.values():
                if 'crypto_amount' not in data or 'fiat_amount' not in data:
                    continue
                ca = data['crypto_amount']
                fa = data['fiat_amount']
                fee = data.get('fiat_fee', 0)
                d = data.get('date')
                if ca > 0 and fa < 0:  # BUY
                    result['buy_count'] += 1
                    result['buy_value'] += abs(fa)
                elif ca < 0 and fa > 0:  # SELL
                    result['sell_count'] += 1
                    result['sell_value'] += abs(fa)
                else:
                    continue
                result['total_fees'] += abs(fee)
                if d:
                    dates.append(d)

            if dates:
                result['min_date'] = min(dates).strftime('%Y-%m-%d')
                result['max_date'] = max(dates).strftime('%Y-%m-%d')
    except Exception:
        pass
    return result


def _parse_paired_ledger_rows(filepath, rules):
    """Parse paired-row ledger (Kraken) into individual trade rows."""
    from collections import defaultdict
    try:
        with open(filepath, 'r', encoding='utf-8-sig') as f:
            reader = csv.DictReader(f)
            trades = defaultdict(dict)
            filter_col = rules.get('type_filter_col')
            filter_val = rules.get('type_filter_val')
            crypto = rules['crypto_asset']
            fiat = rules['fiat_asset']
            line_map = {}  # refid → first CSV line number

            for line_num, row in enumerate(reader, 2):
                row = {k.strip(): v for k, v in row.items() if k}
                if filter_col and row.get(filter_col, '').strip() != filter_val:
                    continue
                refid = row.get(rules['refid_col'], '').strip()
                asset = row.get(rules['asset_col'], '').strip()
                amount = _safe_float(row.get(rules['amount_col'], ''))
                fee = _safe_float(row.get(rules['fee_col'], ''))
                d = _parse_date(row.get(rules['date_col'], ''))
                date_str = row.get(rules['date_col'], '').strip()
                if refid not in line_map:
                    line_map[refid] = line_num
                if asset == crypto:
                    trades[refid]['crypto_amount'] = amount
                    trades[refid]['date'] = d
                    trades[refid]['date_str'] = date_str
                elif asset == fiat:
                    trades[refid]['fiat_amount'] = amount
                    trades[refid]['fiat_fee'] = fee
                    if 'date' not in trades[refid]:
                        trades[refid]['date'] = d
                        trades[refid]['date_str'] = date_str

            rows = []
            for refid, data in trades.items():
                if 'crypto_amount' not in data or 'fiat_amount' not in data:
                    continue
                ca = data['crypto_amount']
                fa = data['fiat_amount']
                fee = abs(data.get('fiat_fee', 0))
                d = data.get('date')
                if ca > 0 and fa < 0:
                    norm_type = 'BUY'
                elif ca < 0 and fa > 0:
                    norm_type = 'SELL'
                else:
                    continue
                rows.append({
                    'line': line_map.get(refid, 0),
                    'date_str': data.get('date_str', ''),
                    'date': d.strftime('%Y-%m-%d %H:%M') if d else None,
                    'date_day': d.strftime('%Y-%m-%d') if d else None,
                    'type_raw': norm_type,
                    'type': norm_type,
                    'is_trade': True,
                    'pair': f'{crypto}/{fiat}',
                    'amount': abs(ca),
                    'value': abs(fa),
                    'fee': fee,
                })
            return sorted(rows, key=lambda r: r['date_str'])
    except Exception:
        return []


def _parse_trt_grouped_deep(filepath, rules, result):
    """Parse TRT multi-line grouped trades into aggregate stats."""
    from collections import defaultdict
    try:
        with open(filepath, 'r', encoding='utf-8-sig') as f:
            reader = csv.DictReader(f)
            desc_filter = rules['desc_filter']
            crypto = rules['crypto_asset']
            fiat = rules['fiat_asset']
            cents = rules['cents_factor']
            satoshi = rules['satoshi_factor']
            buy_crypto_t = rules['buy_crypto_type']
            buy_fiat_t = rules['buy_fiat_type']
            sell_crypto_t = rules['sell_crypto_type']
            sell_fiat_t = rules['sell_fiat_type']
            fee_t = rules['fee_type']

            # Group by (date, description)
            groups = defaultdict(list)
            for row in reader:
                row = {k.strip(): v for k, v in row.items() if k}
                result['total_rows'] += 1
                desc = row.get(rules['desc_col'], '').strip()
                if desc_filter not in desc:
                    continue
                date_str = row.get(rules['date_col'], '').strip()
                groups[(date_str, desc)].append(row)

            dates = []
            for (date_str, desc), rows_g in groups.items():
                btc_amount = 0
                eur_amount = 0
                fee_eur = 0
                trade_type = None
                for r in rows_g:
                    cur = r.get(rules['currency_col'], '').strip()
                    t = r.get(rules['type_col'], '').strip()
                    val = _safe_float(r.get(rules['value_col'], ''))
                    if t == fee_t and cur == fiat:
                        fee_eur += val / cents
                    elif t == buy_crypto_t and cur == crypto:
                        btc_amount = val / satoshi
                        trade_type = 'BUY'
                    elif t == buy_fiat_t and cur == fiat:
                        eur_amount = val / cents
                    elif t == sell_crypto_t and cur == crypto:
                        btc_amount = val / satoshi
                        trade_type = 'SELL'
                    elif t == sell_fiat_t and cur == fiat:
                        eur_amount = val / cents

                if trade_type and btc_amount > 0 and eur_amount > 0:
                    if trade_type == 'BUY':
                        result['buy_count'] += 1
                        result['buy_value'] += eur_amount
                    else:
                        result['sell_count'] += 1
                        result['sell_value'] += eur_amount
                    result['total_fees'] += fee_eur
                    d = _parse_date(date_str)
                    if d:
                        dates.append(d)

            if dates:
                result['min_date'] = min(dates).strftime('%Y-%m-%d')
                result['max_date'] = max(dates).strftime('%Y-%m-%d')
    except Exception:
        pass
    return result


def _parse_trt_grouped_rows(filepath, rules):
    """Parse TRT multi-line grouped trades into individual trade rows."""
    from collections import defaultdict
    try:
        with open(filepath, 'r', encoding='utf-8-sig') as f:
            reader = csv.DictReader(f)
            desc_filter = rules['desc_filter']
            crypto = rules['crypto_asset']
            fiat = rules['fiat_asset']
            cents = rules['cents_factor']
            satoshi = rules['satoshi_factor']
            buy_crypto_t = rules['buy_crypto_type']
            buy_fiat_t = rules['buy_fiat_type']
            sell_crypto_t = rules['sell_crypto_type']
            sell_fiat_t = rules['sell_fiat_type']
            fee_t = rules['fee_type']

            groups = defaultdict(lambda: {'rows': [], 'first_line': 0})
            for line_num, row in enumerate(reader, 2):
                row = {k.strip(): v for k, v in row.items() if k}
                desc = row.get(rules['desc_col'], '').strip()
                if desc_filter not in desc:
                    continue
                date_str = row.get(rules['date_col'], '').strip()
                key = (date_str, desc)
                groups[key]['rows'].append(row)
                if groups[key]['first_line'] == 0:
                    groups[key]['first_line'] = line_num

            result_rows = []
            for (date_str, desc), g in groups.items():
                btc_amount = 0
                eur_amount = 0
                fee_eur = 0
                trade_type = None
                for r in g['rows']:
                    cur = r.get(rules['currency_col'], '').strip()
                    t = r.get(rules['type_col'], '').strip()
                    val = _safe_float(r.get(rules['value_col'], ''))
                    if t == fee_t and cur == fiat:
                        fee_eur += val / cents
                    elif t == buy_crypto_t and cur == crypto:
                        btc_amount = val / satoshi
                        trade_type = 'BUY'
                    elif t == buy_fiat_t and cur == fiat:
                        eur_amount = val / cents
                    elif t == sell_crypto_t and cur == crypto:
                        btc_amount = val / satoshi
                        trade_type = 'SELL'
                    elif t == sell_fiat_t and cur == fiat:
                        eur_amount = val / cents

                if trade_type and btc_amount > 0 and eur_amount > 0:
                    d = _parse_date(date_str)
                    result_rows.append({
                        'line': g['first_line'],
                        'date_str': date_str,
                        'date': d.strftime('%Y-%m-%d %H:%M') if d else None,
                        'date_day': d.strftime('%Y-%m-%d') if d else None,
                        'type_raw': trade_type,
                        'type': trade_type,
                        'is_trade': True,
                        'pair': f'{crypto}/{fiat}',
                        'amount': btc_amount,
                        'value': eur_amount,
                        'fee': fee_eur,
                    })
            return sorted(result_rows, key=lambda r: r['date_str'])
    except Exception:
        return []


def parse_csv_deep(filepath, exchange):
    """Parse a CSV and extract comparable stats."""
    result = {
        'buy_count': 0, 'sell_count': 0,
        'buy_value': 0.0, 'sell_value': 0.0,
        'total_fees': 0.0,
        'min_date': None, 'max_date': None,
        'total_rows': 0,
        'parse_errors': 0,
    }

    rules = CSV_PARSE_RULES.get(exchange)
    if not rules:
        # Fallback: just count rows
        try:
            with open(filepath, 'r', encoding='utf-8-sig') as f:
                result['total_rows'] = sum(1 for _ in f) - 1
        except Exception:
            pass
        return result

    # --- Paired ledger mode (Kraken: each trade = 2 rows joined by refid) ---
    if rules.get('paired_ledger'):
        return _parse_paired_ledger_deep(filepath, rules, result)

    # --- Grouped trade mode (TRT: each trade = 3-4 rows grouped by date+desc) ---
    if rules.get('grouped_trade'):
        return _parse_trt_grouped_deep(filepath, rules, result)

    # Load ECB rates lazily if this exchange has USD pairs or all_usd
    ecb = None
    pair_col = rules.get('pair_col')
    usd_pairs = set(rules.get('usd_pairs', []))
    all_usd = rules.get('all_usd', False)
    currency_col = rules.get('currency_col')
    usd_currencies = set(rules.get('usd_currencies', []))
    if (pair_col and usd_pairs) or all_usd or (currency_col and usd_currencies):
        try:
            from importers.ecb_rates import ECBRates
            ecb = ECBRates(os.path.join(DATA_DIR, 'eurusd.csv'))
        except Exception:
            pass

    def _to_eur(raw_val, date, is_usd_row, fee_currency=None):
        """Convert a value to EUR. Handles BTC fees via price lookup."""
        if fee_currency == 'BTC':
            # BTC fee: can't convert here without price, skip (tiny amounts)
            return 0.0
        if is_usd_row and ecb and date:
            return ecb.usd_to_eur(raw_val, date)
        return raw_val

    try:
        with open(filepath, 'r', encoding='utf-8-sig') as f:
            # Skip metadata header line (e.g. Bybit "UID: ..." line)
            if rules.get('skip_first_line'):
                f.readline()
            sniffer = csv.Sniffer()
            sample = f.read(4096)
            f.seek(0)
            if rules.get('skip_first_line'):
                f.readline()
            try:
                dialect = sniffer.sniff(sample)
            except csv.Error:
                dialect = csv.excel

            reader = csv.DictReader(f, dialect=dialect)
            dates = []

            # Timezone conversion: if tz_source is set, parsed dates are in that
            # timezone and must be converted to UTC to match the DB (importers store UTC).
            tz_source = None
            if rules.get('tz_source'):
                tz_source = pytz.timezone(rules['tz_source'])

            pair_filter = set(rules.get('pair_filter', []))
            type_from_sign = rules['type_col'] == '_amount_sign'
            value_is_unit_price = rules.get('value_is_unit_price', False)
            fee_is_crypto = rules.get('fee_is_crypto', False)
            type_filter_col = rules.get('type_filter_col')
            type_filter_val = rules.get('type_filter_val')
            asset_filter = set(rules.get('asset_filter', []))
            dedup_col = rules.get('dedup_col')
            seen_ids = set()
            aggregate_by_time = rules.get('aggregate_by_time', False)
            agg_buy_times = set()   # unique timestamps for buy aggregation
            agg_sell_times = set()  # unique timestamps for sell aggregation

            for row in reader:
                row = {k.strip(): v for k, v in row.items() if k}
                result['total_rows'] += 1

                try:
                    # Dedup by column (e.g. Mt.Gox: skip duplicate IDs)
                    if dedup_col:
                        row_id = row.get(dedup_col, '').strip()
                        if row_id in seen_ids:
                            continue
                        seen_ids.add(row_id)

                    # Pre-filter rows (e.g. Bitstamp: only Type == 'Market')
                    if type_filter_col and row.get(type_filter_col, '').strip() != type_filter_val:
                        continue

                    # Filter by pair if configured
                    if pair_filter and pair_col:
                        pair_val = row.get(pair_col, '').strip()
                        if pair_val not in pair_filter:
                            continue

                    # Filter by asset if configured (e.g. Coinbase: only BTC)
                    asset_col = rules.get('asset_col')
                    if asset_col:
                        asset_val = row.get(asset_col, '').strip()
                        if asset_val not in asset_filter:
                            continue

                    # Date
                    d = _parse_date(row.get(rules['date_col'], ''))
                    if d and tz_source and d.tzinfo is None:
                        d = tz_source.localize(d).astimezone(pytz.UTC).replace(tzinfo=None)

                    # Check if this row is a USD-quoted pair
                    is_usd_row = all_usd
                    if pair_col:
                        pair_val = row.get(pair_col, '').strip()
                        is_usd_row = is_usd_row or pair_val in usd_pairs
                    row_currency = ''
                    is_crypto_currency = False
                    if currency_col:
                        row_currency = row.get(currency_col, '').strip().upper()
                        is_usd_row = is_usd_row or row_currency in usd_currencies
                        _FIAT = {'EUR', 'USD', 'GBP', 'CHF', 'JPY', 'CAD', 'AUD', ''}
                        is_crypto_currency = row_currency not in _FIAT

                    # Amount (needed early for sign-based type and unit-price calc)
                    amt_raw = row.get(rules['amount_col'], '')
                    amt = _strip_currency(amt_raw) if rules['strip_suffix'] else _safe_float(amt_raw)

                    # Type — derive from amount sign if no type column
                    if type_from_sign:
                        is_buy = amt > 0
                        is_sell = amt < 0
                    else:
                        type_val = row.get(rules['type_col'], '').strip()
                        is_buy = type_val in rules['buy_types']
                        is_sell = type_val in rules['sell_types']

                    # Value
                    val_raw = row.get(rules['value_col'], '') if rules['value_col'] else ''
                    val = _strip_currency(val_raw) if rules['strip_suffix'] else _safe_float(val_raw)

                    # Crypto-to-crypto: total_value is in crypto, not fiat
                    # Use CryptoPrices to get EUR value of the main asset
                    if is_crypto_currency and d:
                        cp = _get_crypto_prices()
                        crypto_name = row.get('cryptocurrency', '').strip().upper() if 'cryptocurrency' in row else ''
                        if cp and crypto_name and abs(amt) > 0:
                            cp_val = cp.crypto_to_eur(crypto_name, abs(amt), d)
                            if cp_val is not None:
                                val = cp_val
                            else:
                                val = 0.0
                        else:
                            val = 0.0
                    else:
                        val = _to_eur(abs(val), d, is_usd_row)

                    if value_is_unit_price:
                        val = abs(amt) * val  # price_per_unit × amount = total

                    # If no value from CSV, try crypto prices (Wirex, etc.)
                    if val == 0 and abs(amt) > 0 and d and not rules.get('value_col'):
                        cp = _get_crypto_prices()
                        asset = next(iter(asset_filter)) if asset_filter else 'BTC'
                        if cp:
                            cp_val = cp.crypto_to_eur(asset, abs(amt), d)
                            if cp_val is not None:
                                val = cp_val

                    if is_buy:
                        if aggregate_by_time and d:
                            agg_buy_times.add(d.strftime('%Y-%m-%d %H:%M:%S'))
                        else:
                            result['buy_count'] += 1
                        result['buy_value'] += val
                        if d:
                            dates.append(d)
                    elif is_sell:
                        if aggregate_by_time and d:
                            agg_sell_times.add(d.strftime('%Y-%m-%d %H:%M:%S'))
                        else:
                            result['sell_count'] += 1
                        result['sell_value'] += val
                        if d:
                            dates.append(d)

                    # Crypto-to-crypto: also count the counterpart side
                    if is_crypto_currency and (is_buy or is_sell) and d:
                        counter_val = 0.0
                        cp = _get_crypto_prices()
                        counter_amount = _safe_float(val_raw)
                        if cp and row_currency and counter_amount > 0:
                            cp_val = cp.crypto_to_eur(row_currency, counter_amount, d)
                            if cp_val is not None:
                                counter_val = cp_val
                        if is_buy:
                            result['sell_count'] += 1
                            result['sell_value'] += counter_val
                        else:
                            result['buy_count'] += 1
                            result['buy_value'] += counter_val

                    # Fee (only count for buy/sell rows)
                    if (is_buy or is_sell) and rules['fee_col'] and rules['fee_col'] in row:
                        fee_raw = row[rules['fee_col']]
                        fee_val = _strip_currency(fee_raw) if rules['strip_suffix'] else _safe_float(fee_raw)
                        if fee_is_crypto:
                            # Fee in crypto (e.g. BTC): convert via unit price in EUR
                            fee_eur = abs(fee_val) * val / abs(amt) if amt != 0 else 0.0
                        else:
                            # Detect fee currency from suffix
                            fee_cur = None
                            if rules['strip_suffix'] and fee_raw:
                                s = str(fee_raw).strip()
                                if s.endswith('BTC'):
                                    fee_cur = 'BTC'
                            fee_eur = _to_eur(abs(fee_val), d, is_usd_row, fee_currency=fee_cur)
                        result['total_fees'] += fee_eur

                except Exception:
                    result['parse_errors'] += 1

            # Aggregate counts by unique timestamp (Bybit: fills → trades)
            if aggregate_by_time:
                result['buy_count'] += len(agg_buy_times)
                result['sell_count'] += len(agg_sell_times)

            if dates:
                result['min_date'] = min(dates).strftime('%Y-%m-%d')
                result['max_date'] = max(dates).strftime('%Y-%m-%d')

    except Exception:
        pass

    return result


def parse_csv_rows(filepath, exchange):
    """Parse CSV into individual rows for row-level matching."""
    rules = CSV_PARSE_RULES.get(exchange)
    if not rules:
        return []

    # --- Paired ledger mode (Kraken) ---
    if rules.get('paired_ledger'):
        return _parse_paired_ledger_rows(filepath, rules)

    # --- Grouped trade mode (TRT) ---
    if rules.get('grouped_trade'):
        return _parse_trt_grouped_rows(filepath, rules)

    # Load ECB rates if exchange has USD pairs or all_usd
    ecb = None
    pair_col = rules.get('pair_col')
    usd_pairs = set(rules.get('usd_pairs', []))
    all_usd = rules.get('all_usd', False)
    currency_col = rules.get('currency_col')
    usd_currencies = set(rules.get('usd_currencies', []))
    if (pair_col and usd_pairs) or all_usd or (currency_col and usd_currencies):
        try:
            from importers.ecb_rates import ECBRates
            ecb = ECBRates(os.path.join(DATA_DIR, 'eurusd.csv'))
        except Exception:
            pass

    rows = []
    try:
        with open(filepath, 'r', encoding='utf-8-sig') as f:
            # Skip metadata header line (e.g. Bybit "UID: ..." line)
            if rules.get('skip_first_line'):
                f.readline()
            sniffer = csv.Sniffer()
            sample = f.read(4096)
            f.seek(0)
            if rules.get('skip_first_line'):
                f.readline()
            try:
                dialect = sniffer.sniff(sample)
            except csv.Error:
                dialect = csv.excel

            reader = csv.DictReader(f, dialect=dialect)

            # Timezone conversion (same as parse_csv_deep)
            tz_source = None
            if rules.get('tz_source'):
                tz_source = pytz.timezone(rules['tz_source'])

            pair_filter = set(rules.get('pair_filter', []))
            type_from_sign = rules['type_col'] == '_amount_sign'
            value_is_unit_price = rules.get('value_is_unit_price', False)
            fee_is_crypto = rules.get('fee_is_crypto', False)
            type_filter_col = rules.get('type_filter_col')
            type_filter_val = rules.get('type_filter_val')
            asset_filter = set(rules.get('asset_filter', []))
            dedup_col = rules.get('dedup_col')
            seen_ids = set()

            for line_num, row in enumerate(reader, 2):  # line 2 = first data row
                row = {k.strip(): v for k, v in row.items() if k}
                try:
                    # Dedup by column (e.g. Mt.Gox: skip duplicate IDs)
                    if dedup_col:
                        row_id = row.get(dedup_col, '').strip()
                        if row_id in seen_ids:
                            continue
                        seen_ids.add(row_id)

                    # Pre-filter rows (e.g. Bitstamp: only Type == 'Market')
                    if type_filter_col and row.get(type_filter_col, '').strip() != type_filter_val:
                        continue

                    # Filter by pair if configured
                    if pair_filter and pair_col:
                        pair_val = row.get(pair_col, '').strip()
                        if pair_val not in pair_filter:
                            continue

                    # Filter by asset if configured (e.g. Coinbase: only BTC)
                    asset_col = rules.get('asset_col')
                    if asset_col:
                        asset_val = row.get(asset_col, '').strip()
                        if asset_val not in asset_filter:
                            continue

                    date_str = row.get(rules['date_col'], '').strip()
                    d = _parse_date(date_str)
                    if d and tz_source and d.tzinfo is None:
                        d = tz_source.localize(d).astimezone(pytz.UTC).replace(tzinfo=None)

                    # Check if USD-quoted pair
                    is_usd_row = all_usd
                    if pair_col:
                        is_usd_row = is_usd_row or row.get(pair_col, '').strip() in usd_pairs
                    row_currency = ''
                    is_crypto_currency = False
                    if currency_col:
                        row_currency = row.get(currency_col, '').strip().upper()
                        is_usd_row = is_usd_row or row_currency in usd_currencies
                        _FIAT = {'EUR', 'USD', 'GBP', 'CHF', 'JPY', 'CAD', 'AUD', ''}
                        is_crypto_currency = row_currency not in _FIAT

                    # Amount
                    amt_raw = row.get(rules['amount_col'], '')
                    amt = _strip_currency(amt_raw) if rules['strip_suffix'] else _safe_float(amt_raw)

                    # Type — derive from amount sign if no type column
                    if type_from_sign:
                        is_buy = amt > 0
                        is_sell = amt < 0
                        type_val = 'BUY' if is_buy else ('SELL' if is_sell else '')
                    else:
                        type_val = row.get(rules['type_col'], '').strip()
                        is_buy = type_val in rules['buy_types']
                        is_sell = type_val in rules['sell_types']
                    norm_type = 'BUY' if is_buy else ('SELL' if is_sell else type_val)

                    # Value — convert USD to EUR if needed
                    val_raw = row.get(rules['value_col'], '') if rules['value_col'] else ''
                    val = _strip_currency(val_raw) if rules['strip_suffix'] else _safe_float(val_raw)

                    # Crypto-to-crypto: total_value is in crypto, not fiat
                    if is_crypto_currency and d:
                        cp = _get_crypto_prices()
                        crypto_name = row.get('cryptocurrency', '').strip().upper() if 'cryptocurrency' in row else ''
                        if cp and crypto_name and abs(amt) > 0:
                            cp_val = cp.crypto_to_eur(crypto_name, abs(amt), d)
                            if cp_val is not None:
                                val = cp_val
                            else:
                                val = 0.0
                        else:
                            val = 0.0
                    else:
                        if is_usd_row and ecb and d:
                            val = ecb.usd_to_eur(abs(val), d)
                    if value_is_unit_price:
                        val = abs(amt) * abs(val)  # price_per_unit × amount = total

                    # If no value from CSV, try crypto prices (Wirex, etc.)
                    if val == 0 and abs(amt) > 0 and d and not rules.get('value_col'):
                        cp = _get_crypto_prices()
                        asset = next(iter(asset_filter)) if asset_filter else 'BTC'
                        if cp:
                            cp_val = cp.crypto_to_eur(asset, abs(amt), d)
                            if cp_val is not None:
                                val = cp_val

                    # Fee
                    fee = 0.0
                    if rules['fee_col'] and rules['fee_col'] in row:
                        fee_raw = row[rules['fee_col']]
                        fee_val = _strip_currency(fee_raw) if rules['strip_suffix'] else _safe_float(fee_raw)
                        if fee_is_crypto:
                            # Fee in crypto: convert via unit price in EUR
                            fee = abs(fee_val) * abs(val) / abs(amt) if amt != 0 else 0.0
                        else:
                            fee_is_btc = rules['strip_suffix'] and str(fee_raw).strip().endswith('BTC')
                            if fee_is_btc:
                                fee = 0.0
                            elif is_usd_row and ecb and d:
                                fee = ecb.usd_to_eur(abs(fee_val), d)
                            else:
                                fee = abs(fee_val)

                    # Pair / crypto
                    pair = ''
                    for col in ('PAIR', 'Pair', 'Asset', 'asset', 'pair'):
                        if col in row and row[col].strip():
                            pair = row[col].strip()
                            break

                    rows.append({
                        'line': line_num,
                        'date_str': date_str,
                        'date': d.strftime('%Y-%m-%d %H:%M') if d else None,
                        'date_day': d.strftime('%Y-%m-%d') if d else None,
                        'type_raw': type_val,
                        'type': norm_type,
                        'is_trade': is_buy or is_sell,
                        'pair': pair,
                        'amount': abs(amt),
                        'value': abs(val),
                        'fee': fee,
                    })

                    # Crypto-to-crypto: emit counterpart row
                    if is_crypto_currency and (is_buy or is_sell) and d:
                        counter_val = 0.0
                        counter_amount = _safe_float(val_raw)
                        cp = _get_crypto_prices()
                        if cp and row_currency and counter_amount > 0:
                            cp_val = cp.crypto_to_eur(row_currency, counter_amount, d)
                            if cp_val is not None:
                                counter_val = cp_val
                        counter_type = 'SELL' if is_buy else 'BUY'
                        rows.append({
                            'line': line_num,
                            'date_str': date_str,
                            'date': d.strftime('%Y-%m-%d %H:%M') if d else None,
                            'date_day': d.strftime('%Y-%m-%d') if d else None,
                            'type_raw': f'{counter_type} (counterpart)',
                            'type': counter_type,
                            'is_trade': True,
                            'pair': row_currency,
                            'amount': counter_amount,
                            'value': counter_val,
                            'fee': 0,
                        })
                except Exception:
                    rows.append({
                        'line': line_num,
                        'date_str': '', 'date': None, 'date_day': None,
                        'type_raw': '?', 'type': 'PARSE_ERROR',
                        'is_trade': False, 'pair': '',
                        'amount': 0, 'value': 0, 'fee': 0,
                    })
    except Exception:
        pass

    # Aggregate fills by timestamp (Bybit: multiple fills → one trade per timestamp)
    if rules.get('aggregate_by_time') and rows:
        from collections import defaultdict
        groups = defaultdict(list)
        non_trade = []
        for r in rows:
            if r['is_trade'] and r['date_str']:
                # Use date_str (second-level precision) for grouping to match importer
                key = (r['date_str'], r['type'])
                groups[key].append(r)
            else:
                non_trade.append(r)
        if groups:
            agg_rows = []
            for (ds, typ), fills in sorted(groups.items()):
                agg_rows.append({
                    'line': fills[0]['line'],
                    'date_str': ds,
                    'date': fills[0]['date'],
                    'date_day': fills[0]['date_day'],
                    'type_raw': fills[0]['type_raw'],
                    'type': typ,
                    'is_trade': True,
                    'pair': fills[0]['pair'],
                    'amount': sum(f['amount'] for f in fills),
                    'value': sum(f['value'] for f in fills),
                    'fee': sum(f['fee'] for f in fills),
                })
            agg_rows.extend(non_trade)
            rows = agg_rows

    return rows


def get_db_rows(exchange):
    """Get individual transactions from DB for matching."""
    if not db_exists():
        return []
    conn = get_db()
    try:
        rows = conn.execute("""
            SELECT
                transaction_date, transaction_type, cryptocurrency,
                amount, total_value, fee_amount
            FROM transactions
            WHERE exchange_name = ?
            ORDER BY transaction_date
        """, (exchange,)).fetchall()
        result = []
        for r in rows:
            d = _parse_date(r['transaction_date'])
            result.append({
                'date': d.strftime('%Y-%m-%d %H:%M') if d else None,
                'date_day': d.strftime('%Y-%m-%d') if d else None,
                'type': r['transaction_type'],
                'crypto': r['cryptocurrency'],
                'amount': r['amount'] or 0,
                'value': r['total_value'] or 0,
                'fee': r['fee_amount'] or 0,
            })
        return result
    except sqlite3.OperationalError:
        return []
    finally:
        conn.close()


def find_unmatched(csv_rows, db_rows, exchange):
    """
    Cross-reference CSV rows vs DB rows. Return unmatched CSV rows with diagnosis.
    Matching key: date (to the minute) + type + amount (within 0.1% tolerance).
    """
    # Build a set of DB signatures for fast lookup
    db_sigs = defaultdict(list)
    for r in db_rows:
        if r['date']:
            key = (r['date'], r['type'])
            db_sigs[key].append(r['amount'])

    # Also build by day for looser matching
    db_day_sigs = defaultdict(list)
    for r in db_rows:
        if r['date_day']:
            key = (r['date_day'], r['type'])
            db_day_sigs[key].append(r['amount'])

    unmatched = []
    matched_count = 0

    for csv_r in csv_rows:
        # --- Diagnose non-trade rows (these are expected to be skipped) ---
        if not csv_r['is_trade']:
            if csv_r['type'] == 'PARSE_ERROR':
                unmatched.append({**csv_r, 'reason': 'Parse error — could not read this row',
                                  'severity': 'error'})
            else:
                # Deposits, withdrawals, rewards, etc. — expected skip
                unmatched.append({**csv_r, 'reason': f'Filtered: type "{csv_r["type_raw"]}" is not a trade (expected)',
                                  'severity': 'info'})
            continue

        # --- Diagnose filtered pairs ---
        pair = csv_r['pair']
        if pair and not pair.endswith('EUR') and pair not in ('BTC', 'ETH'):
            # Non-EUR pair — probably filtered by importer
            unmatched.append({**csv_r, 'reason': f'Filtered: pair "{pair}" is not EUR-denominated',
                              'severity': 'info'})
            continue

        # --- Try exact match (date to minute + type + amount) ---
        if csv_r['date']:
            exact_key = (csv_r['date'], csv_r['type'])
            if exact_key in db_sigs:
                # Check amount match
                amounts = db_sigs[exact_key]
                if any(abs(a - csv_r['amount']) / max(csv_r['amount'], 0.0001) < 0.01 for a in amounts):
                    matched_count += 1
                    continue

        # --- Try day-level match ---
        if csv_r['date_day']:
            day_key = (csv_r['date_day'], csv_r['type'])
            if day_key in db_day_sigs:
                amounts = db_day_sigs[day_key]
                if any(abs(a - csv_r['amount']) / max(csv_r['amount'], 0.0001) < 0.01 for a in amounts):
                    matched_count += 1
                    continue
                # Date matches but amount doesn't
                unmatched.append({**csv_r, 'reason': f'Amount mismatch — date found in DB but amount differs (CSV: {csv_r["amount"]:.8f})',
                                  'severity': 'warning'})
                continue

        # --- No match at all ---
        if csv_r['date_day'] and db_rows:
            db_min = min(r['date_day'] for r in db_rows if r['date_day'])
            db_max = max(r['date_day'] for r in db_rows if r['date_day'])
            if csv_r['date_day'] > db_max:
                unmatched.append({**csv_r, 'reason': f'New record — date {csv_r["date_day"]} is after DB last date ({db_max})',
                                  'severity': 'warning'})
            elif csv_r['date_day'] < db_min:
                unmatched.append({**csv_r, 'reason': f'Old record — date {csv_r["date_day"]} is before DB first date ({db_min})',
                                  'severity': 'warning'})
            else:
                unmatched.append({**csv_r, 'reason': 'Not found in DB — may need re-import',
                                  'severity': 'error'})
        else:
            unmatched.append({**csv_r, 'reason': 'Not found in DB',
                              'severity': 'error'})

    return unmatched, matched_count


def detect_exchange(filename):
    """Detect exchange from filename."""
    basename = filename.lower()
    for pattern, name, importer in EXCHANGE_PATTERNS:
        if re.search(pattern, basename, re.IGNORECASE):
            return name, importer
    return 'Unknown', None


def scan_csv_files():
    """Scan data/ for CSV files and classify them."""
    files = []
    for filepath in sorted(glob.glob(os.path.join(DATA_DIR, '*.csv'))):
        basename = os.path.basename(filepath)
        if basename in ('eurusd.csv',) or basename.startswith('sample_'):
            continue

        exchange, importer = detect_exchange(basename)
        stat = os.stat(filepath)

        # Count rows
        try:
            with open(filepath, 'r', encoding='utf-8-sig') as f:
                row_count = sum(1 for _ in f) - 1  # minus header
        except Exception:
            row_count = -1

        files.append({
            'filename': basename,
            'filepath': filepath,
            'exchange': exchange,
            'importer': importer,
            'size': stat.st_size,
            'modified': datetime.fromtimestamp(stat.st_mtime),
            'rows': row_count,
        })

    return files


def compute_record_hash(source, date, tx_type, exchange, crypto, amount, value, fee):
    """Deterministic SHA256 hash from core record fields."""
    try:
        amount_n = f"{float(amount):.8f}"
    except (ValueError, TypeError):
        amount_n = str(amount)
    try:
        value_n = f"{float(value):.2f}"
    except (ValueError, TypeError):
        value_n = str(value)
    try:
        fee_n = f"{float(fee):.2f}"
    except (ValueError, TypeError):
        fee_n = str(fee or 0)

    raw = f"{source or ''}|{date}|{tx_type}|{exchange}|{crypto}|{amount_n}|{value_n}|{fee_n}"
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()



def get_db_exchange_stats():
    """Get transaction stats per exchange from the database."""
    if not db_exists():
        return {}
    conn = get_db()
    try:
        rows = conn.execute("""
            SELECT
                exchange_name,
                COUNT(*) as total,
                COUNT(CASE WHEN transaction_type = 'BUY' THEN 1 END) as buys,
                COUNT(CASE WHEN transaction_type = 'SELL' THEN 1 END) as sells,
                MIN(transaction_date) as min_date,
                MAX(transaction_date) as max_date,
                SUM(CASE WHEN transaction_type='BUY' THEN total_value ELSE 0 END) as buy_value,
                SUM(CASE WHEN transaction_type='SELL' THEN total_value ELSE 0 END) as sell_value,
                SUM(fee_amount) as total_fees,
                GROUP_CONCAT(DISTINCT cryptocurrency) as cryptos
            FROM transactions
            GROUP BY exchange_name
            ORDER BY exchange_name
        """).fetchall()
        return {r['exchange_name']: dict(r) for r in rows}
    except sqlite3.OperationalError:
        return {}
    finally:
        conn.close()


def get_db_source_stats():
    """Get transaction stats per source file from the database."""
    if not db_exists():
        return {}
    conn = get_db()
    try:
        rows = conn.execute("""
            SELECT source,
                   COUNT(*) as total,
                   COUNT(CASE WHEN transaction_type='BUY' THEN 1 END) as buys,
                   COUNT(CASE WHEN transaction_type='SELL' THEN 1 END) as sells,
                   MAX(imported_at) as last_imported
            FROM transactions
            WHERE source IS NOT NULL
            GROUP BY source
        """).fetchall()
        return {r['source']: dict(r) for r in rows}
    except sqlite3.OperationalError:
        return {}
    finally:
        conn.close()


# ════════════════════════════════════════════════════════════
# ROUTES
# ════════════════════════════════════════════════════════════

@app.context_processor
def inject_globals():
    """Make wizard status and eurusd info available to all templates."""
    return {
        'wizard': get_wizard_status(),
        'db_exists': db_exists(),
        'eurusd': check_eurusd(),
        'data_dir_name': os.path.basename(DATA_DIR),
    }


# ── Home / Dashboard ───────────────────────────────────────

@app.route('/')
def index():
    return redirect(url_for('collect'))


# ── Step 1: Collect ────────────────────────────────────────

@app.route('/collect')
def collect():
    csv_files = scan_csv_files()
    instructions = EXCHANGE_INSTRUCTIONS
    return render_template('collect.html',
                           csv_files=csv_files,
                           instructions=instructions,
                           page='collect')


@app.route('/collect/upload', methods=['POST'])
def upload_csv():
    if 'file' not in request.files:
        flash('No file selected', 'error')
        return redirect(url_for('collect'))

    file = request.files['file']
    if file.filename == '':
        flash('No file selected', 'error')
        return redirect(url_for('collect'))

    if not file.filename.endswith('.csv'):
        flash('Only CSV files are accepted', 'error')
        return redirect(url_for('collect'))

    os.makedirs(DATA_DIR, exist_ok=True)
    try:
        dest = safe_path(DATA_DIR, file.filename)
    except ValueError:
        flash(f'Invalid filename: {file.filename}', 'error')
        return redirect(url_for('collect'))
    file.save(dest)
    saved_name = os.path.basename(dest)
    exchange, _ = detect_exchange(saved_name)
    flash(f'Uploaded {saved_name} → detected as {exchange}', 'success')
    return redirect(url_for('collect'))


@app.route('/collect/delete/<filename>', methods=['POST'])
def delete_csv(filename):
    try:
        filepath = safe_path(DATA_DIR, filename)
    except ValueError:
        flash(f'Invalid filename: {filename}', 'error')
        return redirect(url_for('collect'))
    if os.path.exists(filepath) and filepath.endswith('.csv'):
        os.remove(filepath)
        flash(f'Deleted {os.path.basename(filepath)}', 'success')
    return redirect(url_for('collect'))


@app.route('/collect/template')
def download_template():
    """Download the standard CSV template for manual transactions."""
    template_path = os.path.join(DATA_DIR, 'template_manual_transactions.csv')
    if os.path.exists(template_path):
        return send_file(template_path, as_attachment=True)
    # Generate it on the fly if missing
    content = (
        'transaction_date,transaction_type,exchange_name,cryptocurrency,'
        'amount,price_per_unit,total_value,fee_amount,fee_currency,currency,'
        'transaction_id,notes\n'
        '2024-01-15T10:00:00+00:00,BUY,Exchange Name,BTC,'
        '0.50000000,40000.00,20000.00,50.00,EUR,EUR,'
        'EXAMPLE_001,Example purchase - delete this row\n'
    )
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(template_path, 'w') as f:
        f.write(content)
    return send_file(template_path, as_attachment=True)


# ── Step 2: Import ─────────────────────────────────────────

@app.route('/import')
def import_page():
    csv_files = scan_csv_files()
    db_stats = get_db_exchange_stats()
    source_stats = get_db_source_stats()
    field_map = EXCHANGE_FIELD_MAP

    # Attach per-file DB stats to each CSV file
    for f in csv_files:
        f['db_source'] = source_stats.get(f['filename'])

    # Group CSV files by exchange
    exchange_groups = defaultdict(list)
    for f in csv_files:
        exchange_groups[f['exchange']].append(f)

    # Build grouped data for template
    groups = []
    for exchange in sorted(exchange_groups.keys()):
        files = exchange_groups[exchange]
        importer = files[0]['importer'] if files else None
        total_rows = sum(f['rows'] for f in files)
        db = db_stats.get(exchange, {})

        groups.append({
            'exchange': exchange,
            'files': files,
            'file_count': len(files),
            'total_rows': total_rows,
            'importer': importer,
            'db': db,
            'multi': len(files) > 1,
        })

    return render_template('import_data.html',
                           groups=groups,
                           db_stats=db_stats,
                           field_map=field_map,
                           page='import')


@app.route('/import/run-file/<filename>', methods=['POST'])
def run_import_file(filename):
    """Import a single CSV file into the database.

    All importers now accept: python3 importer.py <filepath> [exchange_name]
    Each importer handles DELETE by source and sets source/imported_at/record_hash.
    """
    try:
        filepath = safe_path(DATA_DIR, filename)
    except ValueError:
        flash(f'Invalid filename: {filename}', 'error')
        return redirect(url_for('import_page'))
    if not os.path.isfile(filepath):
        flash(f'File not found: {filename}', 'error')
        return redirect(url_for('import_page'))

    exchange_name, importer_script = detect_exchange(os.path.basename(filepath))
    if not importer_script:
        flash(f'No importer available for {filename}', 'error')
        return redirect(url_for('import_page'))

    importer_path = os.path.join(PROJECT_ROOT, importer_script)
    if not os.path.exists(importer_path):
        flash(f'Importer script not found: {importer_script}', 'error')
        return redirect(url_for('import_page'))

    try:
        cmd = [sys.executable, importer_path, filepath, exchange_name]
        result = subprocess.run(
            cmd, cwd=PROJECT_ROOT,
            capture_output=True, text=True,
            timeout=120,
        )
        output = result.stdout + result.stderr
        if result.returncode == 0:
            flash(f'Imported {filename} ({exchange_name})', 'success')
        else:
            flash(f'Import error for {filename}: {output[-500:]}', 'error')

    except subprocess.TimeoutExpired:
        flash(f'Import timed out for {filename}', 'error')
    except Exception as e:
        flash(f'Import failed for {filename}: {str(e)}', 'error')

    return redirect(url_for('import_page'))


@app.route('/import/run-exchange/<exchange_name>', methods=['POST'])
def run_import_exchange(exchange_name):
    """Re-import all CSV files for a given exchange. Used by the Status page."""
    redirect_to = request.form.get('redirect', 'status')
    csv_files = scan_csv_files()
    exchange_files = [f for f in csv_files if f['exchange'] == exchange_name and f['importer']]

    if not exchange_files:
        flash(f'No importable files found for {exchange_name}', 'error')
        return redirect(url_for(redirect_to))

    imported = []
    errors = []
    for f in exchange_files:
        importer_path = os.path.join(PROJECT_ROOT, f['importer'])
        filepath = f['filepath']
        try:
            cmd = [sys.executable, importer_path, filepath, exchange_name]
            result = subprocess.run(
                cmd, cwd=PROJECT_ROOT,
                capture_output=True, text=True,
                timeout=120,
            )
            if result.returncode == 0:
                imported.append(f['filename'])
            else:
                output = result.stdout + result.stderr
                errors.append(f"{f['filename']}: {output[-300:]}")
        except subprocess.TimeoutExpired:
            errors.append(f"{f['filename']}: timeout")
        except Exception as e:
            errors.append(f"{f['filename']}: {str(e)}")

    if imported:
        flash(f"Imported {exchange_name}: {', '.join(imported)}", 'success')
    for err in errors:
        flash(f'Import error — {err}', 'error')

    return redirect(url_for(redirect_to))


# ── Step 3: Status ─────────────────────────────────────────

@app.route('/status')
def status():
    csv_files = scan_csv_files()
    db_stats = get_db_exchange_stats()

    # Group CSVs by exchange and deep-parse each
    by_exchange = defaultdict(list)
    for f in csv_files:
        by_exchange[f['exchange']].append(f)

    # Build symmetric comparison
    all_exchanges = sorted(set(
        list(by_exchange.keys()) + list(db_stats.keys())
    ))

    comparisons = []
    for exchange in all_exchanges:
        csvs = by_exchange.get(exchange, [])
        db = db_stats.get(exchange)

        # Parse CSV files deeply
        csv_merged = {
            'files': [f['filename'] for f in csvs],
            'buy_count': 0, 'sell_count': 0,
            'buy_value': 0.0, 'sell_value': 0.0,
            'total_fees': 0.0,
            'min_date': None, 'max_date': None,
            'total_rows': 0, 'parse_errors': 0,
        }
        all_dates = []
        for f in csvs:
            parsed = parse_csv_deep(f['filepath'], exchange)
            csv_merged['buy_count'] += parsed['buy_count']
            csv_merged['sell_count'] += parsed['sell_count']
            csv_merged['buy_value'] += parsed['buy_value']
            csv_merged['sell_value'] += parsed['sell_value']
            csv_merged['total_fees'] += parsed['total_fees']
            csv_merged['total_rows'] += parsed['total_rows']
            csv_merged['parse_errors'] += parsed['parse_errors']
            if parsed['min_date']:
                all_dates.append(parsed['min_date'])
            if parsed['max_date']:
                all_dates.append(parsed['max_date'])

        if all_dates:
            csv_merged['min_date'] = min(all_dates)
            csv_merged['max_date'] = max(all_dates)

        # Build comparison metrics — same metrics from both sources
        metrics = []
        csv_buys = csv_merged['buy_count'] if csvs else None
        csv_sells = csv_merged['sell_count'] if csvs else None
        db_buys = db['buys'] if db else None
        db_sells = db['sells'] if db else None

        def _delta_class(a, b):
            if a is None or b is None:
                return 'dim'
            if a == b:
                return 'match'
            if isinstance(a, str) or isinstance(b, str):
                return 'mismatch' if str(a) != str(b) else 'match'
            if b == 0:
                return 'mismatch' if a != 0 else 'match'
            diff_pct = abs(a - b) / max(abs(b), 1) * 100
            if diff_pct < 2:
                return 'match'
            elif diff_pct < 10:
                return 'close'
            return 'mismatch'

        # Overall status
        if not csvs:
            row_status = 'db-only'
        elif not db:
            row_status = 'not-imported'
        elif csv_buys == db_buys and csv_sells == db_sells:
            row_status = 'ok'
        elif _delta_class(csv_buys, db_buys) != 'mismatch' and _delta_class(csv_sells, db_sells) != 'mismatch':
            row_status = 'close'
        else:
            row_status = 'mismatch'

        # Row-level matching for anomaly detection
        unmatched_rows = []
        matched_row_count = 0
        if csvs and db:
            all_csv_rows = []
            for f in csvs:
                all_csv_rows.extend(parse_csv_rows(f['filepath'], exchange))
            db_rows_list = get_db_rows(exchange)
            unmatched_rows, matched_row_count = find_unmatched(all_csv_rows, db_rows_list, exchange)

        # Separate by severity for display
        errors = [r for r in unmatched_rows if r['severity'] == 'error']
        warnings = [r for r in unmatched_rows if r['severity'] == 'warning']
        infos = [r for r in unmatched_rows if r['severity'] == 'info']

        comparisons.append({
            'exchange': exchange,
            'status': row_status,
            'csv_files': ', '.join(csv_merged['files']) if csvs else None,
            'csv_rows': csv_merged['total_rows'] if csvs else None,
            'parse_errors': csv_merged['parse_errors'],
            'has_importer': any(f['importer'] for f in csvs) if csvs else False,
            'first_csv_file': csvs[0]['filename'] if csvs else None,
            'matched_count': matched_row_count,
            'unmatched_errors': errors,
            'unmatched_warnings': warnings,
            'unmatched_infos': infos,
            'metrics': [
                {
                    'label': 'BUY count',
                    'csv': csv_buys,
                    'db': db_buys,
                    'cls': _delta_class(csv_buys, db_buys),
                    'fmt': 'int',
                },
                {
                    'label': 'SELL count',
                    'csv': csv_sells,
                    'db': db_sells,
                    'cls': _delta_class(csv_sells, db_sells),
                    'fmt': 'int',
                },
                {
                    'label': 'First date',
                    'csv': csv_merged['min_date'] if csvs else None,
                    'db': db['min_date'][:10] if db and db['min_date'] else None,
                    'cls': _delta_class(csv_merged['min_date'], db['min_date'][:10] if db and db['min_date'] else None) if csvs and db else 'dim',
                    'fmt': 'str',
                },
                {
                    'label': 'Last date',
                    'csv': csv_merged['max_date'] if csvs else None,
                    'db': db['max_date'][:10] if db and db['max_date'] else None,
                    'cls': _delta_class(csv_merged['max_date'], db['max_date'][:10] if db and db['max_date'] else None) if csvs and db else 'dim',
                    'fmt': 'str',
                },
                {
                    'label': 'BUY value',
                    'csv': csv_merged['buy_value'] if csvs and csv_merged['buy_value'] > 0 else None,
                    'db': db['buy_value'] if db else None,
                    'cls': _delta_class(csv_merged['buy_value'], db['buy_value']) if csvs and db and csv_merged['buy_value'] > 0 else 'dim',
                    'fmt': 'eur',
                },
                {
                    'label': 'SELL value',
                    'csv': csv_merged['sell_value'] if csvs and csv_merged['sell_value'] > 0 else None,
                    'db': db['sell_value'] if db else None,
                    'cls': _delta_class(csv_merged['sell_value'], db['sell_value']) if csvs and db and csv_merged['sell_value'] > 0 else 'dim',
                    'fmt': 'eur',
                },
                {
                    'label': 'Total fees',
                    'csv': csv_merged['total_fees'] if csvs and csv_merged['total_fees'] > 0 else None,
                    'db': db['total_fees'] if db else None,
                    'cls': _delta_class(csv_merged['total_fees'], db['total_fees']) if csvs and db and csv_merged['total_fees'] > 0 else 'dim',
                    'fmt': 'eur',
                },
            ],
        })

    return render_template('status.html',
                           comparisons=comparisons,
                           page='status')


# ── Step 4: FIFO ──────────────────────────────────────────

@app.route('/fifo')
def fifo():
    fifo_stats = {}
    if db_exists():
        conn = get_db()
        try:
            fifo_stats['lot_count'] = conn.execute(
                "SELECT COUNT(*) FROM fifo_lots").fetchone()[0]
            fifo_stats['lots_with_remaining'] = conn.execute(
                "SELECT COUNT(*) FROM fifo_lots WHERE remaining_amount > 0.00000001"
            ).fetchone()[0]
            fifo_stats['match_count'] = conn.execute(
                "SELECT COUNT(*) FROM sale_lot_matches").fetchone()[0]
            fifo_stats['total_gain'] = conn.execute(
                "SELECT COALESCE(SUM(gain_loss), 0) FROM sale_lot_matches"
            ).fetchone()[0]

            # Per-year summary
            years = conn.execute("""
                SELECT
                    strftime('%Y', sale_date) as year,
                    COUNT(*) as matches,
                    SUM(amount_sold) as sold,
                    SUM(gain_loss) as gain_loss,
                    SUM(CASE WHEN holding_period_days >= 365 THEN 1 ELSE 0 END) as exempt,
                    SUM(CASE WHEN holding_period_days < 365 THEN 1 ELSE 0 END) as taxable,
                    SUM(CASE WHEN holding_period_days >= 365 THEN gain_loss ELSE 0 END) as exempt_gain,
                    SUM(CASE WHEN holding_period_days < 365 THEN gain_loss ELSE 0 END) as taxable_gain
                FROM sale_lot_matches
                GROUP BY year
                ORDER BY year
            """).fetchall()
            fifo_stats['years'] = [dict(r) for r in years]

            # Holdings
            holdings = conn.execute("""
                SELECT cryptocurrency,
                       COUNT(*) as lots,
                       SUM(remaining_amount) as remaining,
                       SUM(cost_basis * remaining_amount / original_amount) as cost
                FROM fifo_lots
                WHERE remaining_amount > 0.00000001
                GROUP BY cryptocurrency
            """).fetchall()
            fifo_stats['holdings'] = [dict(r) for r in holdings]

        except sqlite3.OperationalError:
            pass
        finally:
            conn.close()

    return render_template('fifo.html', stats=fifo_stats, page='fifo')


@app.route('/fifo/calculate', methods=['POST'])
def run_fifo():
    calc_script = os.path.join(PROJECT_ROOT, 'calculators', 'calculate_fifo.py')
    if not os.path.exists(calc_script):
        flash('FIFO calculator not found: calculators/calculate_fifo.py', 'error')
        return redirect(url_for('fifo'))

    # Backup before FIFO
    if db_exists():
        os.makedirs(BACKUPS_DIR, exist_ok=True)
        backup_name = f"crypto_fifo.db.backup_pre_fifo_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        shutil.copy2(DATABASE_PATH, os.path.join(BACKUPS_DIR, backup_name))

    try:
        result = subprocess.run(
            [sys.executable, calc_script],
            cwd=PROJECT_ROOT,
            capture_output=True, text=True, timeout=300,
        )
        if result.returncode == 0:
            flash('FIFO calculation completed successfully', 'success')
        else:
            flash(f'FIFO error: {(result.stdout + result.stderr)[-500:]}', 'error')
    except Exception as e:
        flash(f'FIFO failed: {str(e)}', 'error')

    return redirect(url_for('fifo'))


# ── Step 5: Reports ───────────────────────────────────────

@app.route('/reports')
def reports():
    stats = {}
    available_years = []

    if db_exists():
        conn = get_db()
        try:
            # Available years
            years_rows = conn.execute("""
                SELECT DISTINCT strftime('%Y', sale_date) as year
                FROM sale_lot_matches
                ORDER BY year
            """).fetchall()
            available_years = [r['year'] for r in years_rows]

            # Overall stats
            stats['tx_count'] = conn.execute(
                "SELECT COUNT(*) FROM transactions").fetchone()[0]
            stats['exchange_count'] = conn.execute(
                "SELECT COUNT(DISTINCT exchange_name) FROM transactions").fetchone()[0]
            stats['crypto_count'] = conn.execute(
                "SELECT COUNT(DISTINCT cryptocurrency) FROM transactions").fetchone()[0]
            stats['date_range'] = conn.execute(
                "SELECT MIN(transaction_date), MAX(transaction_date) FROM transactions"
            ).fetchone()

            # Per-exchange summary
            stats['exchanges'] = conn.execute("""
                SELECT exchange_name,
                       COUNT(*) as total,
                       COUNT(CASE WHEN transaction_type='BUY' THEN 1 END) as buys,
                       COUNT(CASE WHEN transaction_type='SELL' THEN 1 END) as sells,
                       SUM(CASE WHEN transaction_type='BUY' THEN total_value ELSE 0 END) as buy_val,
                       SUM(CASE WHEN transaction_type='SELL' THEN total_value ELSE 0 END) as sell_val,
                       SUM(fee_amount) as fees
                FROM transactions
                GROUP BY exchange_name
                ORDER BY total DESC
            """).fetchall()

        except sqlite3.OperationalError:
            pass
        finally:
            conn.close()

    # Existing report files
    existing_reports = []
    if os.path.exists(REPORTS_DIR):
        for f in sorted(glob.glob(os.path.join(REPORTS_DIR, '*.xlsx'))):
            stat = os.stat(f)
            existing_reports.append({
                'filename': os.path.basename(f),
                'filepath': f,
                'size': stat.st_size,
                'modified': datetime.fromtimestamp(stat.st_mtime),
            })

    # SQL query files
    sql_queries = []
    for f in sorted(glob.glob(os.path.join(SQL_DIR, '*.sql'))):
        basename = os.path.basename(f)
        label = os.path.splitext(basename)[0].replace('_', ' ')
        sql_queries.append({'filename': basename, 'label': label})

    return render_template('reports.html',
                           stats=stats,
                           available_years=available_years,
                           existing_reports=existing_reports,
                           sql_queries=sql_queries,
                           page='reports')


@app.route('/reports/query/<filename>')
def run_sql_query(filename):
    """Execute a .sql file and return JSON results."""
    from flask import jsonify

    # Validate filename: no path traversal, must be .sql
    if '/' in filename or '..' in filename or not filename.endswith('.sql'):
        return jsonify(error='Invalid filename'), 400

    sql_path = os.path.join(SQL_DIR, filename)
    if not os.path.exists(sql_path):
        return jsonify(error='Query file not found'), 404

    # Read SQL
    with open(sql_path, 'r') as f:
        sql = f.read().strip()

    # Security: only allow SELECT statements
    sql_upper = sql.upper().lstrip()
    forbidden = ('INSERT', 'UPDATE', 'DELETE', 'DROP', 'ALTER', 'CREATE',
                 'ATTACH', 'DETACH', 'PRAGMA', 'REPLACE', 'VACUUM', 'REINDEX')
    for kw in forbidden:
        if kw in sql_upper.split():
            return jsonify(error=f'Forbidden SQL keyword: {kw}'), 403

    if not sql_upper.startswith('SELECT') and not sql_upper.startswith('WITH'):
        return jsonify(error='Only SELECT queries are allowed'), 403

    if not db_exists():
        return jsonify(error='Database not found'), 500

    try:
        # Open DB in read-only mode
        db_uri = f'file:{DATABASE_PATH}?mode=ro'
        conn = sqlite3.connect(db_uri, uri=True)
        conn.row_factory = sqlite3.Row
        cursor = conn.execute(sql)
        columns = [desc[0] for desc in cursor.description] if cursor.description else []
        rows = [list(r) for r in cursor.fetchmany(500)]
        total = len(rows)
        if cursor.fetchone() is not None:
            total = f'{total}+'  # indicate truncation
        conn.close()
        return jsonify(columns=columns, rows=rows, total=total, query=sql)
    except Exception as e:
        return jsonify(error=str(e)), 500


@app.route('/reports/generate/<int:year>', methods=['POST'])
def generate_report(year):
    report_script = os.path.join(PROJECT_ROOT, 'calculators', 'generate_irs_report.py')
    if not os.path.exists(report_script):
        flash('Report generator not found', 'error')
        return redirect(url_for('reports'))

    try:
        result = subprocess.run(
            [sys.executable, report_script, str(year)],
            cwd=PROJECT_ROOT,
            capture_output=True, text=True, timeout=120,
        )
        if result.returncode == 0:
            flash(f'Report generated for {year}', 'success')
        else:
            flash(f'Report error: {(result.stdout + result.stderr)[-500:]}', 'error')
    except Exception as e:
        flash(f'Report failed: {str(e)}', 'error')

    return redirect(url_for('reports'))


@app.route('/reports/download/<filename>')
def download_report(filename):
    try:
        filepath = safe_path(REPORTS_DIR, filename)
    except ValueError:
        flash('Invalid filename', 'error')
        return redirect(url_for('reports'))
    if os.path.exists(filepath):
        return send_file(filepath, as_attachment=True)
    flash('Report not found', 'error')
    return redirect(url_for('reports'))


# ── Step 6: Manual Entry ──────────────────────────────────

@app.route('/manual')
def manual():
    recent = []
    if db_exists():
        conn = get_db()
        try:
            recent = conn.execute("""
                SELECT * FROM transactions
                ORDER BY created_at DESC
                LIMIT 20
            """).fetchall()
        except sqlite3.OperationalError:
            pass
        finally:
            conn.close()

    return render_template('manual.html', recent=recent, page='manual')


@app.route('/manual/add', methods=['POST'])
def manual_add():
    if not db_exists():
        flash('Database not found. Run setup.sh first.', 'error')
        return redirect(url_for('manual'))

    try:
        conn = get_db()
        tx_date = request.form['date']
        tx_type = request.form['type']
        exchange = request.form['exchange']
        crypto = request.form['crypto'].upper()
        amount = float(request.form['amount'])
        price = float(request.form['price'])
        total_value = amount * price
        fee = float(request.form.get('fee', 0) or 0)
        notes = request.form.get('notes', '')
        now = datetime.now().isoformat()

        record_hash = compute_record_hash(
            'web_manual_entry', tx_date, tx_type,
            exchange, crypto, amount, total_value, fee
        )

        conn.execute("""
            INSERT INTO transactions
                (transaction_date, transaction_type, exchange_name, cryptocurrency,
                 amount, price_per_unit, total_value, fee_amount, fee_currency,
                 currency, notes, source, imported_at, record_hash)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'EUR', 'EUR', ?, ?, ?, ?)
        """, (
            tx_date, tx_type, exchange, crypto,
            amount, price, total_value, fee, notes,
            'web_manual_entry', now, record_hash,
        ))
        conn.commit()
        conn.close()
        flash(f"Added {tx_type} {amount} {crypto} @ €{price}", 'success')
    except Exception as e:
        flash(f'Error: {str(e)}', 'error')

    return redirect(url_for('manual'))


@app.route('/manual/delete/<int:tx_id>', methods=['POST'])
def manual_delete(tx_id):
    if db_exists():
        conn = get_db()
        conn.execute("DELETE FROM transactions WHERE id = ?", (tx_id,))
        conn.commit()
        conn.close()
        flash(f'Deleted transaction #{tx_id}', 'success')
    return redirect(url_for('manual'))


# ── API endpoints (for AJAX) ──────────────────────────────

@app.route('/api/db-stats')
def api_db_stats():
    if not db_exists():
        return jsonify({'error': 'No database'})
    conn = get_db()
    try:
        stats = {
            'transactions': conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0],
            'fifo_lots': conn.execute("SELECT COUNT(*) FROM fifo_lots").fetchone()[0],
            'matches': conn.execute("SELECT COUNT(*) FROM sale_lot_matches").fetchone()[0],
        }
        return jsonify(stats)
    except sqlite3.OperationalError as e:
        return jsonify({'error': str(e)})
    finally:
        conn.close()


# ── Template filters ──────────────────────────────────────

@app.template_filter('fmt_num')
def fmt_num_filter(n, decimals=0):
    if n is None:
        return '—'
    if decimals == 0:
        return f'{int(n):,}'.replace(',', '.')
    return f'{n:,.{decimals}f}'


@app.template_filter('fmt_eur')
def fmt_eur_filter(n):
    if n is None or n == 0:
        return '—'
    return f'€{n:,.2f}'


@app.template_filter('fmt_size')
def fmt_size_filter(n):
    if n < 1024:
        return f'{n} B'
    elif n < 1024 * 1024:
        return f'{n/1024:.1f} KB'
    else:
        return f'{n/1024/1024:.1f} MB'


# ── Main ──────────────────────────────────────────────────

if __name__ == '__main__':
    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(REPORTS_DIR, exist_ok=True)

    port = int(os.environ.get('FIFO_PORT', 5002))

    print("=" * 60)
    print("  Crypto FIFO Tracker — Web Interface")
    print("=" * 60)
    print(f"\n  Database:  {DATABASE_PATH}")
    print(f"  Data dir:  {DATA_DIR}")
    print(f"\n  Open: http://127.0.0.1:{port}")
    print(f"\n  Press CTRL+C to stop")
    print("=" * 60)

    app.run(debug=True, host='127.0.0.1', port=port)

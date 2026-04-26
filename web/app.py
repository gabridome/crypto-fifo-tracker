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
import subprocess
import shutil
import hashlib
import logging
from datetime import datetime
from collections import defaultdict
from flask import (Flask, render_template, request, redirect, url_for,
                   flash, jsonify, send_file)
from werkzeug.utils import secure_filename
from flask_wtf.csrf import CSRFProtect

logger = logging.getLogger(__name__)

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
app.secret_key = os.environ.get('SECRET_KEY', os.urandom(24))
csrf = CSRFProtect(app)

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

# CSV parsing extracted to csv_parser.py
from web.csv_parser import (parse_csv_deep, parse_csv_rows, USD_EXCHANGES, _parse_date)

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
        logger.warning("Failed to parse eurusd.csv at %s", eurusd_path, exc_info=True)

    # Cross-reference with USD exchange CSVs
    for fp in sorted(glob.glob(os.path.join(DATA_DIR, '*.csv'))):
        basename = os.path.basename(fp)
        if basename == 'eurusd.csv' or basename.startswith('sample_'):
            continue
        ex, _ = detect_exchange(basename)
        if ex not in USD_EXCHANGES:
            continue

        # Get date range of this CSV (try deep parse, fallback to generic scan)
        parsed = parse_csv_deep(fp, ex, DATA_DIR)
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


# ── CSV deep parser (see csv_parser.py) ──────────────────────

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
        logger.warning("Failed to parse date range from CSV %s", filepath, exc_info=True)
        return None, None

    if dates:
        return min(dates).strftime('%Y-%m-%d'), max(dates).strftime('%Y-%m-%d')
    return None, None


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
            logger.warning("Failed to count rows in %s", filepath, exc_info=True)
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

_context_cache = {'data': None, 'ts': 0}
_CONTEXT_TTL = 5  # seconds

@app.context_processor
def inject_globals():
    """Make wizard status and eurusd info available to all templates (cached 5s)."""
    import time
    now = time.time()
    if _context_cache['data'] is None or (now - _context_cache['ts']) > _CONTEXT_TTL:
        _context_cache['data'] = {
            'wizard': get_wizard_status(),
            'db_exists': db_exists(),
            'eurusd': check_eurusd(),
            'data_dir_name': os.path.basename(DATA_DIR),
        }
        _context_cache['ts'] = now
    return _context_cache['data']


# ── Home / Dashboard ───────────────────────────────────────

@app.route('/')
def index():
    return redirect(url_for('collect'))


# ── Exchange Data (unified page) ──────────────────────────

@app.route('/exchanges')
def exchanges():
    """Unified page: collect + import + status for all exchanges."""
    csv_files = scan_csv_files()
    db_stats = get_db_exchange_stats()
    source_stats = get_db_source_stats()

    # Attach per-file DB stats
    for f in csv_files:
        f['db_source'] = source_stats.get(f['filename'])

    # Group by exchange
    exchange_groups = defaultdict(list)
    for f in csv_files:
        exchange_groups[f['exchange']].append(f)

    # Build groups with CSV deep-parse comparison
    from web.csv_parser import parse_csv_deep
    groups = []
    for exchange in sorted(exchange_groups.keys()):
        files = exchange_groups[exchange]
        importer = files[0]['importer'] if files else None
        total_rows = sum(f['rows'] for f in files)
        db = db_stats.get(exchange, {})

        # CSV aggregate stats (from all files for this exchange)
        csv_stats = {}
        for f in files:
            try:
                deep = parse_csv_deep(f['filepath'], exchange, DATA_DIR)
                for key in ('buy_count', 'sell_count', 'buy_value', 'sell_value', 'total_fees', 'parse_errors'):
                    csv_stats[key] = csv_stats.get(key, 0) + deep.get(key, 0)
                if deep.get('min_date'):
                    if not csv_stats.get('min_date') or deep['min_date'] < csv_stats['min_date']:
                        csv_stats['min_date'] = deep['min_date']
                if deep.get('max_date'):
                    if not csv_stats.get('max_date') or deep['max_date'] > csv_stats['max_date']:
                        csv_stats['max_date'] = deep['max_date']
            except Exception:
                csv_stats['parse_errors'] = csv_stats.get('parse_errors', 0) + 1

        # Determine status
        has_db = db.get('total', 0) > 0
        has_csv = total_rows > 0
        if has_db and has_csv:
            # Compare counts
            csv_buys = csv_stats.get('buy_count', 0)
            csv_sells = csv_stats.get('sell_count', 0)
            db_buys = db.get('buys', 0)
            db_sells = db.get('sells', 0)
            if csv_buys == db_buys and csv_sells == db_sells:
                status = 'ok'
            elif abs(csv_buys - db_buys) <= max(1, csv_buys * 0.1) and abs(csv_sells - db_sells) <= max(1, csv_sells * 0.1):
                status = 'close'
            else:
                status = 'mismatch'
        elif has_csv and not has_db:
            status = 'not-imported'
        elif has_db and not has_csv:
            status = 'db-only'
        else:
            status = 'empty'

        # Last import time
        last_imported = None
        for f in files:
            src = f.get('db_source')
            if src and src.get('last_imported'):
                if not last_imported or src['last_imported'] > last_imported:
                    last_imported = src['last_imported']

        groups.append({
            'exchange': exchange,
            'files': files,
            'file_count': len(files),
            'total_rows': total_rows,
            'importer': importer,
            'db': db,
            'csv_stats': csv_stats,
            'status': status,
            'last_imported': last_imported,
            'multi': len(files) > 1,
            'has_docs': exchange in EXCHANGE_FIELD_MAP or exchange == 'Standard Format',
        })

    return render_template('exchange_data.html',
                           groups=groups,
                           page='exchanges')


@app.route('/exchange-docs/<exchange_name>')
def exchange_docs(exchange_name):
    """Per-exchange documentation: field mapping, download instructions, known issues."""
    field_map = EXCHANGE_FIELD_MAP.get(exchange_name)
    if not field_map and exchange_name in [e for _, e, _ in EXCHANGE_PATTERNS
                                           if 'standard_csv' in _]:
        field_map = EXCHANGE_FIELD_MAP.get('Standard Format')

    instructions = EXCHANGE_INSTRUCTIONS.get(exchange_name)

    # Load known issues for this exchange from the markdown file
    known_issues = []
    issues_path = os.path.join(PROJECT_ROOT, 'doc', 'known_import_issues.md')
    if os.path.exists(issues_path):
        try:
            with open(issues_path, 'r') as f:
                content = f.read()
            # Find the section for this exchange
            import re
            pattern = rf'### {re.escape(exchange_name)}.*?\n\n(.*?)(?=\n### |\n---|\n## |\Z)'
            match = re.search(pattern, content, re.DOTALL)
            if match:
                # Parse table rows
                for line in match.group(1).strip().split('\n'):
                    if line.startswith('|') and not line.startswith('| Problema') and not line.startswith('|--'):
                        parts = [p.strip() for p in line.split('|')[1:-1]]
                        if len(parts) >= 3:
                            known_issues.append({
                                'problem': parts[0],
                                'cause': parts[1],
                                'status': parts[2],
                                'notes': parts[3] if len(parts) > 3 else '',
                            })
        except Exception:
            pass

    return render_template('exchange_docs.html',
                           exchange_name=exchange_name,
                           field_map=field_map,
                           instructions=instructions,
                           known_issues=known_issues,
                           page='exchanges')


# ── Step 1: Collect (legacy, redirects to /exchanges) ─────

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
            parsed = parse_csv_deep(f['filepath'], exchange, DATA_DIR)
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
                all_csv_rows.extend(parse_csv_rows(f['filepath'], exchange, DATA_DIR))
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

    # Security: only allow SELECT statements (also accept CTE / WITH).
    # Strip line comments (`-- ...`) and blank lines before checking the
    # first keyword, so a query with a header comment is still accepted.
    sql_upper = sql.upper()
    sql_no_comments = '\n'.join(
        line for line in sql_upper.split('\n')
        if line.strip() and not line.strip().startswith('--')
    ).lstrip()

    forbidden = ('INSERT', 'UPDATE', 'DELETE', 'DROP', 'ALTER', 'CREATE',
                 'ATTACH', 'DETACH', 'PRAGMA', 'REPLACE', 'VACUUM', 'REINDEX')
    for kw in forbidden:
        if kw in sql_no_comments.split():
            return jsonify(error=f'Forbidden SQL keyword: {kw}'), 403

    if not sql_no_comments.startswith('SELECT') and not sql_no_comments.startswith('WITH'):
        return jsonify(error='Only SELECT queries are allowed'), 403

    if not db_exists():
        return jsonify(error='Database not found'), 500

    # Open DB in read-only mode
    db_uri = f'file:{DATABASE_PATH}?mode=ro'
    conn = sqlite3.connect(db_uri, uri=True)
    try:
        conn.row_factory = sqlite3.Row
        cursor = conn.execute(sql)
        columns = [desc[0] for desc in cursor.description] if cursor.description else []
        rows = [list(r) for r in cursor.fetchmany(500)]
        total = len(rows)
        if cursor.fetchone() is not None:
            total = f'{total}+'  # indicate truncation
        return jsonify(columns=columns, rows=rows, total=total, query=sql)
    except Exception as e:
        return jsonify(error=str(e)), 500
    finally:
        conn.close()


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

    # ── Validate inputs ──
    tx_type = request.form.get('type', '').strip()
    if tx_type not in ('BUY', 'SELL', 'DEPOSIT', 'WITHDRAWAL'):
        flash(f'Invalid transaction type: {tx_type}', 'error')
        return redirect(url_for('manual'))

    exchange = request.form.get('exchange', '').strip()
    if not exchange:
        flash('Exchange name is required', 'error')
        return redirect(url_for('manual'))

    crypto = request.form.get('crypto', '').strip().upper()
    if not crypto:
        flash('Cryptocurrency is required', 'error')
        return redirect(url_for('manual'))

    tx_date = request.form.get('date', '').strip()
    try:
        datetime.fromisoformat(tx_date.replace('Z', '+00:00').split('T')[0])
    except (ValueError, AttributeError):
        flash(f'Invalid date format: {tx_date}', 'error')
        return redirect(url_for('manual'))

    try:
        amount = float(request.form['amount'])
        if amount <= 0:
            raise ValueError("Amount must be positive")
    except (ValueError, KeyError) as e:
        flash(f'Invalid amount: {e}', 'error')
        return redirect(url_for('manual'))

    try:
        price = float(request.form['price'])
        if price < 0:
            raise ValueError("Price cannot be negative")
    except (ValueError, KeyError) as e:
        flash(f'Invalid price: {e}', 'error')
        return redirect(url_for('manual'))

    try:
        fee = float(request.form.get('fee', 0) or 0)
        if fee < 0:
            raise ValueError("Fee cannot be negative")
    except ValueError as e:
        flash(f'Invalid fee: {e}', 'error')
        return redirect(url_for('manual'))

    # ── Insert ──
    conn = get_db()
    try:
        total_value = amount * price
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
        flash(f"Added {tx_type} {amount} {crypto} @ €{price}", 'success')
    except Exception as e:
        conn.rollback()
        flash(f'Error: {str(e)}', 'error')
    finally:
        conn.close()

    return redirect(url_for('manual'))


@app.route('/manual/delete/<int:tx_id>', methods=['POST'])
def manual_delete(tx_id):
    if db_exists():
        conn = get_db()
        try:
            result = conn.execute(
                "DELETE FROM transactions WHERE id = ? AND source = 'web_manual_entry'",
                (tx_id,))
            if result.rowcount > 0:
                conn.commit()
                flash(f'Deleted manual transaction #{tx_id}', 'success')
            else:
                flash(f'Transaction #{tx_id} is not a manual entry or does not exist', 'error')
        finally:
            conn.close()
    return redirect(url_for('manual'))


# ── Step 7: Audit Trail ──────────────────────────────────

@app.route('/audit')
def audit():
    available_years = []
    if db_exists():
        conn = get_db()
        try:
            rows = conn.execute("""
                SELECT DISTINCT strftime('%Y', sale_date) as year
                FROM sale_lot_matches ORDER BY year
            """).fetchall()
            available_years = [r['year'] for r in rows]
        except sqlite3.OperationalError:
            pass
        finally:
            conn.close()
    return render_template('audit.html', available_years=available_years, page='audit')


@app.route('/api/audit/<int:year>')
def api_audit(year):
    if not db_exists():
        return jsonify({'error': 'No database'})

    conn = get_db()
    try:
        rows = conn.execute("""
            SELECT
                slm.id as match_id,
                date(slm.sale_date) as sale_day,
                t_sell.exchange_name,
                (slm.holding_period_days >= 365) as is_exempt,
                slm.cryptocurrency,
                slm.amount_sold,
                slm.purchase_price_per_unit,
                slm.sale_price_per_unit,
                slm.cost_basis,
                slm.proceeds,
                slm.gain_loss,
                slm.holding_period_days,
                slm.purchase_date,
                slm.sale_date,
                slm.sale_transaction_id,
                t_sell.fee_amount as sell_tx_fee,
                t_sell.source as sell_source,
                t_sell.record_hash as sell_hash,
                t_sell.imported_at as sell_imported_at,
                slm.fifo_lot_id,
                fl.purchase_transaction_id as buy_transaction_id,
                t_buy.fee_amount as buy_tx_fee,
                t_buy.source as buy_source,
                t_buy.record_hash as buy_hash,
                t_buy.imported_at as buy_imported_at,
                fl.original_amount as lot_original,
                fl.remaining_amount as lot_remaining
            FROM sale_lot_matches slm
            JOIN transactions t_sell ON slm.sale_transaction_id = t_sell.id
            JOIN fifo_lots fl ON slm.fifo_lot_id = fl.id
            JOIN transactions t_buy ON fl.purchase_transaction_id = t_buy.id
            WHERE slm.sale_date >= ? AND slm.sale_date < ?
            ORDER BY date(slm.sale_date), t_sell.exchange_name, slm.id
        """, (f'{year}-01-01', f'{year + 1}-01-01')).fetchall()

        matches = [dict(r) for r in rows]

        # Summary
        total_gain = sum(m['gain_loss'] for m in matches)
        exempt = [m for m in matches if m['is_exempt']]
        taxable = [m for m in matches if not m['is_exempt']]

        # Count IRS rows (unique grouping keys)
        row_keys = set()
        for m in matches:
            row_keys.add((m['sale_day'], m['exchange_name'], m['is_exempt']))

        summary = {
            'total_gain': round(total_gain, 2),
            'exempt_gain': round(sum(m['gain_loss'] for m in exempt), 2),
            'taxable_gain': round(sum(m['gain_loss'] for m in taxable), 2),
            'total_proceeds': round(sum(m['proceeds'] for m in matches), 2),
            'total_cost_basis': round(sum(m['cost_basis'] for m in matches), 2),
            'exempt_count': len(set((m['sale_day'], m['exchange_name']) for m in exempt)),
            'taxable_count': len(set((m['sale_day'], m['exchange_name']) for m in taxable)),
            'row_count': len(row_keys),
            'match_count': len(matches),
        }

        return jsonify({'year': year, 'matches': matches, 'summary': summary})

    except sqlite3.OperationalError as e:
        return jsonify({'error': str(e)})
    finally:
        conn.close()


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
    if n is None:
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

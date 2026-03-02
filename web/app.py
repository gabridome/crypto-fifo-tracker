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
    Open http://127.0.0.1:5000
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
from flask import (Flask, render_template, request, redirect, url_for,
                   flash, jsonify, send_file)

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

DATA_DIR = os.path.join(PROJECT_ROOT, 'data')
REPORTS_DIR = os.path.join(DATA_DIR, 'reports')
BACKUPS_DIR = os.path.join(DATA_DIR, 'backups')

app = Flask(__name__)
app.secret_key = 'crypto-fifo-local-dev'  # local only, no real security needed

# ── Helpers ─────────────────────────────────────────────────

def get_db():
    """Get a database connection with Row factory."""
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def db_exists():
    return os.path.exists(DATABASE_PATH)

# Exchanges whose CSV data is in USD and requires EUR conversion via eurusd.csv
USD_EXCHANGES = {'Bitfinex', 'Coinbase Prime', 'Kraken', 'Mt.Gox'}

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
    (r'wirex',                      'Wirex',           'importers/import_wirex.py'),
    (r'trt',                        'TRT',             'importers/import_trt_with_fees.py'),
    (r'changely',                   'changely',        'importers/import_standard_csv.py'),
    (r'coinpal',                    'Coinpal',         'importers/import_standard_csv.py'),
    (r'gdtre',                      'GDTRE',           'importers/import_standard_csv.py'),
    (r'inheritance',                'Inheritance',     'importers/import_standard_csv.py'),
    (r'otc',                        'OTC',             'importers/import_standard_csv.py'),
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

CSV_PARSE_RULES = {
    'Binance': {
        'date_col': 'Date(UTC)',
        'type_col': 'Side',
        'amount_col': 'Executed',
        'value_col': 'Amount',
        'fee_col': 'Fee',
        'buy_types': ['BUY'],
        'sell_types': ['SELL'],
        'strip_suffix': True,
    },
    'Coinbase': {
        'date_col': 'Timestamp',
        'type_col': 'Transaction Type',
        'amount_col': 'Quantity Transacted',
        'value_col': 'Total (inclusive of fees and/or spread)',
        'fee_col': None,
        'buy_types': ['Buy', 'Advanced Trade Buy'],
        'sell_types': ['Sell', 'Advanced Trade Sell'],
        'strip_suffix': False,
    },
    'Bitstamp': {
        'date_col': 'Datetime',
        'type_col': 'Type',
        'amount_col': 'Amount',
        'value_col': 'Value',
        'fee_col': 'Fee',
        'buy_types': ['Buy', 'Market buy'],
        'sell_types': ['Sell', 'Market sell'],
        'strip_suffix': False,
    },
    'Kraken': {
        'date_col': 'time',
        'type_col': 'type',
        'amount_col': 'amount',
        'value_col': 'cost',
        'fee_col': 'fee',
        'buy_types': ['buy'],
        'sell_types': ['sell'],
        'strip_suffix': False,
    },
    'Bitfinex': {
        'date_col': 'Date',
        'type_col': '_amount_sign',  # special: positive=BUY, negative=SELL
        'amount_col': 'Amount',
        'value_col': 'Price',
        'fee_col': 'Fee',
        'buy_types': ['BUY'],
        'sell_types': ['SELL'],
        'strip_suffix': False,
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
        return float(str(val).replace(',', '').replace('€', '').replace('$', '').strip())
    except (ValueError, TypeError):
        return 0.0


def _parse_date(s):
    if not s:
        return None
    s = str(s).strip()
    for fmt in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%dT%H:%M:%S', '%Y-%m-%dT%H:%M:%S%z',
                '%Y-%m-%d', '%d/%m/%Y %H:%M:%S', '%b %d, %Y, %I:%M %p', '%b %d, %Y'):
        try:
            return datetime.strptime(s, fmt)
        except (ValueError, TypeError):
            continue
    try:
        return datetime.fromisoformat(s.replace('Z', '+00:00'))
    except (ValueError, TypeError):
        return None


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

    try:
        with open(filepath, 'r', encoding='utf-8-sig') as f:
            sniffer = csv.Sniffer()
            sample = f.read(4096)
            f.seek(0)
            try:
                dialect = sniffer.sniff(sample)
            except csv.Error:
                dialect = csv.excel

            reader = csv.DictReader(f, dialect=dialect)
            dates = []

            for row in reader:
                row = {k.strip(): v for k, v in row.items() if k}
                result['total_rows'] += 1

                try:
                    # Date
                    d = _parse_date(row.get(rules['date_col'], ''))
                    if d:
                        dates.append(d)

                    # Type
                    type_val = row.get(rules['type_col'], '').strip()
                    is_buy = type_val in rules['buy_types']
                    is_sell = type_val in rules['sell_types']

                    # Value
                    val_raw = row.get(rules['value_col'], '') if rules['value_col'] else ''
                    val = _strip_currency(val_raw) if rules['strip_suffix'] else _safe_float(val_raw)

                    if is_buy:
                        result['buy_count'] += 1
                        result['buy_value'] += abs(val)
                    elif is_sell:
                        result['sell_count'] += 1
                        result['sell_value'] += abs(val)

                    # Fee
                    if rules['fee_col'] and rules['fee_col'] in row:
                        fee_raw = row[rules['fee_col']]
                        fee = _strip_currency(fee_raw) if rules['strip_suffix'] else _safe_float(fee_raw)
                        result['total_fees'] += abs(fee)

                except Exception:
                    result['parse_errors'] += 1

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

    rows = []
    try:
        with open(filepath, 'r', encoding='utf-8-sig') as f:
            sniffer = csv.Sniffer()
            sample = f.read(4096)
            f.seek(0)
            try:
                dialect = sniffer.sniff(sample)
            except csv.Error:
                dialect = csv.excel

            reader = csv.DictReader(f, dialect=dialect)
            for line_num, row in enumerate(reader, 2):  # line 2 = first data row
                row = {k.strip(): v for k, v in row.items() if k}
                try:
                    date_str = row.get(rules['date_col'], '').strip()
                    d = _parse_date(date_str)
                    type_val = row.get(rules['type_col'], '').strip()

                    # Amount
                    amt_raw = row.get(rules['amount_col'], '')
                    amt = _strip_currency(amt_raw) if rules['strip_suffix'] else _safe_float(amt_raw)

                    # Value
                    val_raw = row.get(rules['value_col'], '') if rules['value_col'] else ''
                    val = _strip_currency(val_raw) if rules['strip_suffix'] else _safe_float(val_raw)

                    # Fee
                    fee = 0.0
                    if rules['fee_col'] and rules['fee_col'] in row:
                        fee_raw = row[rules['fee_col']]
                        fee = _strip_currency(fee_raw) if rules['strip_suffix'] else _safe_float(fee_raw)

                    # Pair / crypto
                    pair = ''
                    for col in ('Pair', 'Asset', 'asset', 'pair'):
                        if col in row and row[col].strip():
                            pair = row[col].strip()
                            break

                    # Classify type
                    is_buy = type_val in rules['buy_types']
                    is_sell = type_val in rules['sell_types']
                    norm_type = 'BUY' if is_buy else ('SELL' if is_sell else type_val)

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
                        'fee': abs(fee),
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


def post_import_source_update(source_filename, exchange_name):
    """
    After an exchange-specific importer runs (which doesn't know about source tracking),
    update the newly inserted records that have NULL source/hash.

    This is the bridge: old importers INSERT without source/hash,
    this function fills them in afterward.
    """
    if not db_exists():
        return 0

    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    now = datetime.now().isoformat()

    # Set source and imported_at on NULL records for this exchange
    conn.execute(
        "UPDATE transactions SET source = ?, imported_at = ? "
        "WHERE exchange_name = ? AND source IS NULL",
        (source_filename, now, exchange_name)
    )

    # Compute hash for records without one
    rows = conn.execute("""
        SELECT id, source, transaction_date, transaction_type,
               exchange_name, cryptocurrency, amount, total_value, fee_amount
        FROM transactions
        WHERE record_hash IS NULL AND exchange_name = ?
    """, (exchange_name,)).fetchall()

    for row in rows:
        h = compute_record_hash(
            row['source'], row['transaction_date'], row['transaction_type'],
            row['exchange_name'], row['cryptocurrency'],
            row['amount'], row['total_value'], row['fee_amount']
        )
        conn.execute("UPDATE transactions SET record_hash = ? WHERE id = ?", (h, row['id']))

    conn.commit()
    updated = len(rows)
    conn.close()
    return updated


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
    dest = os.path.join(DATA_DIR, file.filename)
    file.save(dest)
    exchange, _ = detect_exchange(file.filename)
    flash(f'Uploaded {file.filename} → detected as {exchange}', 'success')
    return redirect(url_for('collect'))


@app.route('/collect/delete/<filename>', methods=['POST'])
def delete_csv(filename):
    filepath = os.path.join(DATA_DIR, filename)
    if os.path.exists(filepath) and filepath.endswith('.csv'):
        os.remove(filepath)
        flash(f'Deleted {filename}', 'success')
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
    field_map = EXCHANGE_FIELD_MAP

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


def merge_csv_files(filepaths):
    """
    Merge multiple CSVs with the same structure into one temp file.
    Keeps header from first file, appends data rows from subsequent files.
    Returns path to the merged temp file.
    """
    import tempfile
    merged_path = tempfile.mktemp(suffix='.csv', dir=DATA_DIR)

    header = None
    with open(merged_path, 'w', encoding='utf-8', newline='') as out:
        for i, fp in enumerate(sorted(filepaths)):
            with open(fp, 'r', encoding='utf-8-sig') as inp:
                lines = inp.readlines()
                if not lines:
                    continue
                if i == 0:
                    # First file: write header + all data
                    header = lines[0]
                    out.writelines(lines)
                else:
                    # Subsequent files: skip header, append data
                    file_header = lines[0]
                    # Verify header matches (same columns)
                    if file_header.strip().split(',')[:3] == header.strip().split(',')[:3]:
                        out.writelines(lines[1:])
                    else:
                        # Different structure — still append but note it
                        out.writelines(lines[1:])

    return merged_path


@app.route('/import/run/<exchange_name>', methods=['POST'])
def run_import(exchange_name):
    """Import all CSV files for an exchange.

    Standard CSV importers: import file-by-file (DELETE by source).
    Exchange-specific importers: merge + import (DELETE by exchange), then backfill source.
    """
    csv_files = scan_csv_files()
    exchange_files = [f for f in csv_files if f['exchange'] == exchange_name]

    if not exchange_files:
        flash(f'No CSV files found for {exchange_name}', 'error')
        return redirect(url_for('import_page'))

    importer_script = exchange_files[0]['importer']
    if not importer_script:
        flash(f'No importer available for {exchange_name}', 'error')
        return redirect(url_for('import_page'))

    importer_path = os.path.join(PROJECT_ROOT, importer_script)
    if not os.path.exists(importer_path):
        flash(f'Importer script not found: {importer_script}', 'error')
        return redirect(url_for('import_page'))

    is_standard = importer_script.endswith('import_standard_csv.py')

    merged_path = None
    try:
        if is_standard:
            # ── Standard CSV: import file by file (DELETE by source) ──
            total_inserted = 0
            for ef in exchange_files:
                cmd = [sys.executable, importer_path, ef['filepath'], exchange_name]
                result = subprocess.run(
                    cmd, cwd=PROJECT_ROOT,
                    capture_output=True, text=True,
                    input='1\n', timeout=120,
                )
                output = result.stdout + result.stderr
                if result.returncode == 0:
                    total_inserted += ef['rows']
                else:
                    flash(f'Import error for {ef["filename"]}: {output[-500:]}', 'error')
                    return redirect(url_for('import_page'))

            if len(exchange_files) == 1:
                flash(f'Imported {exchange_name} ({exchange_files[0]["filename"]}, {total_inserted:,} rows)', 'success')
            else:
                filenames = ', '.join(f['filename'] for f in exchange_files)
                flash(f'Imported {exchange_name}: {len(exchange_files)} files ({filenames}), {total_inserted:,} total rows', 'success')

        elif len(exchange_files) > 1:
            # ── Exchange-specific, multi-file: merge + import ──
            filepaths = [f['filepath'] for f in exchange_files]
            merged_path = merge_csv_files(filepaths)
            total_rows = sum(f['rows'] for f in exchange_files)
            filenames = ', '.join(f['filename'] for f in exchange_files)
            source_label = '+'.join(f['filename'] for f in exchange_files)

            main_file = exchange_files[0]['filepath']
            backup_path = main_file + '.bak'

            if os.path.exists(main_file):
                shutil.copy2(main_file, backup_path)
            shutil.copy2(merged_path, main_file)

            try:
                result = subprocess.run(
                    [sys.executable, importer_path],
                    cwd=PROJECT_ROOT,
                    capture_output=True, text=True,
                    input='1\n', timeout=120,
                )
                output = result.stdout + result.stderr
                if result.returncode == 0:
                    # Post-import: backfill source/hash for new records
                    updated = post_import_source_update(source_label, exchange_name)
                    flash(f'Imported {exchange_name}: merged {len(exchange_files)} files ({filenames}), {total_rows:,} rows ({updated} hashed)', 'success')
                else:
                    flash(f'Import error for {exchange_name}: {output[-500:]}', 'error')
            finally:
                if os.path.exists(backup_path):
                    shutil.move(backup_path, main_file)

        else:
            # ── Exchange-specific, single file ──
            source_label = exchange_files[0]['filename']

            result = subprocess.run(
                [sys.executable, importer_path],
                cwd=PROJECT_ROOT,
                capture_output=True, text=True,
                input='1\n', timeout=120,
            )
            output = result.stdout + result.stderr
            if result.returncode == 0:
                # Post-import: backfill source/hash for new records
                updated = post_import_source_update(source_label, exchange_name)
                flash(f'Imported {exchange_name} ({source_label}, {updated} records hashed)', 'success')
            else:
                flash(f'Import error for {exchange_name}: {output[-500:]}', 'error')

    except subprocess.TimeoutExpired:
        flash(f'Import timed out for {exchange_name}', 'error')
    except Exception as e:
        flash(f'Import failed: {str(e)}', 'error')
    finally:
        if merged_path and os.path.exists(merged_path):
            os.remove(merged_path)

    return redirect(url_for('import_page'))


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

    return render_template('reports.html',
                           stats=stats,
                           available_years=available_years,
                           existing_reports=existing_reports,
                           page='reports')


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
    filepath = os.path.join(REPORTS_DIR, filename)
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

    print("=" * 60)
    print("  Crypto FIFO Tracker — Web Interface")
    print("=" * 60)
    print(f"\n  Database:  {DATABASE_PATH}")
    print(f"  Data dir:  {DATA_DIR}")
    print(f"\n  Open: http://127.0.0.1:5000")
    print(f"\n  Press CTRL+C to stop")
    print("=" * 60)

    app.run(debug=True, host='127.0.0.1', port=5000)

"""
CSV parsing logic for the crypto-fifo-tracker web interface.

Extracts exchange CSV files into normalized trade rows. Supports:
- Standard CSV format (import_standard_csv.py)
- Exchange-specific formats (Binance, Coinbase, Bitstamp, etc.)
- Paired-ledger format (Kraken: 2 rows per trade joined by refid)
- Grouped-trade format (TRT: 3-4 rows per trade grouped by date+desc)

Public API:
    parse_csv_deep(filepath, exchange, data_dir) → dict (aggregate statistics)
    parse_csv_rows(filepath, exchange, data_dir) → list[dict] (individual rows)
"""

import csv
import os
import re
import logging
from datetime import datetime
from collections import defaultdict

import pytz

logger = logging.getLogger(__name__)

# Exchanges whose CSV data is in USD and requires EUR conversion via eurusd.csv
USD_EXCHANGES = {'Bitfinex', 'Coinbase Prime', 'Kraken', 'Mt.Gox'}


# ── CSV parse rules ──────────────────────────────────────────

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
        'pair_col': 'Pair',
        'usd_pairs': ['BTCUSDT', 'BTCBUSD'],
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
        'tz_source': 'CET',
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
        'asset_col': 'Asset',
        'asset_filter': ['BTC'],
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
        'all_usd': True,
        'type_filter_col': 'status',
        'type_filter_val': 'Completed',
        'pair_col': 'market',
        'pair_filter': ['BTC/USD'],
    },
    'Bitstamp': {
        'date_col': 'Datetime',
        'type_col': 'Sub Type',
        'amount_col': 'Amount',
        'value_col': 'Value',
        'fee_col': 'Fee',
        'buy_types': ['Buy'],
        'sell_types': ['Sell'],
        'strip_suffix': True,
        'all_usd': True,
        'type_filter_col': 'Type',
        'type_filter_val': 'Market',
    },
    'Kraken': {
        'paired_ledger': True,
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
        'type_col': '_amount_sign',
        'amount_col': 'AMOUNT',
        'value_col': 'PRICE',
        'fee_col': 'FEE',
        'buy_types': ['BUY'],
        'sell_types': ['SELL'],
        'strip_suffix': False,
        'value_is_unit_price': True,
        'fee_is_crypto': True,
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
        'fee_is_crypto': True,
        'dedup_col': 'ID',
        'currency_col': 'Currency',
        'usd_currencies': ['USD'],
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
        'aggregate_by_time': True,
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


# ── Helper functions ─────────────────────────────────────────

def _strip_currency(val):
    """'0.027BTC' or '1731.99EUR' -> float."""
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
    # Strip trailing timezone abbreviations
    s = re.sub(r'\s+(?:UTC|GMT|CET|CEST)$', '', s)
    # Normalize abbreviated months with period: "Nov." -> "Nov"
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


# ── CryptoPrices loader ──────────────────────────────────────

_crypto_prices_cache = None


def _get_crypto_prices(data_dir):
    """Lazy-load CryptoPrices for CSV parsers."""
    global _crypto_prices_cache
    if _crypto_prices_cache is None:
        prices_path = os.path.join(data_dir, 'crypto_prices.csv')
        if os.path.exists(prices_path):
            try:
                from importers.crypto_prices import CryptoPrices
                _crypto_prices_cache = CryptoPrices(prices_path)
            except Exception:
                logger.warning("Failed to load CryptoPrices from %s", prices_path, exc_info=True)
                _crypto_prices_cache = False  # mark as attempted
    return _crypto_prices_cache if _crypto_prices_cache is not False else None


# ── Paired ledger parser (Kraken) ────────────────────────────

def _parse_paired_ledger(filepath, rules):
    """
    Parse paired-row ledger (Kraken) into normalized trade dicts.
    Each trade = 2 CSV rows (BTC + EUR) joined by refid.
    Yields dicts with: line, date_str, date, date_day, type, is_trade, pair, amount, value, fee, total_rows.
    """
    try:
        with open(filepath, 'r', encoding='utf-8-sig') as f:
            reader = csv.DictReader(f)
            trades = defaultdict(dict)
            filter_col = rules.get('type_filter_col')
            filter_val = rules.get('type_filter_val')
            crypto = rules['crypto_asset']
            fiat = rules['fiat_asset']
            line_map = {}  # refid -> first CSV line number
            total_rows = 0

            for line_num, row in enumerate(reader, 2):
                row = {k.strip(): v for k, v in row.items() if k}
                total_rows += 1
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

            results = []
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
                results.append({
                    'line': line_map.get(refid, 0),
                    'date_str': data.get('date_str', ''),
                    'date': d.strftime('%Y-%m-%d %H:%M') if d else None,
                    'date_day': d.strftime('%Y-%m-%d') if d else None,
                    'date_obj': d,
                    'type_raw': norm_type,
                    'type': norm_type,
                    'is_trade': True,
                    'pair': f'{crypto}/{fiat}',
                    'amount': abs(ca),
                    'value': abs(fa),
                    'fee': fee,
                })
            results.sort(key=lambda r: r['date_str'])
            return results, total_rows
    except Exception:
        logger.warning("Failed to parse paired ledger from %s", filepath, exc_info=True)
        return [], 0


# ── Grouped trade parser (TRT) ──────────────────────────────

def _parse_trt_grouped(filepath, rules):
    """
    Parse TRT multi-line grouped trades into normalized trade dicts.
    Each trade = 3-4 rows grouped by (date, description).
    Returns (list[dict], total_rows).
    """
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
            total_rows = 0
            for line_num, row in enumerate(reader, 2):
                row = {k.strip(): v for k, v in row.items() if k}
                total_rows += 1
                desc = row.get(rules['desc_col'], '').strip()
                if desc_filter not in desc:
                    continue
                date_str = row.get(rules['date_col'], '').strip()
                key = (date_str, desc)
                groups[key]['rows'].append(row)
                if groups[key]['first_line'] == 0:
                    groups[key]['first_line'] = line_num

            results = []
            for (date_str, _desc), g in groups.items():
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
                    results.append({
                        'line': g['first_line'],
                        'date_str': date_str,
                        'date': d.strftime('%Y-%m-%d %H:%M') if d else None,
                        'date_day': d.strftime('%Y-%m-%d') if d else None,
                        'date_obj': d,
                        'type_raw': trade_type,
                        'type': trade_type,
                        'is_trade': True,
                        'pair': f'{crypto}/{fiat}',
                        'amount': btc_amount,
                        'value': eur_amount,
                        'fee': fee_eur,
                    })
            results.sort(key=lambda r: r['date_str'])
            return results, total_rows
    except Exception:
        logger.warning("Failed to parse TRT grouped trades from %s", filepath, exc_info=True)
        return [], 0


# ── Generic CSV row parser ───────────────────────────────────

def _parse_csv_common(filepath, rules, data_dir):
    """
    Parse a standard/exchange-specific CSV into normalized trade rows.
    Yields dicts with: line, date_str, date, date_day, date_obj, type_raw, type,
    is_trade, pair, amount, value, fee, is_counterpart.
    Also yields a special sentinel dict with key '_meta' containing total_rows, parse_errors.
    """
    total_rows = 0
    parse_errors = 0

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
            ecb = ECBRates(os.path.join(data_dir, 'eurusd.csv'))
        except Exception:
            logger.warning("Failed to load ECB rates for CSV parse", exc_info=True)

    def _to_eur(raw_val, date, is_usd_row, fee_currency=None):
        """Convert a value to EUR. Handles BTC fees via price lookup."""
        if fee_currency == 'BTC':
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

            # Timezone conversion
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

            for line_num, row in enumerate(reader, 2):
                row = {k.strip(): v for k, v in row.items() if k}
                total_rows += 1

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
                    date_str = row.get(rules['date_col'], '').strip()
                    d = _parse_date(date_str)
                    if d and tz_source and d.tzinfo is None:
                        d = tz_source.localize(d).astimezone(pytz.UTC).replace(tzinfo=None)

                    # Check if this row is a USD-quoted pair
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

                    # Amount (needed early for sign-based type and unit-price calc)
                    amt_raw = row.get(rules['amount_col'], '')
                    amt = _strip_currency(amt_raw) if rules['strip_suffix'] else _safe_float(amt_raw)

                    # Type -- derive from amount sign if no type column
                    if type_from_sign:
                        is_buy = amt > 0
                        is_sell = amt < 0
                        type_val = 'BUY' if is_buy else ('SELL' if is_sell else '')
                    else:
                        type_val = row.get(rules['type_col'], '').strip()
                        is_buy = type_val in rules['buy_types']
                        is_sell = type_val in rules['sell_types']
                    norm_type = 'BUY' if is_buy else ('SELL' if is_sell else type_val)

                    # Value
                    val_raw = row.get(rules['value_col'], '') if rules['value_col'] else ''
                    val = _strip_currency(val_raw) if rules['strip_suffix'] else _safe_float(val_raw)

                    # Crypto-to-crypto: total_value is in crypto, not fiat
                    if is_crypto_currency and d:
                        cp = _get_crypto_prices(data_dir)
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
                        val = abs(amt) * val  # price_per_unit x amount = total

                    # If no value from CSV, try crypto prices (Wirex, etc.)
                    if val == 0 and abs(amt) > 0 and d and not rules.get('value_col'):
                        cp = _get_crypto_prices(data_dir)
                        asset = next(iter(asset_filter)) if asset_filter else 'BTC'
                        if cp:
                            cp_val = cp.crypto_to_eur(asset, abs(amt), d)
                            if cp_val is not None:
                                val = cp_val

                    # Fee
                    fee = 0.0
                    if (is_buy or is_sell) and rules['fee_col'] and rules['fee_col'] in row:
                        fee_raw = row[rules['fee_col']]
                        fee_val = _strip_currency(fee_raw) if rules['strip_suffix'] else _safe_float(fee_raw)
                        if fee_is_crypto:
                            fee = abs(fee_val) * abs(val) / abs(amt) if amt != 0 else 0.0
                        else:
                            fee_is_btc = rules['strip_suffix'] and str(fee_raw).strip().endswith('BTC')
                            if fee_is_btc:
                                fee = 0.0
                            elif is_usd_row and ecb and d:
                                fee = ecb.usd_to_eur(abs(fee_val), d)
                            else:
                                fee = abs(fee_val)

                    # Pair / crypto detection
                    pair = ''
                    for col in ('PAIR', 'Pair', 'Asset', 'asset', 'pair'):
                        if col in row and row[col].strip():
                            pair = row[col].strip()
                            break

                    yield {
                        'line': line_num,
                        'date_str': date_str,
                        'date': d.strftime('%Y-%m-%d %H:%M') if d else None,
                        'date_day': d.strftime('%Y-%m-%d') if d else None,
                        'date_obj': d,
                        'type_raw': type_val,
                        'type': norm_type,
                        'is_trade': is_buy or is_sell,
                        'pair': pair,
                        'amount': abs(amt),
                        'value': abs(val),
                        'fee': fee,
                        'is_counterpart': False,
                    }

                    # Crypto-to-crypto: emit counterpart row
                    if is_crypto_currency and (is_buy or is_sell) and d:
                        counter_val = 0.0
                        counter_amount = _safe_float(val_raw)
                        cp = _get_crypto_prices(data_dir)
                        if cp and row_currency and counter_amount > 0:
                            cp_val = cp.crypto_to_eur(row_currency, counter_amount, d)
                            if cp_val is not None:
                                counter_val = cp_val
                        counter_type = 'SELL' if is_buy else 'BUY'
                        yield {
                            'line': line_num,
                            'date_str': date_str,
                            'date': d.strftime('%Y-%m-%d %H:%M') if d else None,
                            'date_day': d.strftime('%Y-%m-%d') if d else None,
                            'date_obj': d,
                            'type_raw': f'{counter_type} (counterpart)',
                            'type': counter_type,
                            'is_trade': True,
                            'pair': row_currency,
                            'amount': counter_amount,
                            'value': counter_val,
                            'fee': 0,
                            'is_counterpart': True,
                        }

                except Exception:
                    logger.debug("Parse error at line %d in %s", line_num, filepath, exc_info=True)
                    parse_errors += 1
                    yield {
                        'line': line_num,
                        'date_str': '', 'date': None, 'date_day': None, 'date_obj': None,
                        'type_raw': '?', 'type': 'PARSE_ERROR',
                        'is_trade': False, 'pair': '',
                        'amount': 0, 'value': 0, 'fee': 0,
                        'is_counterpart': False,
                        '_parse_error': True,
                    }

    except Exception:
        logger.warning("Failed to parse CSV from %s", filepath, exc_info=True)

    # Yield sentinel with metadata
    yield {'_meta': True, 'total_rows': total_rows, 'parse_errors': parse_errors}


def _aggregate_by_time(rows):
    """Aggregate fills by timestamp (Bybit: multiple fills -> one trade per timestamp)."""
    groups = defaultdict(list)
    non_trade = []
    for r in rows:
        if r['is_trade'] and r['date_str']:
            key = (r['date_str'], r['type'])
            groups[key].append(r)
        else:
            non_trade.append(r)
    if not groups:
        return rows
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
    return agg_rows


# ── Public API ───────────────────────────────────────────────

def parse_csv_deep(filepath, exchange, data_dir):
    """Parse a CSV and extract comparable aggregate statistics.

    Returns a dict with keys: buy_count, sell_count, buy_value, sell_value,
    total_fees, min_date, max_date, total_rows, parse_errors.
    """
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
            logger.warning("Failed to count rows in %s", filepath, exc_info=True)
        return result

    # --- Paired ledger (Kraken) or grouped trade (TRT) ---
    if rules.get('paired_ledger'):
        trades, total_rows = _parse_paired_ledger(filepath, rules)
        result['total_rows'] = total_rows
        dates = []
        for t in trades:
            if t['type'] == 'BUY':
                result['buy_count'] += 1
                result['buy_value'] += t['value']
            elif t['type'] == 'SELL':
                result['sell_count'] += 1
                result['sell_value'] += t['value']
            result['total_fees'] += t['fee']
            if t['date_obj']:
                dates.append(t['date_obj'])
        if dates:
            result['min_date'] = min(dates).strftime('%Y-%m-%d')
            result['max_date'] = max(dates).strftime('%Y-%m-%d')
        return result

    if rules.get('grouped_trade'):
        trades, total_rows = _parse_trt_grouped(filepath, rules)
        result['total_rows'] = total_rows
        dates = []
        for t in trades:
            if t['type'] == 'BUY':
                result['buy_count'] += 1
                result['buy_value'] += t['value']
            elif t['type'] == 'SELL':
                result['sell_count'] += 1
                result['sell_value'] += t['value']
            result['total_fees'] += t['fee']
            if t['date_obj']:
                dates.append(t['date_obj'])
        if dates:
            result['min_date'] = min(dates).strftime('%Y-%m-%d')
            result['max_date'] = max(dates).strftime('%Y-%m-%d')
        return result

    # --- Standard / exchange-specific CSV ---
    aggregate_by_time = rules.get('aggregate_by_time', False)
    agg_buy_times = set()
    agg_sell_times = set()
    dates = []

    for row in _parse_csv_common(filepath, rules, data_dir):
        # Sentinel with metadata
        if row.get('_meta'):
            result['total_rows'] = row['total_rows']
            result['parse_errors'] = row['parse_errors']
            continue

        # Skip parse errors for aggregation (already counted)
        if row.get('_parse_error'):
            continue

        is_buy = row['type'] == 'BUY'
        is_sell = row['type'] == 'SELL'
        is_trade = row['is_trade']
        d = row['date_obj']
        val = row['value']

        if is_buy and not row.get('is_counterpart'):
            if aggregate_by_time and d:
                agg_buy_times.add(d.strftime('%Y-%m-%d %H:%M:%S'))
            else:
                result['buy_count'] += 1
            result['buy_value'] += val
            if d:
                dates.append(d)
        elif is_sell and not row.get('is_counterpart'):
            if aggregate_by_time and d:
                agg_sell_times.add(d.strftime('%Y-%m-%d %H:%M:%S'))
            else:
                result['sell_count'] += 1
            result['sell_value'] += val
            if d:
                dates.append(d)

        # Counterpart rows (crypto-to-crypto)
        if row.get('is_counterpart'):
            if is_buy:
                result['buy_count'] += 1
                result['buy_value'] += val
            elif is_sell:
                result['sell_count'] += 1
                result['sell_value'] += val

        # Fee (only for non-counterpart trade rows)
        if is_trade and not row.get('is_counterpart'):
            result['total_fees'] += row['fee']

    # Aggregate counts by unique timestamp (Bybit: fills -> trades)
    if aggregate_by_time:
        result['buy_count'] += len(agg_buy_times)
        result['sell_count'] += len(agg_sell_times)

    if dates:
        result['min_date'] = min(dates).strftime('%Y-%m-%d')
        result['max_date'] = max(dates).strftime('%Y-%m-%d')

    return result


def parse_csv_rows(filepath, exchange, data_dir):
    """Parse CSV into individual rows for row-level matching.

    Returns a list of dicts with keys: line, date_str, date, date_day,
    type_raw, type, is_trade, pair, amount, value, fee.
    """
    rules = CSV_PARSE_RULES.get(exchange)
    if not rules:
        return []

    # --- Paired ledger (Kraken) or grouped trade (TRT) ---
    if rules.get('paired_ledger'):
        trades, _ = _parse_paired_ledger(filepath, rules)
        # Remove internal date_obj key
        for t in trades:
            t.pop('date_obj', None)
        return trades

    if rules.get('grouped_trade'):
        trades, _ = _parse_trt_grouped(filepath, rules)
        for t in trades:
            t.pop('date_obj', None)
        return trades

    # --- Standard / exchange-specific CSV ---
    rows = []
    for row in _parse_csv_common(filepath, rules, data_dir):
        if row.get('_meta'):
            continue
        # Remove internal keys
        row.pop('date_obj', None)
        row.pop('is_counterpart', None)
        row.pop('_parse_error', None)
        rows.append(row)

    # Aggregate fills by timestamp (Bybit)
    if rules.get('aggregate_by_time') and rows:
        rows = _aggregate_by_time(rows)

    return rows

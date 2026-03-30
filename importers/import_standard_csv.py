"""
Generic CSV Importer for Standard Format Transactions
Works with any CSV file matching the standard column format.

Handles three currency scenarios:
  - EUR: direct import, no conversion needed
  - USD: converts to EUR via ECB historical rates
  - Crypto (BTC, BCH, etc.): crypto-to-crypto trade → imports BOTH sides
    (e.g. SELL BCH → also creates BUY BTC counterpart)

Source tracking:
  - DELETE by source (file-level) instead of exchange (exchange-level)
  - Records source filename, import timestamp, and record hash
"""

import pandas as pd
import sys
import os
from datetime import datetime
import pytz

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

try:
    from config import DATABASE_PATH
except ImportError:
    DATABASE_PATH = os.path.join(PROJECT_ROOT, 'data', 'crypto_fifo.db')

from importers.import_utils import compute_record_hash, import_and_verify
from importers.crypto_prices import CryptoPrices

DB_PATH = DATABASE_PATH

# Known fiat currencies — everything else is treated as crypto
FIAT_CURRENCIES = {'EUR', 'USD', 'GBP', 'CHF', 'JPY', 'CAD', 'AUD'}


def parse_numeric(value):
    """Parse numeric value, handling comma as thousands separator"""
    if pd.isna(value):
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    # Remove comma thousands separator, quotes
    value_str = str(value).replace(',', '').replace('"', '').strip()
    try:
        return float(value_str)
    except:
        return 0.0


def _load_ecb_rates():
    """Load ECB rates if available."""
    try:
        from importers.ecb_rates import ECBRates
        eurusd_path = os.path.join(PROJECT_ROOT, 'data', 'eurusd.csv')
        if os.path.exists(eurusd_path):
            return ECBRates(eurusd_path)
    except Exception as e:
        print(f"  ⚠️  Could not load ECB rates: {e}")
    return None


def _load_crypto_prices():
    """Load crypto prices if available."""
    try:
        prices_path = os.path.join(PROJECT_ROOT, 'data', 'crypto_prices.csv')
        if os.path.exists(prices_path):
            return CryptoPrices(prices_path)
    except Exception as e:
        print(f"  ⚠️  Could not load crypto prices: {e}")
    return None


def import_standard_csv(filepath, exchange_name_override=None):
    """
    Import transactions from a standard CSV file.

    CSV must have these columns:
    - transaction_date (ISO format with timezone)
    - transaction_type (BUY, SELL, DEPOSIT, WITHDRAWAL)
    - cryptocurrency (BTC, USDC, etc)
    - amount (quantity)
    - price_per_unit (per unit in `currency`)
    - total_value (total in `currency`, before fees)
    - fee_amount
    - fee_currency (EUR, USD, BTC, etc.)
    - currency (EUR, USD, BTC, BCH, etc.)
    - exchange_name (descriptive name)
    - transaction_id (unique ID)
    - notes (optional description)
    """

    source_filename = os.path.basename(filepath)
    import_timestamp = datetime.now().isoformat()

    print("="*80)
    print(f"IMPORTING STANDARD CSV: {filepath}")
    print(f"Source: {source_filename}")
    print("="*80)

    # Read CSV
    df = pd.read_csv(filepath)
    print(f"\nLoaded {len(df):,} rows")

    # Validate required columns
    required_cols = ['transaction_date', 'transaction_type', 'cryptocurrency',
                     'amount', 'total_value', 'exchange_name']
    missing = [col for col in required_cols if col not in df.columns]
    if missing:
        print(f"✗ Missing required columns: {missing}")
        return 0

    # Parse dates
    df['transaction_date'] = pd.to_datetime(df['transaction_date'])

    # Get exchange name
    if exchange_name_override:
        exchange_name = exchange_name_override
    else:
        exchange_name = df['exchange_name'].iloc[0] if len(df) > 0 else 'Unknown'

    print(f"Exchange: {exchange_name}")
    print(f"Date range: {df['transaction_date'].min()} to {df['transaction_date'].max()}")

    # Statistics
    crypto_counts = df['cryptocurrency'].value_counts()
    print(f"\nCryptocurrencies:")
    for crypto, count in crypto_counts.items():
        print(f"  {crypto}: {count} transactions")

    type_counts = df['transaction_type'].value_counts()
    print(f"\nTransaction types:")
    for tx_type, count in type_counts.items():
        print(f"  {tx_type}: {count}")

    # Check if we need ECB rates (any USD rows)
    ecb = None
    currencies = set()
    for _, row in df.iterrows():
        cur = str(row.get('currency', 'EUR')).strip().upper() if pd.notna(row.get('currency')) else 'EUR'
        currencies.add(cur)
    if 'USD' in currencies:
        ecb = _load_ecb_rates()
        if ecb:
            print(f"\n  ✓ ECB rates loaded for USD→EUR conversion")
        else:
            print(f"\n  ⚠️  USD transactions found but ECB rates not available!")

    # Detect crypto-to-crypto trades and load crypto prices
    crypto_currencies = currencies - FIAT_CURRENCIES
    crypto_prices = None
    if crypto_currencies:
        print(f"\n  ℹ️  Crypto-to-crypto trades detected (currency: {', '.join(crypto_currencies)})")
        print(f"      Will create counterpart transactions for both sides")
        crypto_prices = _load_crypto_prices()
        if crypto_prices:
            print(f"  ✓ Crypto prices loaded for EUR valuation")
        else:
            print(f"  ⚠️  Crypto prices not available — EUR values will be 0")

    # Insert function for import_and_verify
    def do_inserts(conn):
        cursor = conn.cursor()
        inserted = 0

        # Fallback: clean legacy records with NULL source for this exchange
        cursor.execute("DELETE FROM transactions WHERE exchange_name = ? AND source IS NULL",
                     (exchange_name,))
        legacy = cursor.rowcount
        if legacy > 0:
            print(f"\n  Deleted {legacy:,} legacy records (no source) for {exchange_name}")

        def _insert_tx(dt, tx_type, exch, crypto, amount, price, total, fee,
                       fee_cur, currency, tx_id, notes_str, suffix=''):
            """Insert a single transaction record."""
            nonlocal inserted
            hash_source = f"{source_filename}{suffix}"
            record_hash = compute_record_hash(
                hash_source, dt.isoformat(), tx_type,
                exch, crypto, amount, total, fee
            )
            cursor.execute("""
                INSERT INTO transactions (
                    transaction_date, transaction_type, exchange_name, cryptocurrency,
                    amount, price_per_unit, total_value, fee_amount, fee_currency,
                    currency, transaction_id, notes,
                    source, imported_at, record_hash
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                dt.isoformat(), tx_type, exch, crypto,
                amount, price, total, fee,
                fee_cur, currency,
                tx_id, notes_str,
                source_filename, import_timestamp, record_hash,
            ))
            inserted += 1

        for _, row in df.iterrows():
            # Parse date with timezone
            dt = row['transaction_date']
            if dt.tzinfo is None:
                dt = pytz.UTC.localize(dt)

            # Get values with defaults
            fee_amount = row.get('fee_amount', 0)
            fee_currency_raw = row.get('fee_currency', 'EUR')
            price_per_unit = row.get('price_per_unit', 0)
            notes = row.get('notes', '')
            transaction_id = row.get('transaction_id', f"{exchange_name}_{dt.isoformat()}")
            csv_currency = str(row.get('currency', 'EUR')).strip().upper() if pd.notna(row.get('currency')) else 'EUR'

            amount_val = parse_numeric(row['amount'])
            total_val = parse_numeric(row['total_value'])
            fee_val = parse_numeric(fee_amount) if pd.notna(fee_amount) else 0
            price_val = parse_numeric(price_per_unit) if pd.notna(price_per_unit) else 0

            fee_cur = str(fee_currency_raw).strip() if pd.notna(fee_currency_raw) else 'EUR'
            notes_str = notes if pd.notna(notes) else ''

            tx_type = str(row['transaction_type']).strip().upper()
            crypto = str(row['cryptocurrency']).strip()

            # Per-row exchange_name: use CSV column if present, fallback to global
            row_exch_val = str(row.get('exchange_name', '')).strip() if pd.notna(row.get('exchange_name')) else ''
            row_exchange = row_exch_val if row_exch_val else exchange_name

            # ── Case 1: EUR — direct import ──
            if csv_currency == 'EUR':
                _insert_tx(dt, tx_type, row_exchange, crypto,
                           amount_val, price_val, total_val, fee_val,
                           fee_cur, 'EUR', transaction_id, notes_str)

            # ── Case 2: USD — convert to EUR via ECB ──
            elif csv_currency == 'USD':
                if ecb:
                    total_eur = ecb.usd_to_eur(total_val, dt)
                    fee_eur = ecb.usd_to_eur(fee_val, dt) if fee_val > 0 else 0
                    price_eur = total_eur / amount_val if amount_val > 0 else 0
                    print(f"    USD→EUR: {crypto} {tx_type} ${total_val:.2f} → €{total_eur:.2f}")
                else:
                    # No ECB rates — store USD values with warning
                    total_eur = total_val
                    fee_eur = fee_val
                    price_eur = price_val
                    print(f"    ⚠️  No ECB rates: storing USD value as-is for {crypto} {tx_type}")

                _insert_tx(dt, tx_type, row_exchange, crypto,
                           amount_val, price_eur, total_eur, fee_eur,
                           'EUR', 'EUR', transaction_id,
                           f"{notes_str} [converted from USD]".strip())

            # ── Case 3: Crypto currency — crypto-to-crypto trade ──
            else:
                # The "currency" is another crypto (e.g. BTC, BCH)
                # This means: traded `cryptocurrency` for `currency`
                # We need to record BOTH sides:
                #   Side A: the original row (e.g. SELL BCH, amount=10)
                #   Side B: the counterpart (e.g. BUY BTC, amount=total_value)

                counter_crypto = csv_currency  # e.g. BTC
                counter_amount = total_val     # e.g. 0.604 BTC received
                counter_type = 'BUY' if tx_type == 'SELL' else 'SELL'

                # Compute EUR values using crypto prices
                side_a_eur = 0.0
                side_a_price = 0.0
                side_b_eur = 0.0
                side_b_price = 0.0
                eur_source = 'EUR value pending'

                if crypto_prices:
                    # Side A: EUR value of the crypto being traded
                    a_eur = crypto_prices.crypto_to_eur(crypto, amount_val, dt)
                    if a_eur is not None:
                        side_a_eur = a_eur
                        side_a_price = a_eur / amount_val if amount_val > 0 else 0
                        eur_source = 'EUR from CryptoCompare'

                    # Side B: EUR value of the counter crypto received
                    b_eur = crypto_prices.crypto_to_eur(counter_crypto, counter_amount, dt)
                    if b_eur is not None:
                        side_b_eur = b_eur
                        side_b_price = b_eur / counter_amount if counter_amount > 0 else 0

                        # If side A had no price, use side B value (same trade)
                        if side_a_eur == 0.0 and side_b_eur > 0:
                            side_a_eur = side_b_eur
                            side_a_price = side_a_eur / amount_val if amount_val > 0 else 0
                        # Vice versa
                        elif side_b_eur == 0.0 and side_a_eur > 0:
                            side_b_eur = side_a_eur
                            side_b_price = side_b_eur / counter_amount if counter_amount > 0 else 0

                # Side A: original transaction
                _insert_tx(dt, tx_type, row_exchange, crypto,
                           amount_val, side_a_price, side_a_eur, 0,
                           'EUR', 'EUR', transaction_id,
                           f"{notes_str} [crypto-to-crypto: {amount_val:.8f} {crypto} → {counter_amount:.8f} {counter_crypto}, {eur_source}]".strip(),
                           suffix='|sideA')

                # Side B: counterpart transaction
                # Fee goes on the counterpart (the crypto received) if fee is in counter_crypto
                fee_eur_counter = 0.0
                fee_note = ''
                if fee_val > 0 and fee_cur.upper() == counter_crypto:
                    if crypto_prices:
                        f_eur = crypto_prices.crypto_to_eur(counter_crypto, fee_val, dt)
                        if f_eur is not None:
                            fee_eur_counter = f_eur
                    fee_note = f", fee: {fee_val:.8f} {counter_crypto}"

                _insert_tx(dt, counter_type, row_exchange, counter_crypto,
                           counter_amount, side_b_price, side_b_eur, fee_eur_counter,
                           'EUR', 'EUR', f"{transaction_id}_counter",
                           f"Counterpart of {tx_type} {amount_val:.8f} {crypto}{fee_note} [{eur_source}]",
                           suffix='|sideB')

                eur_label = f"€{side_a_eur:,.2f}" if side_a_eur > 0 else "no EUR price"
                print(f"    Crypto-to-crypto: {tx_type} {amount_val:.8f} {crypto} → {counter_type} {counter_amount:.8f} {counter_crypto} ({eur_label})")

        return inserted

    return import_and_verify(DB_PATH, source_filename, do_inserts, group_by_crypto=True)

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 import_standard_csv.py <filepath> [exchange_name]")
        print("\nExample:")
        print("  python3 import_standard_csv.py data/otc_2017.csv OTC")
        sys.exit(1)

    filepath = sys.argv[1]
    exchange_name = sys.argv[2] if len(sys.argv) > 2 else None

    import_standard_csv(filepath, exchange_name)

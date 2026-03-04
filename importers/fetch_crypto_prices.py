#!/usr/bin/env python3
"""
Download historical daily crypto prices from CryptoCompare API.

Saves to data/crypto_prices.csv in format: date,coin,close_eur
Supports incremental updates (appends only new dates).

Usage:
    # Full download of all coins
    python3 importers/fetch_crypto_prices.py

    # Specific coins only
    python3 importers/fetch_crypto_prices.py BTC BCH

    # Force full re-download (ignore existing data)
    python3 importers/fetch_crypto_prices.py --full

API: CryptoCompare (CoinDesk Data) — free, no API key required.
Endpoint: https://min-api.cryptocompare.com/data/v2/histoday
"""

import csv
import os
import sys
import time
import requests
from datetime import datetime, timezone

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(PROJECT_ROOT, 'data')
OUTPUT_FILE = os.path.join(DATA_DIR, 'crypto_prices.csv')

API_URL = 'https://min-api.cryptocompare.com/data/v2/histoday'

# Coins to download by default — matches what's used in transactions DB
DEFAULT_COINS = ['BTC', 'BCH', 'ETH']


def fetch_all_history(coin, currency='EUR'):
    """Fetch complete daily history for a coin from CryptoCompare.

    Returns list of (date_str, close_price) tuples sorted by date.
    """
    params = {
        'fsym': coin,
        'tsym': currency,
        'allData': 'true',
    }
    print(f"  Fetching {coin}/{currency} from CryptoCompare...")
    resp = requests.get(API_URL, params=params, timeout=30)
    resp.raise_for_status()
    data = resp.json()

    if data.get('Response') != 'Success':
        print(f"    ✗ API error: {data.get('Message', 'unknown')}")
        return []

    entries = data.get('Data', {}).get('Data', [])
    results = []
    for entry in entries:
        ts = entry['time']
        close = entry['close']
        if close <= 0:
            continue
        dt = datetime.fromtimestamp(ts, tz=timezone.utc)
        date_str = dt.strftime('%Y-%m-%d')
        results.append((date_str, close))

    if results:
        print(f"    ✓ {len(results):,} daily prices: {results[0][0]} → {results[-1][0]}")
    else:
        print(f"    ✗ No data returned")

    return results


def load_existing(filepath):
    """Load existing CSV data into a dict of {(coin, date): close_eur}."""
    existing = {}
    if not os.path.exists(filepath):
        return existing
    with open(filepath, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            key = (row['coin'].strip().upper(), row['date'].strip())
            existing[key] = float(row['close_eur'])
    return existing


def save_csv(data, filepath):
    """Save complete price data to CSV, sorted by coin then date."""
    rows = sorted(data.items(), key=lambda x: (x[0][0], x[0][1]))
    with open(filepath, 'w', encoding='utf-8', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(['date', 'coin', 'close_eur'])
        for (coin, date), close in rows:
            writer.writerow([date, coin, f'{close:.2f}'])
    print(f"\n✓ Saved {len(rows):,} prices to {filepath}")


def main():
    args = [a for a in sys.argv[1:] if not a.startswith('-')]
    force_full = '--full' in sys.argv

    coins = [c.upper() for c in args] if args else DEFAULT_COINS

    print("=" * 60)
    print("CRYPTO PRICE DOWNLOAD (CryptoCompare)")
    print("=" * 60)
    print(f"Coins: {', '.join(coins)}")
    print(f"Output: {OUTPUT_FILE}")
    print(f"Mode: {'full re-download' if force_full else 'incremental update'}")
    print()

    # Load existing data (for incremental mode)
    if force_full:
        all_data = {}
    else:
        all_data = load_existing(OUTPUT_FILE)
        if all_data:
            existing_coins = sorted(set(c for c, _ in all_data.keys()))
            print(f"Existing data: {len(all_data):,} prices for {existing_coins}")
        else:
            print("No existing data found — doing full download")

    # Fetch each coin
    for coin in coins:
        time.sleep(0.5)  # polite rate limiting
        history = fetch_all_history(coin)

        new_count = 0
        for date_str, close in history:
            key = (coin, date_str)
            if key not in all_data:
                new_count += 1
            all_data[key] = close

        print(f"    Added {new_count:,} new prices for {coin}")

    # Save
    save_csv(all_data, OUTPUT_FILE)

    # Summary
    coins_in_file = sorted(set(c for c, _ in all_data.keys()))
    print(f"\nSummary:")
    for coin in coins_in_file:
        coin_data = [(d, p) for (c, d), p in all_data.items() if c == coin]
        coin_data.sort()
        if coin_data:
            print(f"  {coin}: {len(coin_data):,} days, {coin_data[0][0]} → {coin_data[-1][0]}")
            print(f"         latest price: €{coin_data[-1][1]:,.2f}")

    print("\n" + "=" * 60)


if __name__ == '__main__':
    main()

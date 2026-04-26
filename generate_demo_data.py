#!/usr/bin/env python3
"""
Generate realistic demo CSV data for the Crypto FIFO Tracker.

Creates 3 CSV files with ~300 records each:
  - DEMO_alpha_buys.csv:  300 BUY  (2016-2019, early accumulation)
  - DEMO_beta_buys.csv:   300 BUY  (2019-2025, DCA strategy)
  - DEMO_gamma_sells.csv: 300 SELL (2023-2025, profit-taking)

BTC/EUR prices follow a realistic historical curve with daily noise.
All transactions use the standard CSV format for import_standard_csv.py.
"""

import csv
import os
import random
from datetime import datetime, timedelta

random.seed(42)  # Reproducible

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'demo')

# ── Realistic BTC/EUR monthly price anchors ──────────────────
# (year, month) → approximate EUR price
PRICE_ANCHORS = {
    (2016, 1): 380,   (2016, 4): 400,   (2016, 7): 580,   (2016, 10): 600,
    (2016, 12): 870,
    (2017, 1): 900,   (2017, 3): 1050,  (2017, 5): 1800,  (2017, 7): 2100,
    (2017, 9): 3400,  (2017, 11): 7000,  (2017, 12): 13000,
    (2018, 1): 11000, (2018, 3): 6500,  (2018, 6): 5500,  (2018, 9): 5700,
    (2018, 12): 3200,
    (2019, 1): 3100,  (2019, 4): 4500,  (2019, 6): 8500,  (2019, 9): 7300,
    (2019, 12): 6500,
    (2020, 1): 7500,  (2020, 3): 5200,  (2020, 6): 8400,  (2020, 9): 9500,
    (2020, 12): 22000,
    (2021, 1): 27000, (2021, 3): 50000, (2021, 5): 32000, (2021, 7): 28000,
    (2021, 9): 38000, (2021, 11): 53000, (2021, 12): 42000,
    (2022, 1): 34000, (2022, 3): 36000, (2022, 5): 26000, (2022, 7): 19000,
    (2022, 9): 18500, (2022, 12): 15500,
    (2023, 1): 19500, (2023, 3): 25000, (2023, 6): 27000, (2023, 9): 24000,
    (2023, 11): 34000, (2023, 12): 38000,
    (2024, 1): 40000, (2024, 3): 60000, (2024, 5): 58000, (2024, 7): 55000,
    (2024, 9): 52000, (2024, 11): 82000, (2024, 12): 85000,
    (2025, 1): 88000, (2025, 3): 78000, (2025, 6): 82000, (2025, 9): 90000,
    (2025, 12): 92000,
}


def get_price(dt):
    """Interpolate BTC/EUR price for a date, with ±5% daily noise."""
    year, month = dt.year, dt.month

    # Find surrounding anchors
    keys = sorted(PRICE_ANCHORS.keys())
    key = (year, month)

    # Find lower and upper bounds
    lower = keys[0]
    upper = keys[-1]
    for k in keys:
        if k <= key:
            lower = k
        if k >= key:
            upper = k
            break

    if lower == upper:
        base = PRICE_ANCHORS[lower]
    else:
        p1 = PRICE_ANCHORS[lower]
        p2 = PRICE_ANCHORS[upper]
        # Linear interpolation between anchors
        d1 = datetime(lower[0], lower[1], 15)
        d2 = datetime(upper[0], upper[1], 15)
        total_days = (d2 - d1).days
        elapsed = (dt - d1).days
        frac = max(0, min(1, elapsed / total_days)) if total_days > 0 else 0
        base = p1 + (p2 - p1) * frac

    # Add daily noise ±5%
    noise = random.uniform(-0.05, 0.05)
    return round(base * (1 + noise), 2)


def generate_dates(start, end, count):
    """Generate evenly-spaced dates with slight jitter."""
    total_days = (end - start).days
    step = total_days / count
    dates = []
    for i in range(count):
        base = start + timedelta(days=step * i)
        jitter = timedelta(hours=random.randint(0, 23), minutes=random.randint(0, 59))
        dates.append(base + jitter)
    return sorted(dates)


def write_csv(filepath, rows):
    """Write standard-format CSV."""
    fieldnames = [
        'transaction_date', 'transaction_type', 'cryptocurrency', 'amount',
        'price_per_unit', 'total_value', 'fee_amount', 'fee_currency',
        'currency', 'exchange_name', 'transaction_id', 'notes',
    ]
    with open(filepath, 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    print(f"  ✓ {os.path.basename(filepath)}: {len(rows)} records")


def generate_buys(dates, exchange_name, prefix, amount_range, notes_pool):
    """Generate BUY records for given dates."""
    rows = []
    for i, dt in enumerate(dates):
        price = get_price(dt)
        amount = round(random.uniform(*amount_range), 6)
        total = round(amount * price, 2)
        fee = round(total * random.uniform(0.0004, 0.0015), 2)  # 0.04-0.15% fee
        rows.append({
            'transaction_date': dt.strftime('%Y-%m-%dT%H:%M:%S+00:00'),
            'transaction_type': 'BUY',
            'cryptocurrency': 'BTC',
            'amount': amount,
            'price_per_unit': price,
            'total_value': total,
            'fee_amount': fee,
            'fee_currency': 'EUR',
            'currency': 'EUR',
            'exchange_name': exchange_name,
            'transaction_id': f'{prefix}-{i+1:04d}',
            'notes': random.choice(notes_pool),
        })
    return rows


def generate_sells(dates, exchange_name, prefix, amount_range, notes_pool):
    """Generate SELL records for given dates."""
    rows = []
    for i, dt in enumerate(dates):
        price = get_price(dt)
        amount = round(random.uniform(*amount_range), 6)
        total = round(amount * price, 2)
        fee = round(total * random.uniform(0.0005, 0.002), 2)  # 0.05-0.2% fee
        rows.append({
            'transaction_date': dt.strftime('%Y-%m-%dT%H:%M:%S+00:00'),
            'transaction_type': 'SELL',
            'cryptocurrency': 'BTC',
            'amount': amount,
            'price_per_unit': price,
            'total_value': total,
            'fee_amount': fee,
            'fee_currency': 'EUR',
            'currency': 'EUR',
            'exchange_name': exchange_name,
            'transaction_id': f'{prefix}-{i+1:04d}',
            'notes': random.choice(notes_pool),
        })
    return rows


def main():
    print("Generating demo CSV data...")

    # ── Alpha: 300 BUYs, 2016-01 to 2019-06 ──
    alpha_dates = generate_dates(
        datetime(2016, 1, 15), datetime(2019, 6, 30), 300
    )
    alpha_notes = [
        'Weekly DCA purchase', 'Regular buy', 'Accumulation',
        'Dollar cost averaging', 'Recurring buy order',
    ]
    alpha_rows = generate_buys(
        alpha_dates, 'DEMO Alpha', 'DA',
        amount_range=(0.002, 0.025),
        notes_pool=alpha_notes,
    )

    # ── Beta: 300 BUYs, 2019-07 to 2025-09 ──
    beta_dates = generate_dates(
        datetime(2019, 7, 1), datetime(2025, 9, 30), 300
    )
    beta_notes = [
        'DCA strategy', 'Monthly purchase', 'Dip buy',
        'Regular investment', 'Portfolio rebalance',
    ]
    beta_rows = generate_buys(
        beta_dates, 'DEMO Beta', 'DB',
        amount_range=(0.001, 0.012),
        notes_pool=beta_notes,
    )

    # ── Calculate total BTC bought for sell budget ──
    total_bought = sum(r['amount'] for r in alpha_rows + beta_rows)
    # Sell ~92% of total — pushes FIFO into recent Beta lots,
    # producing a mix of long-term (exempt) and short-term (taxable) matches
    sell_budget = total_bought * 0.92

    # ── Gamma: 300 SELLs, 2023-01 to 2025-06 ──
    gamma_dates = generate_dates(
        datetime(2023, 1, 10), datetime(2025, 6, 15), 300
    )
    gamma_notes = [
        'Profit taking', 'Partial exit', 'Tax-loss harvest',
        'Portfolio rebalance', 'Scheduled sell',
    ]
    # Distribute sell_budget across 300 transactions
    avg_sell = sell_budget / 300
    gamma_rows = []
    remaining_budget = sell_budget
    for i, dt in enumerate(gamma_dates):
        price = get_price(dt)
        # Vary amount around average, ensure we don't oversell
        target = avg_sell * random.uniform(0.3, 1.8)
        amount = min(round(target, 6), remaining_budget)
        if amount <= 0:
            amount = 0.000001
        remaining_budget -= amount
        total = round(amount * price, 2)
        fee = round(total * random.uniform(0.0005, 0.002), 2)
        gamma_rows.append({
            'transaction_date': dt.strftime('%Y-%m-%dT%H:%M:%S+00:00'),
            'transaction_type': 'SELL',
            'cryptocurrency': 'BTC',
            'amount': round(amount, 6),
            'price_per_unit': price,
            'total_value': total,
            'fee_amount': fee,
            'fee_currency': 'EUR',
            'currency': 'EUR',
            'exchange_name': 'DEMO Gamma',
            'transaction_id': f'DG-{i+1:04d}',
            'notes': random.choice(gamma_notes),
        })

    # Write CSV files
    write_csv(os.path.join(DATA_DIR, 'DEMO_alpha_buys.csv'), alpha_rows)
    write_csv(os.path.join(DATA_DIR, 'DEMO_beta_buys.csv'), beta_rows)
    write_csv(os.path.join(DATA_DIR, 'DEMO_gamma_sells.csv'), gamma_rows)

    # Summary
    alpha_btc = sum(r['amount'] for r in alpha_rows)
    alpha_eur = sum(r['total_value'] for r in alpha_rows)
    beta_btc = sum(r['amount'] for r in beta_rows)
    beta_eur = sum(r['total_value'] for r in beta_rows)
    gamma_btc = sum(r['amount'] for r in gamma_rows)
    gamma_eur = sum(r['total_value'] for r in gamma_rows)

    print(f"\n  Alpha (2016-2019): {len(alpha_rows)} BUYs, {alpha_btc:.6f} BTC, €{alpha_eur:,.2f}")
    print(f"  Beta  (2019-2025): {len(beta_rows)} BUYs, {beta_btc:.6f} BTC, €{beta_eur:,.2f}")
    print(f"  Gamma (2023-2025): {len(gamma_rows)} SELLs, {gamma_btc:.6f} BTC, €{gamma_eur:,.2f}")
    print(f"\n  Total bought: {alpha_btc + beta_btc:.6f} BTC (€{alpha_eur + beta_eur:,.2f})")
    print(f"  Total sold:   {gamma_btc:.6f} BTC (€{gamma_eur:,.2f})")
    print(f"  Remaining:    {alpha_btc + beta_btc - gamma_btc:.6f} BTC")


if __name__ == '__main__':
    main()

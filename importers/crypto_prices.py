"""
Historical Crypto Price Lookup

Loads daily closing prices from data/crypto_prices.csv (downloaded from CryptoCompare).
Follows the same pattern as ecb_rates.py for EUR/USD rates.

Usage:
    from importers.crypto_prices import CryptoPrices
    prices = CryptoPrices('data/crypto_prices.csv')
    eur_value = prices.get_eur_price('BTC', '2017-10-25')  # → 4876.32
    eur_total = prices.crypto_to_eur('BTC', 0.5, '2017-10-25')  # → 2438.16
"""

import csv
from datetime import datetime, timedelta


class CryptoPrices:
    def __init__(self, csv_path='data/crypto_prices.csv'):
        """Load crypto prices from CSV.

        Expected CSV format:
            date,coin,close_eur
            2011-08-27,BTC,6.14
            ...
        """
        self._prices = {}  # {(coin, 'YYYY-MM-DD'): close_eur}
        self._coins = set()
        self._min_date = None
        self._max_date = None
        count = 0

        with open(csv_path, 'r', encoding='utf-8-sig') as f:
            reader = csv.DictReader(f)
            for row in reader:
                coin = row['coin'].strip().upper()
                date_str = row['date'].strip()
                try:
                    close = float(row['close_eur'])
                except (ValueError, TypeError):
                    continue
                if close <= 0:
                    continue
                self._prices[(coin, date_str)] = close
                self._coins.add(coin)
                if self._min_date is None or date_str < self._min_date:
                    self._min_date = date_str
                if self._max_date is None or date_str > self._max_date:
                    self._max_date = date_str
                count += 1

        print(f"✓ Loaded {count:,} crypto prices for {sorted(self._coins)} "
              f"from {self._min_date} to {self._max_date}")

    @property
    def coins(self):
        return sorted(self._coins)

    @property
    def date_range(self):
        return (self._min_date, self._max_date)

    def get_eur_price(self, coin, date):
        """Get closing EUR price for a coin on a specific date.

        Falls back to previous days (up to 5) for weekends/holidays.

        Args:
            coin: e.g. 'BTC', 'BCH', 'ETH'
            date: datetime, date, or string 'YYYY-MM-DD'

        Returns:
            float or None: EUR price, or None if not available
        """
        coin = coin.strip().upper()
        if isinstance(date, str):
            d = datetime.strptime(date[:10], '%Y-%m-%d').date()
        elif hasattr(date, 'date'):
            d = date.date()
        else:
            d = date

        # Exact match
        key = (coin, d.strftime('%Y-%m-%d'))
        if key in self._prices:
            return self._prices[key]

        # Try previous days (weekends, exchange holidays)
        for days_back in range(1, 6):
            prev = d - timedelta(days=days_back)
            key = (coin, prev.strftime('%Y-%m-%d'))
            if key in self._prices:
                return self._prices[key]

        return None

    def crypto_to_eur(self, coin, amount, date):
        """Convert a crypto amount to EUR using daily closing price.

        Args:
            coin: e.g. 'BTC', 'BCH'
            amount: crypto amount (e.g. 0.5 BTC)
            date: transaction date

        Returns:
            float or None: EUR value, or None if price not available
        """
        price = self.get_eur_price(coin, date)
        if price is None:
            return None
        return abs(amount) * price

    def has_coin(self, coin):
        return coin.strip().upper() in self._coins


if __name__ == '__main__':
    prices = CryptoPrices()

    test_cases = [
        ('BTC', '2011-09-27'),
        ('BTC', '2017-10-25'),
        ('BTC', '2024-06-24'),
        ('BCH', '2017-10-25'),
    ]

    print("\n" + "=" * 60)
    print("CRYPTO PRICE TEST")
    print("=" * 60)

    for coin, date in test_cases:
        price = prices.get_eur_price(coin, date)
        if price is not None:
            print(f"\n  {coin} on {date}: €{price:,.2f}")
            print(f"    10 {coin} = €{prices.crypto_to_eur(coin, 10, date):,.2f}")
        else:
            print(f"\n  {coin} on {date}: price not available")

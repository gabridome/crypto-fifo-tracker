"""
ECB Exchange Rate Lookup
Converts USD to EUR using historical ECB rates.

Rates are fetched automatically from the Frankfurter API (https://api.frankfurter.app),
which mirrors official ECB data. The CSV file acts as a local cache — no manual download needed.

Legacy CSV files downloaded from the ECB website are fully compatible.
"""

import os
import csv
import pandas as pd
from datetime import datetime, timedelta, date as date_type

FRANKFURTER_API = 'https://api.frankfurter.app'
FETCH_START = '1999-01-04'  # EUR introduction


class ECBRates:
    def __init__(self, csv_path='data/eurusd.csv', auto_fetch=True):
        """Load ECB exchange rates, fetching from Frankfurter API if needed.

        Args:
            csv_path: Path to the CSV cache file.
            auto_fetch: If True, fetch missing/stale rates from Frankfurter API.
        """
        self.csv_path = csv_path
        self._warn_count = 0

        if os.path.exists(csv_path):
            self._load_csv()
            if auto_fetch and self._is_stale():
                self._update_from_api()
        elif auto_fetch:
            self._full_fetch_from_api()
        else:
            raise FileNotFoundError(f"eurusd.csv not found: {csv_path}")

    def _load_csv(self):
        """Load rates from CSV into a pandas DataFrame."""
        self.df = pd.read_csv(self.csv_path)
        self.df['DATE'] = pd.to_datetime(self.df['DATE'])
        self.df['rate'] = pd.to_numeric(
            self.df['US dollar/Euro (EXR.D.USD.EUR.SP00.A)'])
        self.df = self.df.sort_values('DATE').reset_index(drop=True)
        print(f"✓ Loaded {len(self.df):,} ECB rates"
              f" from {self.df['DATE'].min().date()} to {self.df['DATE'].max().date()}")

    def _is_stale(self):
        """Check if latest rate is older than last business day."""
        if self.df.empty:
            return True
        latest = self.df['DATE'].max().date()
        today = date_type.today()
        # Find last business day
        check = today - timedelta(days=1)
        while check.weekday() >= 5:  # skip weekends
            check -= timedelta(days=1)
        return latest < check

    def _fetch_frankfurter(self, start, end):
        """Fetch rates from Frankfurter API. Returns dict {date_str: rate}.

        Frankfurter returns weekly data for ranges > 1 year,
        so we fetch year by year to get daily granularity.
        """
        import requests

        all_rates = {}
        # Split into yearly chunks
        s = datetime.strptime(start, '%Y-%m-%d').date()
        e = datetime.strptime(end, '%Y-%m-%d').date()

        while s <= e:
            chunk_end = min(date_type(s.year, 12, 31), e)
            url = f"{FRANKFURTER_API}/{s}..{chunk_end}"
            try:
                resp = requests.get(
                    url, params={'from': 'EUR', 'to': 'USD'}, timeout=30)
                resp.raise_for_status()
                data = resp.json()
                rates = data.get('rates', {})
                for d, v in rates.items():
                    all_rates[d] = v['USD']
                print(f"  Frankfurter API: {s.year}"
                      f" → {len(rates)} rates")
            except Exception as ex:
                print(f"  ⚠ Frankfurter API error for {s.year}: {ex}")
                raise
            s = date_type(s.year + 1, 1, 1)

        return all_rates

    def _save_csv(self, rates_dict):
        """Save rates to CSV in ECB format, merging with existing data."""
        # Load existing rates if file exists
        existing = {}
        if os.path.exists(self.csv_path):
            try:
                with open(self.csv_path, 'r', encoding='utf-8-sig') as f:
                    reader = csv.DictReader(f)
                    for row in reader:
                        existing[row['DATE']] = row[
                            'US dollar/Euro (EXR.D.USD.EUR.SP00.A)']
            except Exception:
                pass

        # Merge: new rates override existing
        for d, rate in rates_dict.items():
            existing[d] = f"{rate:.4f}"

        # Write sorted
        os.makedirs(os.path.dirname(os.path.abspath(self.csv_path)),
                     exist_ok=True)
        with open(self.csv_path, 'w', newline='') as f:
            f.write(
                '"DATE","TIME PERIOD",'
                '"US dollar/Euro (EXR.D.USD.EUR.SP00.A)"\n')
            for d in sorted(existing.keys()):
                dt = datetime.strptime(d, '%Y-%m-%d')
                time_period = dt.strftime('%d %b %Y')
                f.write(f'"{d}","{time_period}","{existing[d]}"\n')

        print(f"  ✓ Saved {len(existing):,} rates to {self.csv_path}")

    def _update_from_api(self):
        """Incremental update: fetch only new dates since last in CSV."""
        latest = self.df['DATE'].max().date()
        start = (latest + timedelta(days=1)).strftime('%Y-%m-%d')
        end = date_type.today().strftime('%Y-%m-%d')
        print(f"  Updating ECB rates: {start} → {end}...")
        try:
            new_rates = self._fetch_frankfurter(start, end)
            if new_rates:
                self._save_csv(new_rates)
                self._load_csv()
        except Exception as ex:
            print(f"  ⚠ Could not update rates (using cached data): {ex}")

    def _full_fetch_from_api(self):
        """Full download from 1999 to today, save to CSV, then load."""
        end = date_type.today().strftime('%Y-%m-%d')
        print(f"  Downloading ECB rates from Frankfurter API"
              f" ({FETCH_START} → {end})...")
        try:
            rates = self._fetch_frankfurter(FETCH_START, end)
            self._save_csv(rates)
            self._load_csv()
        except Exception as ex:
            raise RuntimeError(
                f"Cannot fetch ECB rates and no local cache exists: {ex}"
            ) from ex

    def get_rate(self, date):
        """
        Get USD/EUR rate for a specific date.
        If date is weekend/holiday, use previous business day.

        Args:
            date: datetime or string 'YYYY-MM-DD'

        Returns:
            float: USD/EUR exchange rate
        """
        if isinstance(date, str):
            date = pd.to_datetime(date)

        # Normalize to pandas Timestamp
        date = pd.Timestamp(date.date())

        # Find exact match
        exact = self.df[self.df['DATE'] == date]
        if not exact.empty:
            return float(exact.iloc[0]['rate'])

        # Find previous business day (up to 4 days back)
        for days_back in range(1, 5):
            prev_date = date - timedelta(days=days_back)
            prev = self.df[self.df['DATE'] == prev_date]
            if not prev.empty:
                rate = float(prev.iloc[0]['rate'])
                if days_back > 2:
                    print(f"  ⚠️  ECB rate for {date.date()}:"
                          f" using {prev_date.date()}"
                          f" (-{days_back}d) = {rate:.4f}")
                return rate

        # Fallback beyond 4 days
        idx = self.df['DATE'].searchsorted(date)
        if idx > 0:
            fallback_date = self.df.iloc[idx - 1]['DATE']
            rate = float(self.df.iloc[idx - 1]['rate'])
            days_gap = (date - fallback_date).days
            print(f"  ⚠️  ECB rate for {date.date()}:"
                  f" nearest is {fallback_date.date()}"
                  f" (-{days_gap}d) = {rate:.4f}")
            self._warn_count += 1
            return rate
        else:
            rate = float(self.df.iloc[0]['rate'])
            print(f"  ⚠️  ECB rate for {date.date()}:"
                  f" before earliest, using"
                  f" {self.df.iloc[0]['DATE'].date()} = {rate:.4f}")
            self._warn_count += 1
            return rate

    def usd_to_eur(self, usd_amount, date):
        """Convert USD to EUR using ECB rate for the given date."""
        rate = self.get_rate(date)
        return usd_amount / rate

    def eur_to_usd(self, eur_amount, date):
        """Convert EUR to USD using ECB rate for the given date."""
        rate = self.get_rate(date)
        return eur_amount * rate

    def print_summary(self):
        """Print summary of rate lookups, including any warnings."""
        if self._warn_count > 0:
            print(f"\n  ⚠️  ATTENZIONE: {self._warn_count} transazioni"
                  f" hanno usato tassi ECB approssimati!")
            print(f"     Copertura file:"
                  f" {self.df['DATE'].min().date()}"
                  f" → {self.df['DATE'].max().date()}")
        else:
            print(f"\n  ✓ Tutti i tassi ECB trovati correttamente")


# CLI: test rates or force refresh
if __name__ == '__main__':
    import sys

    if '--fetch' in sys.argv:
        # Force full re-download
        path = 'data/eurusd.csv'
        for i, arg in enumerate(sys.argv):
            if arg == '--path' and i + 1 < len(sys.argv):
                path = sys.argv[i + 1]
        print("Force-fetching all ECB rates...")
        ecb = ECBRates(path, auto_fetch=True)
        print(f"\nDone. {len(ecb.df):,} rates in {path}")
    else:
        ecb = ECBRates()

        test_dates = [
            '2014-05-21',   # Bitfinex era
            '2024-09-18',   # Recent
            '2025-03-14',   # Very recent
        ]

        print("\n" + "=" * 70)
        print("ECB RATE TEST")
        print("=" * 70)

        for date_str in test_dates:
            rate = ecb.get_rate(date_str)
            print(f"\n{date_str}:")
            print(f"  Rate: 1 EUR = ${rate:.4f} USD")
            print(f"  $1,000 = €{ecb.usd_to_eur(1000, date_str):.2f}")
            print(f"  €1,000 = ${ecb.eur_to_usd(1000, date_str):.2f}")

        print("\n" + "=" * 70)

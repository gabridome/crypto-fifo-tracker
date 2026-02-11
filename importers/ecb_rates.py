"""
ECB Exchange Rate Lookup
Converts USD to EUR using historical ECB rates.

At the moment, the rates are available at https://data.ecb.europa.eu/data/datasets/EXR/EXR.D.USD.EUR.SP00.A
Click on the download symbol next to the chart and select ".csv".
"""

import pandas as pd
from datetime import datetime, timedelta

class ECBRates:
    def __init__(self, csv_path='data/eurusd.csv'):
        """Load ECB exchange rates from CSV"""
        self.df = pd.read_csv(csv_path)
        self.df['DATE'] = pd.to_datetime(self.df['DATE'])
        self.df['rate'] = pd.to_numeric(self.df['US dollar/Euro (EXR.D.USD.EUR.SP00.A)'])
        self.df = self.df.sort_values('DATE')
        print(f"✓ Loaded {len(self.df):,} ECB rates from {self.df['DATE'].min()} to {self.df['DATE'].max()}")
    
    def get_rate(self, date):
        """
        Get USD/EUR rate for a specific date
        If date is weekend/holiday, use previous business day
        
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
        
        # Find previous business day (up to 4 days back — covers weekends/holidays)
        for days_back in range(1, 5):
            prev_date = date - timedelta(days=days_back)
            prev = self.df[self.df['DATE'] == prev_date]
            if not prev.empty:
                rate = float(prev.iloc[0]['rate'])
                # Only warn if > 2 days back (weekends are normal)
                if days_back > 2:
                    print(f"  ⚠️  ECB rate for {date.date()}: using {prev_date.date()} (-{days_back}d) = {rate:.4f}")
                return rate
        
        # Fallback beyond 4 days — likely missing data, always warn
        idx = self.df['DATE'].searchsorted(date)
        if idx > 0:
            fallback_date = self.df.iloc[idx-1]['DATE']
            rate = float(self.df.iloc[idx-1]['rate'])
            days_gap = (date - fallback_date).days
            print(f"  ⚠️  ECB rate for {date.date()}: nearest rate is {fallback_date.date()} (-{days_gap}d) = {rate:.4f} — AGGIORNA eurusd.csv!")
            self._warn_count = getattr(self, '_warn_count', 0) + 1
            return rate
        else:
            rate = float(self.df.iloc[0]['rate'])
            print(f"  ⚠️  ECB rate for {date.date()}: date before earliest rate, using {self.df.iloc[0]['DATE'].date()} = {rate:.4f}")
            self._warn_count = getattr(self, '_warn_count', 0) + 1
            return rate
    
    def usd_to_eur(self, usd_amount, date):
        """
        Convert USD to EUR
        
        Args:
            usd_amount: Amount in USD
            date: Transaction date
        
        Returns:
            float: Amount in EUR
        """
        rate = self.get_rate(date)
        return usd_amount / rate
    
    def eur_to_usd(self, eur_amount, date):
        """
        Convert EUR to USD
        
        Args:
            eur_amount: Amount in EUR
            date: Transaction date
        
        Returns:
            float: Amount in USD
        """
        rate = self.get_rate(date)
        return eur_amount * rate

    def print_summary(self):
        """Print summary of rate lookups, including any warnings"""
        warn_count = getattr(self, '_warn_count', 0)
        if warn_count > 0:
            print(f"\n  ⚠️  ATTENZIONE: {warn_count} transazioni hanno usato tassi ECB approssimati!")
            print(f"     Aggiorna data/eurusd.csv e reimporta per tassi corretti.")
            print(f"     Copertura file: {self.df['DATE'].min().date()} → {self.df['DATE'].max().date()}")
        else:
            print(f"\n  ✓ Tutti i tassi ECB trovati correttamente")


# Test if run directly
if __name__ == '__main__':
    import sys
    
    ecb = ECBRates()
    
    # Test dates
    test_dates = [
        '2014-05-21',  # Bitfinex era
        '2024-09-18',  # Inheritance
        '2025-07-28',  # Coinbase Prime
    ]
    
    print("\n" + "="*70)
    print("ECB RATE TEST")
    print("="*70)
    
    for date_str in test_dates:
        rate = ecb.get_rate(date_str)
        print(f"\n{date_str}:")
        print(f"  Rate: 1 EUR = ${rate:.4f} USD")
        print(f"  $1,000 = €{ecb.usd_to_eur(1000, date_str):.2f}")
        print(f"  €1,000 = ${ecb.eur_to_usd(1000, date_str):.2f}")
    
    print("\n" + "="*70)

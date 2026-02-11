# Guide — Crypto-to-Crypto Transactions

How to handle BTC→ETH swaps, stablecoin conversions, and trades between different cryptocurrencies.

---

## Fundamental tax rule

**Every crypto-to-crypto swap is a taxable event** in most jurisdictions, including Portugal.

When you trade BTC for ETH:
1. You are **selling** BTC at its EUR market value at that moment
2. You are **buying** ETH for the same EUR value
3. You must calculate **gain/loss** on the BTC sold (via FIFO)
4. The ETH has a **new cost basis** equal to its EUR value at the time of purchase

---

## How it works in the system

A crypto-to-crypto swap is broken down into two transactions in the `transactions` table:

| Operation | transaction_type | Example |
|-----------|-----------------|---------|
| Sale of the crypto given away | SELL | SELL X BTC at €YY per unit |
| Purchase of the crypto received | BUY | BUY X ETH at €YY per unit |

Both transactions have the same `transaction_date` and equivalent EUR values (minus fees).

---

## Determining the EUR value

For the swap, you need the EUR price of both cryptos at the time of the trade.

**Price sources (in order of preference):**
1. If the exchange provides the value in EUR: use that directly
2. If the exchange provides the value in USD: convert via `ecb_rates.py` with the ECB rate for that day
3. If not available: use a historical price API

**Note on CoinGecko**: the free (Demo) plan only provides up to 365 days of historical data. For older transactions, use CryptoCompare instead — it offers full daily history back to the coin's origin on the free tier.

```python
# Option 1: CryptoCompare (recommended — full history, free tier)
# Requires free API key from https://www.cryptocompare.com/cryptopian/api-keys
import requests, time

CRYPTOCOMPARE_API_KEY = "YOUR_KEY"

def get_price_cryptocompare(coin, currency, date):
    """Get historical daily price. date is a datetime object."""
    timestamp = int(date.timestamp())
    url = "https://min-api.cryptocompare.com/data/v2/histoday"
    params = {"fsym": coin, "tsym": currency, "limit": 1, "toTs": timestamp}
    headers = {"authorization": f"Apikey {CRYPTOCOMPARE_API_KEY}"}
    response = requests.get(url, headers=headers, params=params)
    data = response.json()["Data"]["Data"]
    return data[-1]["close"]  # closing price for that day

# Usage:
from datetime import datetime
price_eur = get_price_cryptocompare("BTC", "EUR", datetime(2014, 5, 21))
print(f"BTC price: €{price_eur}")
time.sleep(0.5)  # respect rate limits


# Option 2: CoinGecko (only for dates within the last 365 days)
import requests

date_str = "15-03-2025"  # dd-mm-yyyy format for CoinGecko
url = f"https://api.coingecko.com/api/v3/coins/bitcoin/history?date={date_str}"
response = requests.get(url)
price_eur = response.json()['market_data']['current_price']['eur']
```

---

## Manual import

For a small number of crypto-to-crypto transactions, create a standard CSV and use `import_standard_csv.py`:

### CSV format

```csv
transaction_date,transaction_type,cryptocurrency,amount,price_per_unit,total_value,fee_amount,fee_currency,currency,exchange_name,transaction_id,notes
2024-03-15T14:30:00+00:00,SELL,BTC,0.5,60000,30000,15,EUR,EUR,Binance,c2c_001_sell,Swap BTC→ETH
2024-03-15T14:30:00+00:00,BUY,ETH,8.0,3750,30000,15,EUR,EUR,Binance,c2c_001_buy,Swap BTC→ETH
```

**Rules:**
- Same date for both transactions
- Equal `total_value` (EUR value of the swap, before fees)
- Fees can be split between both transactions or placed on just one
- Use `notes` to link the two transactions

### Import

```bash
python3 importers/import_standard_csv.py data/crypto_to_crypto.csv "Binance"
```

---

## Common scenarios

### Swap on centralised exchange (Binance Convert, Coinbase swap)

Binance and Coinbase have "Convert" or direct swap operations. Check the exchange CSV:
- Binance: look for "Convert", "Swap Farming", "Small assets exchange BNB"
- Coinbase: look for "Convert" in the transaction type

These must be broken down into SELL + BUY as described above.

### Stablecoin conversion (BTC→USDT, ETH→USDC)

Same logic: converting to a stablecoin is a sale of the original crypto. The USDT/USDC received has cost basis = EUR value at the time of conversion.

### Dust conversion (small balances → BNB)

Binance allows converting small residual balances to BNB. Technically taxable, but typically de minimis amounts. Still worth recording for completeness.

### Wrapped tokens (ETH→WETH)

ETH→WETH may not be a taxable event (same underlying asset). Check with your tax advisor and document the decision.

---

## Impact on FIFO

After importing crypto-to-crypto transactions, FIFO processes them automatically:

1. The BTC **SELL** consumes FIFO lots (from the oldest)
2. The ETH **BUY** creates a new FIFO lot
3. The gain/loss on the SELL is calculated normally
4. The holding period is that of the consumed BTC lot, not the duration of the swap

If the BTC sold in the swap came from a purchase more than 365 days earlier, the gain is **exempt** in Portugal — even if immediately converted to ETH.

---

## Calculation and reporting

After importing crypto-to-crypto transactions:

```bash
# Recalculate FIFO for all cryptos
python3 calculators/calculate_fifo.py

# Generate report — includes all cryptos with sales in the year
python3 calculators/generate_irs_report.py YYYY
```

The IRS report automatically includes all cryptocurrencies (BTC, ETH, USDC, etc.) with sale operations in the year.

---

## Verification

### Check imported crypto-to-crypto transactions

```sql
SELECT 
    transaction_date,
    transaction_type,
    cryptocurrency,
    amount,
    price_per_unit,
    total_value,
    exchange_name,
    notes
FROM transactions
WHERE notes LIKE '%Swap%' OR notes LIKE '%Convert%' OR notes LIKE '%c2c%'
ORDER BY transaction_date;
```

### Verify the resulting gain/loss

```sql
SELECT 
    slm.cryptocurrency,
    COUNT(*) as operations,
    SUM(slm.gain_loss) as total_gain_loss,
    SUM(CASE WHEN slm.holding_period_days >= 365 THEN slm.gain_loss ELSE 0 END) as exempt,
    SUM(CASE WHEN slm.holding_period_days < 365 THEN slm.gain_loss ELSE 0 END) as taxable
FROM sale_lot_matches slm
WHERE strftime('%Y', slm.sale_date) = ?
GROUP BY slm.cryptocurrency;
```

---

## Portuguese tax specifics

- **Every crypto-to-crypto swap is taxable** — not just conversion to EUR
- **Holding period is calculated on the asset sold** — not the one received
- **The ≥365 day exemption applies** even if proceeds go to another crypto
- **FIFO is mandatory** — the oldest lot is consumed first
- **Values in EUR** — use the ECB rate for the day for USD conversions
- **Stablecoins** (USDT, USDC) = cryptocurrency for all tax purposes

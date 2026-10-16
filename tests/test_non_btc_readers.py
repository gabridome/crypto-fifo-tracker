"""Readers: export shape → normalized rows. Synthetic fixtures (public repo)."""
import io
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from importers.non_btc_readers import read_trade_history, split_amount, split_pair

TRADE_HISTORY = """Date(UTC),Pair,Side,Price,Executed,Amount,Fee
2024-01-15 10:00:00,BTCUSDT,SELL,60000.00,0.00100000BTC,60.00000000USDT,0.06000000USDT
2024-01-15 10:00:01,BTCEUR,SELL,55000.00,0.00200000BTC,110.00000000EUR,0.11000000EUR
"""


def test_trade_history_splits_pair_into_assets():
    rows = list(read_trade_history(io.StringIO(TRADE_HISTORY), exchange='Binance'))

    assert len(rows) == 2
    first = rows[0]
    assert first['kind'] == 'TRADE'
    assert first['give_asset'] == 'BTC'
    assert first['give_amount'] == pytest.approx(0.001)
    assert first['take_asset'] == 'USDT'
    assert first['take_amount'] == pytest.approx(60.0)
    assert first['fee_asset'] == 'USDT'
    assert first['fee_amount'] == pytest.approx(0.06)
    assert first['when'] == '2024-01-15T10:00:00+00:00'
    assert first['exchange'] == 'Binance'


def test_trade_history_unknown_pair_raises():
    """A pair the reader cannot split must stop the import, not be guessed."""
    bad = "Date(UTC),Pair,Side,Price,Executed,Amount,Fee\n2024-01-15 10:00:00,WEIRD,SELL,1,1WEIRD,1WEIRD,0WEIRD\n"
    with pytest.raises(ValueError, match='WEIRD'):
        list(read_trade_history(io.StringIO(bad), exchange='Binance'))


def test_trade_history_buy_side_inverts_legs():
    """A BUY row gives the quote asset and takes the base asset."""
    buy = "Date(UTC),Pair,Side,Price,Executed,Amount,Fee\n2024-01-15 10:00:00,BTCUSDT,BUY,60000.00,0.00100000BTC,60.00000000USDT,0.00000100BTC\n"
    row = next(read_trade_history(io.StringIO(buy), exchange='Binance'))
    assert row['give_asset'] == 'USDT'
    assert row['give_amount'] == pytest.approx(60.0)
    assert row['take_asset'] == 'BTC'
    assert row['take_amount'] == pytest.approx(0.001)


def test_trade_history_side_is_trimmed_and_case_insensitive():
    """A lowercase or padded Side ('sell ') is still a valid SELL, not rejected."""
    row = "Date(UTC),Pair,Side,Price,Executed,Amount,Fee\n2024-01-15 10:00:00,BTCUSDT,sell ,60000.00,0.001BTC,60.0USDT,0.0USDT\n"
    parsed = next(read_trade_history(io.StringIO(row), exchange='Binance'))
    assert parsed['give_asset'] == 'BTC'
    assert parsed['take_asset'] == 'USDT'


@pytest.mark.parametrize('side', ['', 'SEL', 'MARKET', 'BUYY'])
def test_trade_history_unknown_side_raises(side):
    """A Side that is neither BUY nor SELL must stop the import, not be
    silently treated as a BUY — a wrong leg is worse than no leg at all.
    A real Binance export in this project's archive has the typo 'SEL'.
    """
    bad = f"Date(UTC),Pair,Side,Price,Executed,Amount,Fee\n2024-01-15 10:00:00,BTCUSDT,{side},60000.00,0.001BTC,60.0USDT,0.0USDT\n"
    with pytest.raises(ValueError, match='Side'):
        list(read_trade_history(io.StringIO(bad), exchange='Binance'))


def test_split_amount_plain_and_suffixed():
    assert split_amount('0.00100000BTC') == (pytest.approx(0.001), 'BTC')
    assert split_amount('110.00') == (pytest.approx(110.0), '')


def test_split_amount_negative_is_made_positive():
    """The contract is positive amounts; sign must be read before calling."""
    assert split_amount('-0.5BTC') == (pytest.approx(0.5), 'BTC')


@pytest.mark.parametrize('raw', [
    '1.234,56',  # decimal comma — indistinguishable from thousands grouping, refused
    '1,234.56',  # thousands grouping — refused too, deliberately, same reason
    '',
    '1.63934E-5',  # scientific notation (seen in Price columns) — not parsed here
])
def test_split_amount_rejects_ambiguous_or_unparsable(raw):
    """A comma could be a decimal separator or a thousands separator — guessing
    wrong silently corrupts a tax figure by a factor of ~1000, so any comma
    is refused outright rather than interpreted either way.
    """
    with pytest.raises(ValueError):
        split_amount(raw)


@pytest.mark.parametrize('pair,base,quote', [
    ('BTCUSDT', 'BTC', 'USDT'),
    ('BTCUSDC', 'BTC', 'USDC'),
    ('BTCBUSD', 'BTC', 'BUSD'),
    ('BTCEUR', 'BTC', 'EUR'),
    ('BTCUSD', 'BTC', 'USD'),
    ('ETHBTC', 'ETH', 'BTC'),
    ('ETHUSDT', 'ETH', 'USDT'),
    # BUSD ends with 'USD': if 'USD' were tried before 'BUSD' this would
    # wrongly split into base='ETHB', quote='USD'.
    ('ETHBUSD', 'ETH', 'BUSD'),
])
def test_split_pair_known_quotes(pair, base, quote):
    assert split_pair(pair) == (base, quote)

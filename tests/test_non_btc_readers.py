"""Readers: export shape → normalized rows. Synthetic fixtures (public repo)."""
import io
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from importers.non_btc_readers import (
    read_account_history,
    read_trade_history,
    split_amount,
    split_pair,
)

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


ACCOUNT_HISTORY = """ID,Timestamp,Transaction Type,Asset,Quantity Transacted,Price Currency,Price at Transaction,Subtotal,Total (inclusive of fees and/or spread),Fees and/or Spread
a1,2024-02-20 09:00:00 UTC,Receive,USDC,1500.0,EUR,€0.93,€1395.0,€1395.0,€0.00
a2,2024-03-05 11:00:00 UTC,Advanced Trade Sell,USDC,2.0,EUR,€0.93,€1.86,€1.86,€0.00
a3,2024-02-21 08:00:00 UTC,Withdrawal,USDC,-50000.0,EUR,€0.92,€46500.0,€46500.0,€0.00
"""


def test_account_history_classifies_receive_sell_and_withdrawal():
    rows = list(read_account_history(io.StringIO(ACCOUNT_HISTORY), exchange='Coinbase'))

    kinds = [(r['kind'], r['give_asset'], r['take_asset']) for r in rows]
    assert kinds == [
        ('TRANSFER_IN', '', 'USDC'),      # Receive: arrival, not an acquisition
        ('TRADE', 'USDC', 'EUR'),         # sale against fiat
        ('CONVERT', 'USDC', 'EUR'),       # crypto out with a fiat subtotal
    ]
    assert rows[0]['take_amount'] == pytest.approx(1500.0)
    assert rows[1]['take_amount'] == pytest.approx(1.86)
    assert rows[2]['give_amount'] == pytest.approx(50000.0)
    assert rows[2]['take_amount'] == pytest.approx(46500.0)


def test_account_history_refuses_a_blank_subtotal_on_a_sale():
    """Blank must not become a zero-proceeds trade or a demoted transfer."""
    blank_sell = ACCOUNT_HISTORY.replace(',Advanced Trade Sell,USDC,2.0,EUR,€0.93,€1.86,€1.86,€0.00',
                                         ',Advanced Trade Sell,USDC,2.0,EUR,€0.93,,,€0.00')
    with pytest.raises(ValueError, match='Subtotal'):
        list(read_account_history(io.StringIO(blank_sell), exchange='Coinbase'))

    blank_withdrawal = ACCOUNT_HISTORY.replace(',Withdrawal,USDC,-50000.0,EUR,€0.92,€46500.0,€46500.0,€0.00',
                                               ',Withdrawal,USDC,-50000.0,EUR,€0.92,,,€0.00')
    with pytest.raises(ValueError, match='Subtotal'):
        list(read_account_history(io.StringIO(blank_withdrawal), exchange='Coinbase'))


def test_account_history_blank_subtotal_is_fine_on_a_transfer():
    """A Receive has no fiat side by construction: the blank is legitimate."""
    blank_receive = ACCOUNT_HISTORY.replace(',Receive,USDC,1500.0,EUR,€0.93,€1395.0,€1395.0,€0.00',
                                            ',Receive,USDC,1500.0,EUR,€0.93,,,€0.00')
    rows = list(read_account_history(io.StringIO(blank_receive), exchange='Coinbase'))
    assert rows[0]['kind'] == 'TRANSFER_IN'
    assert rows[0]['take_amount'] == pytest.approx(1500.0)


def test_account_history_refuses_an_ambiguous_fiat_amount():
    """A decimal comma must raise, not silently divide by a thousand.

    The replacement value is CSV-quoted ('"€1.395,00"', not '€1.395,00') so
    the embedded comma survives as part of the Subtotal field instead of
    being consumed by csv.DictReader as a column separator — an unquoted
    comma here would shift every later column in the row and the ambiguous
    value would never reach the parser as a single string.
    """
    bad = ACCOUNT_HISTORY.replace('€1395.0', '"€1.395,00"')
    with pytest.raises(ValueError, match='Subtotal'):
        list(read_account_history(io.StringIO(bad), exchange='Coinbase'))


def test_account_history_skips_fiat_only_rows():
    """A fiat deposit or withdrawal is bank plumbing, not a crypto leg."""
    fiat = ACCOUNT_HISTORY.replace('Receive,USDC', 'Deposit,EUR')
    rows = list(read_account_history(io.StringIO(fiat), exchange='Coinbase'))
    assert all(r['take_asset'] != 'EUR' or r['give_asset'] for r in rows)
    assert len(rows) == 2


def test_account_history_crypto_deposit_is_also_a_transfer_in():
    """'Deposit' is listed alongside 'Receive' for a crypto asset (some export
    variants use it for an inbound crypto transfer rather than 'Receive').
    Fiat 'Deposit' rows never reach this branch — they are skipped earlier,
    by asset, not by label — so this pins the one case where the label does
    reach it: a non-fiat asset.
    """
    crypto_deposit = ACCOUNT_HISTORY.replace(
        ',Receive,USDC,1500.0,EUR,€0.93,€1395.0,€1395.0,€0.00',
        ',Deposit,USDC,1500.0,EUR,€0.93,€1395.0,€1395.0,€0.00',
    )
    rows = list(read_account_history(io.StringIO(crypto_deposit), exchange='Coinbase'))
    assert rows[0]['kind'] == 'TRANSFER_IN'
    assert rows[0]['give_asset'] == ''
    assert rows[0]['take_asset'] == 'USDC'
    assert rows[0]['take_amount'] == pytest.approx(1500.0)


USD_ACCOUNT_HISTORY = """ID,Timestamp,Transaction Type,Asset,Quantity Transacted,Price Currency,Price at Transaction,Subtotal,Total (inclusive of fees and/or spread),Fees and/or Spread
u1,2024-04-01 12:00:00 UTC,Advanced Trade Sell,USDC,3.0,USD,$1.00,$3.00,$3.00,$0.00
"""


def test_account_history_fiat_leg_follows_price_currency_column():
    """The fiat side must come from the row, not be hardcoded to EUR — a
    USD subtotal saved as 'EUR' would be the right digits, wrong currency.
    """
    rows = list(read_account_history(io.StringIO(USD_ACCOUNT_HISTORY), exchange='Coinbase'))
    assert rows[0]['take_asset'] == 'USD'
    assert rows[0]['take_amount'] == pytest.approx(3.0)


def test_account_history_refuses_an_unrecognised_price_currency():
    """A Price Currency this reader does not know must stop the import,
    not be guessed at or silently passed through.
    """
    bad = USD_ACCOUNT_HISTORY.replace(',USD,', ',XXX,')
    with pytest.raises(ValueError, match='Price Currency'):
        list(read_account_history(io.StringIO(bad), exchange='Coinbase'))


def test_account_history_fee_asset_matches_price_currency():
    """A fee is quoted in the row's own fiat currency, read from the same
    'Price Currency' column — not invented as EUR. Convention: fee_asset
    carries that currency even when fee_amount is zero, since the currency
    is still well-defined on a zero fee; only the amount is zero.
    """
    fee = ACCOUNT_HISTORY.replace(
        ',Advanced Trade Sell,USDC,2.0,EUR,€0.93,€1.86,€1.86,€0.00',
        ',Advanced Trade Sell,USDC,2.0,EUR,€0.93,€1.86,€1.86,€0.50',
    )
    rows = list(read_account_history(io.StringIO(fee), exchange='Coinbase'))
    assert rows[1]['fee_asset'] == 'EUR'
    assert rows[1]['fee_amount'] == pytest.approx(0.5)
    # A zero fee still carries its currency — only the amount is zero.
    assert rows[0]['fee_asset'] == 'EUR'
    assert rows[0]['fee_amount'] == pytest.approx(0.0)


def test_account_history_malformed_quantity_raises_with_field_named():
    """Quantity Transacted goes through the same grammar as every other
    amount in this module (split_amount), so a comma is refused here too,
    and the error names the field rather than just the raw value.
    """
    bad = ACCOUNT_HISTORY.replace(
        ',Advanced Trade Sell,USDC,2.0,EUR,€0.93,€1.86,€1.86,€0.00',
        ',Advanced Trade Sell,USDC,"2,0",EUR,€0.93,€1.86,€1.86,€0.00',
    )
    with pytest.raises(ValueError, match='Quantity Transacted'):
        list(read_account_history(io.StringIO(bad), exchange='Coinbase'))


def test_account_history_send_is_transfer_out():
    """No existing test built a Send row: TRANSFER_OUT had zero coverage."""
    send = ACCOUNT_HISTORY.replace(
        ',Receive,USDC,1500.0,EUR,€0.93,€1395.0,€1395.0,€0.00',
        ',Send,USDC,1500.0,EUR,€0.93,,,€0.00',
    )
    rows = list(read_account_history(io.StringIO(send), exchange='Coinbase'))
    assert rows[0]['kind'] == 'TRANSFER_OUT'
    assert rows[0]['give_asset'] == 'USDC'
    assert rows[0]['give_amount'] == pytest.approx(1500.0)
    assert rows[0]['take_asset'] == ''
    assert rows[0]['take_amount'] == pytest.approx(0.0)


def test_account_history_buy_inverts_legs():
    """No existing test built a Buy row: swapping give/take in that branch
    would have passed the suite before this test existed.
    """
    buy = ACCOUNT_HISTORY.replace(
        ',Advanced Trade Sell,USDC,2.0,EUR,€0.93,€1.86,€1.86,€0.00',
        ',Advanced Trade Buy,USDC,2.0,EUR,€0.93,€1.86,€1.86,€0.00',
    )
    rows = list(read_account_history(io.StringIO(buy), exchange='Coinbase'))
    buy_row = rows[1]
    assert buy_row['kind'] == 'TRADE'
    assert buy_row['give_asset'] == 'EUR'
    assert buy_row['give_amount'] == pytest.approx(1.86)
    assert buy_row['take_asset'] == 'USDC'
    assert buy_row['take_amount'] == pytest.approx(2.0)


def test_account_history_timestamp_without_utc_suffix_raises():
    """A fixed-width slice would silently misparse a differently-shaped
    timestamp into the wrong instant; splitting on the literal ' UTC'
    suffix means an unrecognised shape raises instead.
    """
    bad = ACCOUNT_HISTORY.replace(
        'a1,2024-02-20 09:00:00 UTC,Receive',
        'a1,2024-02-20 09:00:00,Receive',
    )
    with pytest.raises(ValueError, match='Timestamp'):
        list(read_account_history(io.StringIO(bad), exchange='Coinbase'))

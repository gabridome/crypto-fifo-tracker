"""Valuation rules: who creates a lot, at what cost, and with which link."""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from importers.non_btc_valuation import value_row


class FakeRates:
    """Stand-in for ecb_rates/crypto_prices: fixed, explicit, no network."""
    def fiat_to_eur(self, amount, currency, when):
        return amount if currency == 'EUR' else amount * 0.93

    def crypto_to_eur(self, coin, amount, when):
        return amount * 60000.0 if coin == 'BTC' else amount * 0.93


RATES = FakeRates()


def _row(**kw):
    base = {'when': '2024-03-05T10:00:00+00:00', 'kind': 'CONVERT', 'give_asset': 'EUR',
            'give_amount': 10000.0, 'take_asset': 'USDC', 'take_amount': 10700.0,
            'fee_asset': '', 'fee_amount': 0.0, 'exchange': 'Bybit', 'ref': 'r1'}
    base.update(kw)
    return base


def test_fiat_buys_crypto_uses_the_fiat_paid():
    """Cost basis is what left the account, not the value at arrival elsewhere."""
    txs = value_row(_row(), RATES)

    assert len(txs) == 1
    tx = txs[0]
    assert tx['transaction_type'] == 'BUY'
    assert tx['cryptocurrency'] == 'USDC'
    assert tx['amount'] == pytest.approx(10700.0)
    assert tx['total_value'] == pytest.approx(10000.0)
    assert tx['price_per_unit'] == pytest.approx(10000.0 / 10700.0)
    assert tx['swap_group_id'] is None


def test_crypto_sold_for_fiat_is_a_sell():
    txs = value_row(_row(kind='TRADE', give_asset='USDC', give_amount=2.0,
                         take_asset='EUR', take_amount=1.86), RATES)

    assert [t['transaction_type'] for t in txs] == ['SELL']
    assert txs[0]['cryptocurrency'] == 'USDC'
    assert txs[0]['total_value'] == pytest.approx(1.86)


def test_crypto_for_crypto_emits_two_linked_legs():
    txs = value_row(_row(kind='TRADE', give_asset='BTC', give_amount=2.0,
                         take_asset='USDT', take_amount=120000.0, ref='o9'),
                     RATES, row_ref='file.csv#5')

    assert [t['transaction_type'] for t in txs] == ['SELL', 'BUY']
    assert txs[0]['cryptocurrency'] == 'BTC'
    assert txs[1]['cryptocurrency'] == 'USDT'
    # Both legs valued at the EUR value of the asset given up.
    assert txs[0]['total_value'] == pytest.approx(120000.0)
    assert txs[1]['total_value'] == pytest.approx(120000.0)
    assert txs[0]['swap_group_id'] == txs[1]['swap_group_id'] is not None


def test_fee_in_the_received_asset_reduces_the_lot():
    """The exchange takes its cut out of what arrives: the lot is the net.

    Gross 1000 USDT with a 2 USDT fee means 998 USDT in the account, bought
    with the same 2 BTC. Valuing the lot at 1000 would invent 2 USDT and
    spread the cost of the leg over a quantity that never existed.
    """
    txs = value_row(_row(kind='TRADE', give_asset='BTC', give_amount=2.0,
                         take_asset='USDT', take_amount=1000.0,
                         fee_asset='USDT', fee_amount=2.0),
                     RATES, row_ref='file.csv#6')

    buy = [t for t in txs if t['transaction_type'] == 'BUY'][0]
    assert buy['amount'] == pytest.approx(998.0)
    assert buy['total_value'] == pytest.approx(120000.0)   # cost unchanged: 2 BTC given up
    assert buy['fee_amount'] == pytest.approx(0.0)         # already inside the quantity


def test_fee_in_the_given_asset_is_part_of_the_cost():
    """A fee charged in what you hand over is part of what the purchase cost."""
    txs = value_row(_row(kind='CONVERT', give_asset='EUR', give_amount=10000.0,
                         take_asset='USDC', take_amount=10700.0,
                         fee_asset='EUR', fee_amount=25.0), RATES)

    assert len(txs) == 1
    assert txs[0]['amount'] == pytest.approx(10700.0)
    assert txs[0]['total_value'] == pytest.approx(10025.0)


def test_fee_in_a_third_asset_refuses():
    """A fee paid in neither leg (a discount token) cannot be allocated here."""
    with pytest.raises(ValueError, match='fee'):
        value_row(_row(kind='TRADE', give_asset='BTC', give_amount=2.0,
                       take_asset='USDT', take_amount=1000.0,
                       fee_asset='BNB', fee_amount=0.5), RATES)


def test_fee_without_an_asset_refuses():
    """A fee amount with no currency is not information: it is a guess."""
    with pytest.raises(ValueError, match='fee'):
        value_row(_row(kind='CONVERT', fee_asset='', fee_amount=25.0), RATES)


def test_transfer_creates_nothing():
    assert value_row(_row(kind='TRANSFER_IN', give_asset='', give_amount=0.0,
                          take_asset='USDC', take_amount=10640.0), RATES) == []
    assert value_row(_row(kind='TRANSFER_OUT', give_asset='USDC', give_amount=10650.0,
                          take_asset='', take_amount=0.0), RATES) == []


def test_dust_is_dropped():
    assert value_row(_row(take_amount=1e-9, give_amount=1e-9), RATES) == []


def test_fee_consuming_the_whole_leg_refuses():
    """A fee as big as (or bigger than) the leg it is taken from would leave

    a non-zero cost attached to a lot of ~zero quantity — a price_per_unit
    this layer cannot report honestly. This row survives the dust check
    (the *gross* take_amount is well above DUST), so a separate guard is
    needed once the fee is subtracted.
    """
    with pytest.raises(ValueError, match='fee'):
        value_row(_row(kind='TRADE', give_asset='BTC', give_amount=2.0,
                       take_asset='USDT', take_amount=2.0,
                       fee_asset='USDT', fee_amount=2.0),
                   RATES, row_ref='file.csv#7')


def test_crypto_for_crypto_without_row_ref_refuses():
    """Real exports collide on every field value_row could otherwise hash:

    same-second timestamps, a blank order-id ('ref'), sometimes identical
    quantities on both legs too. row_ref is the caller's own uniqueifier
    (e.g. its row position in the file) and is the one thing guaranteed to
    differ; a crypto-to-crypto swap refuses outright without it rather than
    falling back to a hash that can silently collide.
    """
    with pytest.raises(ValueError, match='row_ref'):
        value_row(_row(kind='TRADE', give_asset='BTC', give_amount=2.0,
                       take_asset='USDT', take_amount=120000.0, ref=''), RATES)


def test_different_row_ref_gives_different_swap_ids():
    """Two rows identical in every normalized field must not share an id."""
    row = _row(kind='TRADE', give_asset='BTC', give_amount=2.0,
               take_asset='USDT', take_amount=120000.0, ref='')

    txs_a = value_row(row, RATES, row_ref='file.csv#0')
    txs_b = value_row(row, RATES, row_ref='file.csv#1')

    assert txs_a[0]['swap_group_id'] != txs_b[0]['swap_group_id']


def test_same_row_ref_gives_the_same_swap_id_across_calls():
    """Stability across re-imports: the same row, re-read, must link the same way."""
    row = _row(kind='TRADE', give_asset='BTC', give_amount=2.0,
               take_asset='USDT', take_amount=120000.0, ref='o9')

    first = value_row(row, RATES, row_ref='file.csv#3')
    second = value_row(row, RATES, row_ref='file.csv#3')

    assert first[0]['swap_group_id'] == second[0]['swap_group_id']

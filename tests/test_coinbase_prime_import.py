"""Coinbase Prime importer — which orders count as executed.

Bug: the importer filtered `status == 'Completed'`, so orders cancelled *after*
a partial fill were dropped entirely, losing real executions (quantity, cost
basis and the fees that were charged on them).

The fixtures below are synthetic — shape of the real CSV export, invented
numbers (this repo is public: no real account data).
"""

import os
import sys

import pandas as pd
import pytest

# Project root on sys.path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from importers.import_coinbase_prime import FILLED_QTY_COLUMN, filled_orders


def _row(order_id, status, side, filled_base, filled_quote, price='60000'):
    """One row in the Coinbase Prime UI export format."""
    return {
        'order id': order_id,
        'status': status,
        'market': 'BTC/USD',
        'side': side,
        'type': 'LIMIT',
        'total order quantity': '100000',
        'order currency': 'USD',
        FILLED_QTY_COLUMN: filled_base,
        'filled quote quantity': filled_quote,
        'average fill price': price,
        'total fees and commissions': '12.50',
        'initiated by': 'Test User',
        'initiated time': '2024-01-15T20:59:36.31Z',
        'last updated time': '2024-01-15T22:54:48.935Z',
    }


# Ordini cancellati che però hanno eseguito: il caso che il filtro perdeva.
CANCELLED_WITH_FILLS = [
    _row('order-a', 'Cancelled', 'BUY', '0.25000000', '15000.00'),
    _row('order-b', 'Cancelled', 'BUY', '1.50000000', '90000.00'),
    _row('order-c', 'Cancelled', 'BUY', '0.10000000', '6000.00'),
    _row('order-d', 'Cancelled', 'BUY', '2.00000000', '120000.00'),
]


def test_filled_orders_keeps_cancelled_with_partial_fill():
    """Cancelled but filled = executed: the quantity is in
    `filled base quantity` and the fees were charged."""
    df = pd.DataFrame([_row('cancel-partial', 'Cancelled', 'BUY', '1.50000000', '90000.00')])

    kept = filled_orders(df)

    assert list(kept['order id']) == ['cancel-partial']


def test_filled_orders_drops_cancelled_without_fill():
    """Cancelled and never filled: nothing to record."""
    df = pd.DataFrame([_row('cancel-empty', 'Cancelled', 'SELL', '0', '0')])

    assert len(filled_orders(df)) == 0


def test_filled_orders_keeps_completed():
    """Historical behaviour is unchanged: Completed orders stay."""
    df = pd.DataFrame([_row('done', 'Completed', 'BUY', '0.50000000', '30000.00')])

    assert list(filled_orders(df)['order id']) == ['done']


def test_filled_orders_keeps_every_cancelled_fill():
    """Several cancelled-but-filled orders: all of them come back, in order."""
    df = pd.DataFrame(CANCELLED_WITH_FILLS)

    kept = filled_orders(df)

    assert list(kept['order id']) == ['order-a', 'order-b', 'order-c', 'order-d']
    assert kept[FILLED_QTY_COLUMN].astype(float).sum() == pytest.approx(3.85)
    assert kept['filled quote quantity'].astype(float).sum() == pytest.approx(231000.00)


def test_filled_orders_blank_or_non_numeric_quantity():
    """Blank or non-numeric cells must not blow up the import."""
    df = pd.DataFrame([
        _row('blank', 'Cancelled', 'BUY', '', ''),
        _row('dash', 'Cancelled', 'BUY', '-', '-'),
        _row('good', 'Completed', 'BUY', '1.5', '90000.00'),
    ])

    assert list(filled_orders(df)['order id']) == ['good']


def test_filled_orders_drops_dust():
    """Below DUST_THRESHOLD (1e-8) it is not an execution, it is noise."""
    df = pd.DataFrame([_row('dust', 'Completed', 'BUY', '0.000000001', '0.0001')])

    assert len(filled_orders(df)) == 0


def test_filled_orders_does_not_mutate_the_input():
    """The filter returns a copy: no SettingWithCopyWarning downstream."""
    df = pd.DataFrame(CANCELLED_WITH_FILLS)

    kept = filled_orders(df)
    kept['amount'] = 1.0

    assert 'amount' not in df.columns

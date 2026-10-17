# Non-BTC Legs Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Import the non-BTC legs (stablecoins, crypto-to-crypto swaps) that the BTC-only importers drop, with each acquisition valued where it happened, and make the crypto-to-crypto tax rule an explicit setting instead of prose.

**Architecture:** Three pure readers turn the three export shapes into normalized rows; a pure valuation module turns normalized rows into transaction dicts; one additive importer writes them under a `#non-btc` source tag so the BTC ledger is untouched. A `swap_group_id` column links the two legs of a swap, and `tax_rules.swap_realizes` in `config.py` says whether a swap realizes.

**Tech Stack:** Python 3.11, SQLite3, pandas (already a dependency), pytest, ruff. Spec: `doc/specs/2026-10-16-non-btc-legs-design.md`.

---

## Scope note on the tax rule

Both values of `swap_realizes` are implemented. `True` is today's behaviour and
the default: a swap is a disposal of the asset given up. `False` is the
deferral regime: the swap realizes nothing, the lots of the asset given up are
consumed, and the asset received inherits **one lot per lot consumed**, each
with its original acquisition date and cost — which is what keeps the
365-day clock honest. Tasks 8-11 build it. Which value is correct for a given
jurisdiction is a question for a tax professional, not for this repo: the
setting exists so the answer can be applied without re-importing anything.

## File structure

| file | responsibility |
|---|---|
| `calculators/update_fifo_schema_swap_group.py` (create) | one-shot idempotent migration: `swap_group_id` + index |
| `importers/non_btc_readers.py` (create) | three pure readers, export shape → normalized rows |
| `importers/non_btc_valuation.py` (create) | normalized row → transaction dict, the §5 rules |
| `importers/import_non_btc_legs.py` (create) | CLI + DB write via `import_and_verify` |
| `config.py` (modify) | `swap_realizes` in every profile's `tax_rules` |
| `calculators/crypto_fifo_tracker.py` (modify) | honour `swap_realizes`: realize, or defer and inherit lots; swap dependency order |
| `doc/compliance.md` (create) | the evidence method, no data |
| `doc/en/crypto_to_crypto_guide.md`, `doc/pt/crypto_to_crypto_guide.md` (modify) | assumption, not law |
| `calculators/update_fifo_schema_swap_transfers.py` (create) | migration: `swap_lot_transfers` |
| `tests/test_non_btc_readers.py`, `tests/test_non_btc_valuation.py`, `tests/test_import_non_btc_legs.py`, `tests/test_swap_realizes.py`, `tests/test_swap_deferral.py` (create) | one test module per unit |

Normalized row — the contract between readers and valuation, used by every task:

```python
# A reader yields dicts with exactly these keys.
{
    'when':      '2024-03-05T10:00:00+00:00',  # ISO 8601, UTC, str
    'kind':      'TRADE',        # TRADE | CONVERT | TRANSFER_IN | TRANSFER_OUT | FEE
    'give_asset': 'EUR',         # asset leaving (may be fiat), '' if none
    'give_amount': 10000.0,     # positive float
    'take_asset': 'USDC',        # asset arriving, '' if none
    'take_amount': 10700.0,   # positive float
    'fee_asset':  '',            # '' when no fee
    'fee_amount': 0.0,
    'exchange':   'Bybit',
    'ref':        'ORD-12345',   # exchange reference, '' if absent
}
```

---

### Task 1: `swap_group_id` column

**Files:**
- Create: `calculators/update_fifo_schema_swap_group.py`
- Test: `tests/test_import_non_btc_legs.py`

- [ ] **Step 1: Write the failing test**

```python
"""Non-BTC legs importer: schema, idempotency, and the BTC ledger it must not touch."""
import os
import sqlite3
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

PROJECT_ROOT = os.path.join(os.path.dirname(__file__), '..')


def _columns(db_path, table):
    conn = sqlite3.connect(db_path)
    try:
        return [r[1] for r in conn.execute(f'PRAGMA table_info({table})')]
    finally:
        conn.close()


def test_migration_adds_swap_group_id(db_path):
    """The migration adds the column and its index, and is safe to re-run."""
    script = os.path.join(PROJECT_ROOT, 'calculators', 'update_fifo_schema_swap_group.py')

    for _ in range(2):  # idempotent: second run must not fail
        r = subprocess.run([sys.executable, script, db_path], capture_output=True, text=True)
        assert r.returncode == 0, r.stderr

    assert 'swap_group_id' in _columns(db_path, 'transactions')

    conn = sqlite3.connect(db_path)
    try:
        idx = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='transactions'")]
    finally:
        conn.close()
    assert 'idx_transactions_swap_group' in idx
```

- [ ] **Step 2: Run it and watch it fail**

Run: `venv/bin/pytest tests/test_import_non_btc_legs.py::test_migration_adds_swap_group_id -v`
Expected: FAIL — the script does not exist, `returncode == 2`.

- [ ] **Step 3: Write the migration**

```python
#!/usr/bin/env python3
"""Add transactions.swap_group_id — the link between the two legs of one swap.

Idempotent: checks PRAGMA table_info before ALTER, creates the index with
IF NOT EXISTS. Takes the database path as argv[1], defaults to config.

Usage: python3 calculators/update_fifo_schema_swap_group.py [db_path]
"""
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import DATABASE_PATH

COLUMN = 'swap_group_id'
INDEX = 'idx_transactions_swap_group'


def migrate(db_path):
    conn = sqlite3.connect(db_path)
    try:
        conn.execute('BEGIN')
        existing = [row[1] for row in conn.execute('PRAGMA table_info(transactions)')]
        if COLUMN in existing:
            print(f"✓ Column '{COLUMN}' already present — nothing to do")
        else:
            conn.execute(f'ALTER TABLE transactions ADD COLUMN {COLUMN} TEXT')
            print(f"✓ Column '{COLUMN}' added")
        conn.execute(f'CREATE INDEX IF NOT EXISTS {INDEX} ON transactions({COLUMN})')
        print(f"✓ Index '{INDEX}' present")
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == '__main__':
    migrate(sys.argv[1] if len(sys.argv) > 1 else DATABASE_PATH)
```

- [ ] **Step 4: Run it and watch it pass**

Run: `venv/bin/pytest tests/test_import_non_btc_legs.py::test_migration_adds_swap_group_id -v`
Expected: PASS (2 runs of the script inside the test, both exit 0).

- [ ] **Step 5: Add the column to the schema source of truth**

`doc/schema.sql` is the single source the tests build fixtures from
(`doc/project_guidelines.md` §Schema DB). Add to the `transactions` table
definition, after `record_hash`:

```sql
    swap_group_id TEXT,                            -- both legs of one swap share this
```

and after the table:

```sql
CREATE INDEX IF NOT EXISTS idx_transactions_swap_group ON transactions(swap_group_id);
```

- [ ] **Step 6: Run the whole suite** — the fixture DB now carries the column, nothing else may break.

Run: `venv/bin/pytest tests/ -q`
Expected: all green (65 at this point).

- [ ] **Step 7: Commit**

```bash
git add calculators/update_fifo_schema_swap_group.py tests/test_import_non_btc_legs.py doc/schema.sql
git commit -m "feat(schema): swap_group_id on transactions, idempotent migration"
```

---

### Task 2: Trade-history reader (pair-based exports)

Shape: one row per fill, a `Pair` column like `BTCUSDT`, a `Side`, and amounts
carrying their unit as a suffix (`0.0024BTC`, `157.978248USDT`).

**Files:**
- Create: `importers/non_btc_readers.py`
- Test: `tests/test_non_btc_readers.py`

- [ ] **Step 1: Write the failing test**

```python
"""Readers: export shape → normalized rows. Synthetic fixtures (public repo)."""
import io
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from importers.non_btc_readers import read_trade_history

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
```

- [ ] **Step 2: Run it and watch it fail**

Run: `venv/bin/pytest tests/test_non_btc_readers.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'importers.non_btc_readers'`.

- [ ] **Step 3: Write the reader**

```python
"""Readers for the non-BTC legs of exchange exports.

Each reader takes an open text file and yields normalized rows (the contract is
documented in doc/plans/2026-10-16-non-btc-legs.md). Pure functions: no DB, no
network, no config. Unknown shapes raise ValueError — a leg guessed wrong is
worse than an import that stops.
"""
import csv
import re
from datetime import datetime, timezone

# Assets that may appear as the quote side of a pair. Longest first, so that
# 'BTCUSDT' splits into BTC/USDT and not BTC/USD + 'T'.
KNOWN_QUOTES = ('USDT', 'USDC', 'BUSD', 'EUR', 'USD', 'BTC', 'ETH')

_AMOUNT_RE = re.compile(r'^\s*(-?[\d.,]+)\s*([A-Za-z]*)\s*$')


def _utc(value, fmt='%Y-%m-%d %H:%M:%S'):
    """Export timestamps are UTC without offset; emit explicit ISO 8601 UTC."""
    return datetime.strptime(value.strip(), fmt).replace(tzinfo=timezone.utc).isoformat()


def split_amount(raw):
    """'0.0024BTC' → (0.0024, 'BTC'); '110.00' → (110.0, '')."""
    m = _AMOUNT_RE.match(str(raw))
    if not m:
        raise ValueError(f'unparsable amount: {raw!r}')
    number, unit = m.groups()
    return abs(float(number.replace(',', ''))), unit.upper()


def split_pair(pair):
    """'BTCUSDT' → ('BTC', 'USDT'). Raises on a pair we do not recognise."""
    pair = pair.strip().upper()
    for quote in KNOWN_QUOTES:
        if pair.endswith(quote) and len(pair) > len(quote):
            return pair[: -len(quote)], quote
    raise ValueError(f'unknown pair, cannot split into assets: {pair!r}')


def read_trade_history(fh, exchange):
    """Trade-history export: one row per fill, pair + side + suffixed amounts."""
    for row in csv.DictReader(fh):
        base, quote = split_pair(row['Pair'])
        executed, _ = split_amount(row['Executed'])
        amount, _ = split_amount(row['Amount'])
        fee_amount, fee_asset = split_amount(row['Fee'] or '0')
        side = (row['Side'] or '').strip().upper()
        give_asset, give_amount, take_asset, take_amount = (
            (base, executed, quote, amount) if side == 'SELL'
            else (quote, amount, base, executed)
        )
        yield {
            'when': _utc(row['Date(UTC)']),
            'kind': 'TRADE',
            'give_asset': give_asset,
            'give_amount': give_amount,
            'take_asset': take_asset,
            'take_amount': take_amount,
            'fee_asset': fee_asset,
            'fee_amount': fee_amount,
            'exchange': exchange,
            'ref': (row.get('Order No.') or '').strip(),
        }
```

- [ ] **Step 4: Run it and watch it pass**

Run: `venv/bin/pytest tests/test_non_btc_readers.py -v`
Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add importers/non_btc_readers.py tests/test_non_btc_readers.py
git commit -m "feat(readers): trade-history reader for non-BTC legs"
```

---

### Task 3: Account-history reader (per-asset rows)

Shape: one row per movement with `Transaction Type`, `Asset`, `Quantity
Transacted` and a fiat `Subtotal` — the account history of a retail exchange.
`Receive`/`Send` are transfers, `… Sell`/`… Buy` are trades against fiat,
`Withdrawal` of a crypto asset with a fiat subtotal is a conversion.

**Files:**
- Modify: `importers/non_btc_readers.py`
- Test: `tests/test_non_btc_readers.py`

- [ ] **Step 1: Write the failing test** (append to the test module)

```python
from importers.non_btc_readers import read_account_history

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
    """A decimal comma must raise, not silently divide by a thousand."""
    bad = ACCOUNT_HISTORY.replace('€1395.0', '€1.395,00')
    with pytest.raises(ValueError, match='Subtotal'):
        list(read_account_history(io.StringIO(bad), exchange='Coinbase'))


def test_account_history_skips_fiat_only_rows():
    """A fiat deposit or withdrawal is bank plumbing, not a crypto leg."""
    fiat = ACCOUNT_HISTORY.replace('Receive,USDC', 'Deposit,EUR')
    rows = list(read_account_history(io.StringIO(fiat), exchange='Coinbase'))
    assert all(r['take_asset'] != 'EUR' or r['give_asset'] for r in rows)
    assert len(rows) == 2
```

- [ ] **Step 2: Run it and watch it fail**

Run: `venv/bin/pytest tests/test_non_btc_readers.py -k account_history -v`
Expected: FAIL — `ImportError: cannot import name 'read_account_history'`.

- [ ] **Step 3: Write the reader** (append to `importers/non_btc_readers.py`)

```python
FIAT = ('EUR', 'USD', 'GBP', 'CHF')


_FIAT_RE = re.compile(r'^\s*-?\d+(?:\.\d+)?\s*$')


def _eur(raw, field='', allow_blank=False):
    """'€1395.0' → 1395.0. Raises on anything ambiguous, blank included.

    Same discipline as split_amount: a comma cannot be told apart from a
    decimal separator, so '€1.395,00' must raise instead of silently becoming
    1.39500 — a thousand times wrong, straight into a tax figure. Task 2
    shipped exactly that bug; do not reintroduce it here.

    A blank cell is only 0.0 where the caller declares it legitimate
    (`allow_blank=True`): the exports leave the fiat subtotal empty on rows
    that have no fiat side at all, i.e. transfers. Everywhere else a blank is
    an error, because "blank means zero" is how a real disposal gets silently
    demoted to a transfer, or a real trade recorded with zero proceeds — the
    same failure shape as the comma bug, moved up into business logic.
    """
    cleaned = str(raw).replace('€', '').replace('$', '').strip()
    if not cleaned:
        if allow_blank:
            return 0.0
        raise ValueError(f'blank {field or "fiat amount"}: a value is required on this row')
    if not _FIAT_RE.match(cleaned):
        raise ValueError(f'unparsable fiat amount in {field or "export"}: {raw!r}')
    return abs(float(cleaned))


def read_account_history(fh, exchange):
    """Account-history export: one row per movement, per asset."""
    for row in csv.DictReader(fh):
        asset = (row['Asset'] or '').strip().upper()
        if asset in FIAT:
            continue  # fiat deposits/withdrawals are bank plumbing
        kind_raw = (row['Transaction Type'] or '').strip()
        qty = abs(float(row['Quantity Transacted']))
        when = _utc(row['Timestamp'][:19], fmt='%Y-%m-%d %H:%M:%S')
        base = {
            'when': when, 'fee_asset': '',
            'fee_amount': _eur(row.get('Fees and/or Spread'), field='Fees and/or Spread',
                               allow_blank=True),
            'exchange': exchange, 'ref': (row.get('ID') or '').strip(),
        }
        # The fiat side is required exactly where the row has one. Asking for it
        # on a transfer would reject a legitimately blank cell; defaulting it to
        # zero on a trade or a withdrawal would invent a figure.
        needs_fiat = kind_raw in ('Withdrawal',) or 'Sell' in kind_raw or 'Buy' in kind_raw
        fiat_value = _eur(row['Subtotal'], field='Subtotal') if needs_fiat else 0.0
        if kind_raw in ('Receive', 'Deposit'):
            yield {**base, 'kind': 'TRANSFER_IN', 'give_asset': '', 'give_amount': 0.0,
                   'take_asset': asset, 'take_amount': qty}
        elif kind_raw in ('Send',):
            yield {**base, 'kind': 'TRANSFER_OUT', 'give_asset': asset, 'give_amount': qty,
                   'take_asset': '', 'take_amount': 0.0}
        elif 'Sell' in kind_raw:
            yield {**base, 'kind': 'TRADE', 'give_asset': asset, 'give_amount': qty,
                   'take_asset': 'EUR', 'take_amount': fiat_value}
        elif 'Buy' in kind_raw:
            yield {**base, 'kind': 'TRADE', 'give_asset': 'EUR', 'give_amount': fiat_value,
                   'take_asset': asset, 'take_amount': qty}
        elif kind_raw == 'Withdrawal':
            # Crypto leaving with a fiat subtotal is recorded as a sale: that is
            # what it was on this account. The export cannot tell a sale from a
            # transfer carrying a valuation, so the importer prints every row
            # classified this way for confirmation against the bank side
            # (decision of 2026-10-16; see task 6). A blank subtotal cannot
            # reach this branch — _eur raises — so only a real zero means
            # transfer.
            if fiat_value > 0:
                yield {**base, 'kind': 'CONVERT', 'give_asset': asset, 'give_amount': qty,
                       'take_asset': 'EUR', 'take_amount': fiat_value}
            else:
                yield {**base, 'kind': 'TRANSFER_OUT', 'give_asset': asset,
                       'give_amount': qty, 'take_asset': '', 'take_amount': 0.0}
        else:
            raise ValueError(f'unknown account-history type: {kind_raw!r}')
```

- [ ] **Step 4: Run it and watch it pass**

Run: `venv/bin/pytest tests/test_non_btc_readers.py -v`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add importers/non_btc_readers.py tests/test_non_btc_readers.py
git commit -m "feat(readers): account-history reader"
```

---

### Task 4: Deposit/withdrawal reader (fiat↔crypto conversions)

Shape: a preamble line starting with `UID:`, then `Date & Time(UTC)`, `Coin`,
`QTY` (signed), `Type` (`Fiat`, `Deposit`, `Withdraw`, `Transfer in/out`) and a
`Description` (`Convert`, `Deposit`, …). A conversion is two rows with the same
timestamp to the second: fiat out, crypto in.

**Files:**
- Modify: `importers/non_btc_readers.py`
- Test: `tests/test_non_btc_readers.py`

- [ ] **Step 1: Write the failing test** (append)

```python
from importers.non_btc_readers import read_deposit_withdrawal

DEPOSIT_WITHDRAWAL = """UID: 123,Company Name: ,Country: 
Uid,Date & Time(UTC),Coin,QTY,Type,Account Balance,Description
123,2024-03-05 10:00:00,USDC,10700.000000,Fiat,10700.000000,Convert
123,2024-03-05 09:59:50,EUR,-10000.000000,Fiat,0.000000,Convert
123,2024-03-05 10:15:00,USDC,-10650.000000,Withdraw,0.000000,Withdrawal
"""


def test_deposit_withdrawal_pairs_a_convert():
    rows = list(read_deposit_withdrawal(io.StringIO(DEPOSIT_WITHDRAWAL), exchange='Bybit'))

    convert = [r for r in rows if r['kind'] == 'CONVERT']
    assert len(convert) == 1
    assert convert[0]['give_asset'] == 'EUR'
    assert convert[0]['give_amount'] == pytest.approx(10000.0)
    assert convert[0]['take_asset'] == 'USDC'
    assert convert[0]['take_amount'] == pytest.approx(10700.0)

    out = [r for r in rows if r['kind'] == 'TRANSFER_OUT']
    assert len(out) == 1
    assert out[0]['give_amount'] == pytest.approx(10650.0)


def test_deposit_withdrawal_sign_is_the_direction():
    """The sign decides in or out, and the module's grammar still applies."""
    from importers.non_btc_readers import _signed_qty

    assert _signed_qty('-10650.5', {}) == pytest.approx(-10650.5)
    assert _signed_qty('10700.25', {}) == pytest.approx(10700.25)
    with pytest.raises(ValueError, match='QTY'):
        _signed_qty('-1500.0BTC', {})
    with pytest.raises(ValueError):
        _signed_qty('1.234,56', {})


def test_deposit_withdrawal_unpaired_convert_raises():
    """Half a conversion is a broken export: stop, do not invent the other leg."""
    half = DEPOSIT_WITHDRAWAL.replace('123,2024-03-05 09:59:50,EUR,-10000.000000,Fiat,0.000000,Convert\n', '')
    with pytest.raises(ValueError, match='unpaired'):
        list(read_deposit_withdrawal(io.StringIO(half), exchange='Bybit'))
```

- [ ] **Step 2: Run it and watch it fail**

Run: `venv/bin/pytest tests/test_non_btc_readers.py -k deposit_withdrawal -v`
Expected: FAIL — `ImportError: cannot import name 'read_deposit_withdrawal'`.

- [ ] **Step 3: Write the reader** (append)

```python
def _signed_qty(raw, row):
    """The quantity of a deposit/withdrawal row, sign included.

    Here the sign *is* the direction — negative leaves the account, positive
    enters it — so this cannot use `split_amount` directly: that helper returns
    a positive amount by contract. It reuses its grammar, which refuses an
    ambiguous comma, a stray unit and scientific notation, and then puts the
    sign back. A bare `float()` would accept shapes the rest of the module
    rejects, and this column decides whether an asset came in or went out.
    """
    amount, unit = split_amount(raw)
    if unit:
        raise ValueError(f'unexpected unit in QTY: {raw!r} '
                         f'(Coin={row.get("Coin")!r}, '
                         f'Time={row.get("Date & Time(UTC)")!r})')
    return -amount if str(raw).strip().startswith('-') else amount


def read_deposit_withdrawal(fh, exchange):
    """Deposit/withdrawal export. Conversions are two rows sharing a minute."""
    lines = fh.read().splitlines()
    if lines and lines[0].startswith('UID'):
        lines = lines[1:]
    rows = list(csv.DictReader(lines))

    converts, others = {}, []
    for row in rows:
        coin = (row['Coin'] or '').strip().upper()
        qty = _signed_qty(row['QTY'], row)
        when = _utc(row['Date & Time(UTC)'])
        desc = (row['Description'] or '').strip()
        if desc == 'Convert':
            # Group on the minute: the two legs of one conversion are seconds apart.
            converts.setdefault(when[:16], []).append((coin, qty, when))
        elif desc.startswith('Withdraw'):
            others.append({'when': when, 'kind': 'TRANSFER_OUT', 'give_asset': coin,
                           'give_amount': abs(qty), 'take_asset': '', 'take_amount': 0.0,
                           'fee_asset': '', 'fee_amount': 0.0, 'exchange': exchange, 'ref': ''})
        elif desc.startswith('Deposit') or desc.startswith('Transfer'):
            key = 'TRANSFER_IN' if qty > 0 else 'TRANSFER_OUT'
            others.append({'when': when, 'kind': key,
                           'give_asset': '' if qty > 0 else coin, 'give_amount': 0.0 if qty > 0 else abs(qty),
                           'take_asset': coin if qty > 0 else '', 'take_amount': abs(qty) if qty > 0 else 0.0,
                           'fee_asset': '', 'fee_amount': 0.0, 'exchange': exchange, 'ref': ''})
        else:
            raise ValueError(f'unknown deposit/withdrawal description: {desc!r}')

    for minute, legs in sorted(converts.items()):
        given = [leg for leg in legs if leg[1] < 0]
        taken = [leg for leg in legs if leg[1] > 0]
        if len(given) != 1 or len(taken) != 1:
            raise ValueError(f'unpaired convert at {minute}: {legs!r}')
        (give_asset, give_qty, _), (take_asset, take_qty, when) = given[0], taken[0]
        yield {'when': when, 'kind': 'CONVERT', 'give_asset': give_asset,
               'give_amount': abs(give_qty), 'take_asset': take_asset, 'take_amount': take_qty,
               'fee_asset': '', 'fee_amount': 0.0, 'exchange': exchange, 'ref': ''}

    for row in others:
        yield row
```

- [ ] **Step 4: Run it and watch it pass**

Run: `venv/bin/pytest tests/test_non_btc_readers.py -v`
Expected: 6 passed.

- [ ] **Step 5: Run ruff and commit**

```bash
venv/bin/ruff check .
git add importers/non_btc_readers.py tests/test_non_btc_readers.py
git commit -m "feat(readers): deposit/withdrawal reader, conversions paired by minute"
```

---

### Task 5: Valuation — normalized row → transaction dicts

The §5 rules of the spec, as one pure function. It returns a list because a
swap produces two transactions, a transfer none.

**What the readers already guarantee you.** Both readers carry the fee's own
currency in `fee_asset` and the fiat leg's real currency in `give_asset` /
`take_asset` — the account-history reader reads it from the row's
`Price Currency` column and refuses an unknown one (fixed in task 3 after
review). So `value_row` may trust that a fiat asset code is the real one and
must convert it when it is not the reporting currency; it must never assume
EUR.

**Why fees are not a detail here.** On a crypto-to-crypto swap the exchange
takes its fee out of the asset received: the trade history reports a gross
quantity, while what lands in the account — and what the next leg sells — is
the net. Valuing the lot at the gross quantity creates crypto out of nothing
and, worse, creates it with the cost basis of the whole leg, which lowers the
cost per unit of everything downstream. The rule below handles it.

**Files:**
- Create: `importers/non_btc_valuation.py`
- Test: `tests/test_non_btc_valuation.py`

- [ ] **Step 1: Write the failing test**

```python
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
                         take_asset='USDT', take_amount=120000.0, ref='o9'), RATES)

    assert [t['transaction_type'] for t in txs] == ['SELL', 'BUY']
    assert txs[0]['cryptocurrency'] == 'BTC'
    assert txs[1]['cryptocurrency'] == 'USDT'
    # Both legs valued at the EUR value of the asset given up.
    assert txs[0]['total_value'] == pytest.approx(120000.0)
    assert txs[1]['total_value'] == pytest.approx(120000.0)
    assert txs[0]['swap_group_id'] == txs[1]['swap_group_id'] != None  # noqa: E711


def test_fee_in_the_received_asset_reduces_the_lot():
    """The exchange takes its cut out of what arrives: the lot is the net.

    Gross 1000 USDT with a 2 USDT fee means 998 USDT in the account, bought
    with the same 2 BTC. Valuing the lot at 1000 would invent 2 USDT and
    spread the cost of the leg over a quantity that never existed.
    """
    txs = value_row(_row(kind='TRADE', give_asset='BTC', give_amount=2.0,
                         take_asset='USDT', take_amount=1000.0,
                         fee_asset='USDT', fee_amount=2.0), RATES)

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
```

- [ ] **Step 2: Run it and watch it fail**

Run: `venv/bin/pytest tests/test_non_btc_valuation.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'importers.non_btc_valuation'`.

- [ ] **Step 3: Write the valuation module**

```python
"""Valuation rules for non-BTC legs (spec §5).

Pure: a normalized row plus a rate source in, transaction dicts out. The rate
source is injected so tests do not need the network and so the caller decides
whether a missing rate is fatal (it is: see import_non_btc_legs).
"""
import hashlib
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from calculators.crypto_fifo_tracker import DUST_THRESHOLD

FIAT = ('EUR', 'USD', 'GBP', 'CHF')
DUST = float(DUST_THRESHOLD)


def _swap_id(row):
    """Stable id for the two legs of one swap: same inputs, same id, always."""
    raw = f"{row['when']}|{row['exchange']}|{row['give_asset']}|{row['give_amount']}|{row['take_asset']}|{row['take_amount']}|{row['ref']}"
    return 'swap-' + hashlib.sha256(raw.encode('utf-8')).hexdigest()[:16]


def _tx(row, tx_type, crypto, amount, total_value, swap_group_id=None):
    return {
        'transaction_date': row['when'],
        'transaction_type': tx_type,
        'cryptocurrency': crypto,
        'amount': amount,
        'price_per_unit': (total_value / amount) if amount else 0.0,
        'total_value': total_value,
        'fee_amount': 0.0,
        'exchange_name': row['exchange'],
        'transaction_id': row['ref'],
        'swap_group_id': swap_group_id,
    }


def value_row(row, rates):
    """Return the transactions a normalized row produces: 0, 1 or 2."""
    if row['kind'] in ('TRANSFER_IN', 'TRANSFER_OUT'):
        return []  # moving your own asset is neither acquisition nor disposal

    give, take = row['give_asset'], row['take_asset']
    give_amt, take_amt = row['give_amount'], row['take_amount']
    if max(give_amt, take_amt) <= DUST:
        return []

    give_is_fiat, take_is_fiat = give in FIAT, take in FIAT

    # Where the fee sits decides what it does. Taken out of what arrives, it
    # is already missing from the account, so it reduces the quantity acquired.
    # Charged on what you hand over, it is part of what the purchase cost. Paid
    # in a third asset it cannot be allocated without valuing that asset too,
    # which this function has no mandate to do: it refuses.
    fee_asset, fee_amt = row['fee_asset'], row['fee_amount']
    if fee_amt > DUST and not fee_asset:
        raise ValueError(f'fee of {fee_amt} with no asset: cannot tell what was charged')
    if fee_amt > DUST and fee_asset not in (give, take):
        raise ValueError(
            f'fee charged in {fee_asset}, which is neither leg ({give}/{take}): '
            f'allocating it would need a price for {fee_asset} too'
        )
    if fee_amt > DUST and fee_asset == take:
        take_amt -= fee_amt
    give_total = give_amt + (fee_amt if fee_amt > DUST and fee_asset == give else 0.0)

    if give_is_fiat and not take_is_fiat:
        value = rates.fiat_to_eur(give_total, give, row['when'])
        return [_tx(row, 'BUY', take, take_amt, value)]

    if take_is_fiat and not give_is_fiat:
        # The fee, if charged in the fiat received, is already out of take_amt.
        value = rates.fiat_to_eur(take_amt, take, row['when'])
        return [_tx(row, 'SELL', give, give_amt, value)]

    if not give_is_fiat and not take_is_fiat:
        value = rates.crypto_to_eur(give_total, give, row['when'])
        group = _swap_id(row)
        return [
            _tx(row, 'SELL', give, give_amt, value, group),
            _tx(row, 'BUY', take, take_amt, value, group),
        ]

    return []  # fiat ↔ fiat is not a crypto leg
```

- [ ] **Step 4: Run it and watch it pass**

Run: `venv/bin/pytest tests/test_non_btc_valuation.py -v`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add importers/non_btc_valuation.py tests/test_non_btc_valuation.py
git commit -m "feat(valuation): cost basis where the acquisition happened, swaps linked"
```

---

### Task 6: The importer

**Files:**
- Create: `importers/import_non_btc_legs.py`
- Test: `tests/test_import_non_btc_legs.py`

- [ ] **Step 1: Write the failing tests** (append to the module from Task 1)

```python
import csv

from importers.import_non_btc_legs import SOURCE_SUFFIX, import_non_btc_legs

DEPOSIT_WITHDRAWAL = """UID: 123,Company Name: ,Country: 
Uid,Date & Time(UTC),Coin,QTY,Type,Account Balance,Description
123,2024-03-05 10:00:00,USDC,10700.000000,Fiat,10700.000000,Convert
123,2024-03-05 09:59:50,EUR,-10000.000000,Fiat,0.000000,Convert
"""


def _seed_btc_row(db_path, source):
    """A BTC row written by another importer, from the same file."""
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("""
            INSERT INTO transactions (transaction_date, transaction_type, exchange_name,
                cryptocurrency, amount, price_per_unit, total_value, fee_amount,
                fee_currency, currency, source, imported_at, record_hash)
            VALUES ('2024-02-01T09:00:00+00:00','SELL','Bybit','BTC',0.010000,50000.00,
                    500.00,0,'EUR','EUR',?, '2026-01-01T00:00:00', 'hash-btc-1')
        """, (source,))
        conn.commit()
    finally:
        conn.close()


def test_import_writes_legs_without_touching_btc_rows(db_path, tmp_path, monkeypatch):
    """The regression that matters: the #non-btc tag must not delete BTC rows."""
    export = tmp_path / 'bybit_dw.csv'
    export.write_text(DEPOSIT_WITHDRAWAL)
    _seed_btc_row(db_path, export.name)

    inserted = import_non_btc_legs(str(export), exchange_name='Bybit',
                                   shape='deposit_withdrawal', db_path=db_path)

    assert inserted == 1
    conn = sqlite3.connect(db_path)
    try:
        btc = conn.execute("SELECT COUNT(*) FROM transactions WHERE cryptocurrency='BTC'").fetchone()[0]
        legs = conn.execute("SELECT cryptocurrency, transaction_type, total_value, source "
                            "FROM transactions WHERE cryptocurrency='USDC'").fetchall()
    finally:
        conn.close()

    assert btc == 1, 'the BTC row of the same file was deleted'
    assert legs == [('USDC', 'BUY', 10000.0, export.name + SOURCE_SUFFIX)]


def test_convert_rows_are_listed_for_confirmation(db_path, tmp_path, capsys):
    """A withdrawal recorded as a sale must be announced, never silent."""
    export = tmp_path / 'account_history.csv'
    export.write_text(
        'ID,Timestamp,Transaction Type,Asset,Quantity Transacted,Price Currency,'
        'Price at Transaction,Subtotal,Total (inclusive of fees and/or spread),Fees and/or Spread\n'
        'w1,2024-02-21 08:00:00 UTC,Withdrawal,USDC,-50000.0,EUR,€0.92,€46500.0,€46500.0,€0.00\n'
    )

    import_non_btc_legs(str(export), exchange_name='Coinbase',
                        shape='account_history', db_path=db_path)

    err = capsys.readouterr().err
    assert 'recorded as a SALE' in err
    assert '50000.00000000 USDC' in err


def test_zero_proceeds_withdrawals_are_listed_too(db_path, tmp_path, capsys):
    """The transfer side of the fork gets the same confirmation list."""
    export = tmp_path / 'account_history.csv'
    export.write_text(
        'ID,Timestamp,Transaction Type,Asset,Quantity Transacted,Price Currency,'
        'Price at Transaction,Subtotal,Total (inclusive of fees and/or spread),Fees and/or Spread\n'
        'w2,2024-02-22 08:00:00 UTC,Withdrawal,USDC,-25.0,EUR,€0.00,€0.00,€0.00,€0.00\n'
    )

    import_non_btc_legs(str(export), exchange_name='Coinbase',
                        shape='account_history', db_path=db_path)

    err = capsys.readouterr().err
    assert 'recorded as non-taxable' in err
    assert '25.00000000 USDC' in err


def test_import_is_idempotent(db_path, tmp_path):
    export = tmp_path / 'bybit_dw.csv'
    export.write_text(DEPOSIT_WITHDRAWAL)

    import_non_btc_legs(str(export), 'Bybit', 'deposit_withdrawal', db_path)
    first = sqlite3.connect(db_path).execute(
        'SELECT record_hash FROM transactions ORDER BY id').fetchall()
    import_non_btc_legs(str(export), 'Bybit', 'deposit_withdrawal', db_path)
    second = sqlite3.connect(db_path).execute(
        'SELECT record_hash FROM transactions ORDER BY id').fetchall()

    assert first == second and len(first) == 1
```

- [ ] **Step 2: Run them and watch them fail**

Run: `venv/bin/pytest tests/test_import_non_btc_legs.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'importers.import_non_btc_legs'`.

- [ ] **Step 3: Write the importer**

```python
#!/usr/bin/env python3
"""Import the non-BTC legs of an exchange export.

Additive by design: it writes only non-BTC rows, under a source tag of its own
(`<filename>#non-btc`), so re-importing the legs can never delete the BTC rows
that another importer wrote from the same file.

Usage:
    python3 importers/import_non_btc_legs.py <filepath> <shape> [exchange_name]

    shape: trade_history | account_history | deposit_withdrawal
"""
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import DATABASE_PATH
from importers.crypto_prices import CryptoPrices
from importers.ecb_rates import ECBRates
from importers.import_utils import compute_record_hash, import_and_verify
from importers.non_btc_readers import (
    read_account_history,
    read_deposit_withdrawal,
    read_trade_history,
)
from importers.non_btc_valuation import value_row

SOURCE_SUFFIX = '#non-btc'

READERS = {
    'trade_history': read_trade_history,
    'account_history': read_account_history,
    'deposit_withdrawal': read_deposit_withdrawal,
}


class Rates:
    """The project's rate sources behind the interface valuation expects.

    A missing rate aborts the import (doc/project_guidelines.md §Conversione
    valute): a leg valued with a guessed rate is worse than no leg.
    """

    def __init__(self):
        self._ecb = ECBRates()
        self._crypto = CryptoPrices()

    def fiat_to_eur(self, amount, currency, when):
        """Keep these parameter names: value_row calls by keyword on purpose.

        The plan once had the crypto call's arguments swapped, and only the
        test double's parameter names turned it into a TypeError instead of a
        silently wrong figure (task 5 review). Renaming a parameter here
        breaks the call loudly, which is the point.
        """
        if currency == 'EUR':
            return amount
        if currency != 'USD':
            raise ValueError(
                f'no rate source for fiat {currency}: add one before importing '
                f'an export denominated in it rather than treating it as EUR '
                f'(doc/project_guidelines.md §Conversione valute)')
        return self._ecb.usd_to_eur(amount, datetime.fromisoformat(when))

    def crypto_to_eur(self, coin, amount, when):
        value = self._crypto.crypto_to_eur(coin, amount, datetime.fromisoformat(when))
        if value is None:
            raise ValueError(f'no price for {coin} on {when}: import aborted')
        return value


def import_non_btc_legs(filepath, exchange_name, shape, db_path=None):
    """Read one export, write its non-BTC legs. Returns the number of rows."""
    if shape not in READERS:
        raise ValueError(f'unknown shape {shape!r}, expected one of {sorted(READERS)}')

    source = os.path.basename(filepath) + SOURCE_SUFFIX
    imported_at = datetime.now().isoformat()
    rates = Rates()

    with open(filepath, encoding='utf-8-sig') as fh:
        rows = list(READERS[shape](fh, exchange_name))

    # row_ref must be unique per row: value_row folds it into the swap id, and
    # the real export has 74 crypto-to-crypto trades sharing one timestamp with
    # no order id, two pairs of which are identical on both quantities. Without
    # it, distinct swaps would share an inheritance key (task 5 refuses a swap
    # with a blank row_ref rather than minting a colliding id).
    txs = [tx
           for i, row in enumerate(rows)
           for tx in value_row(row, rates, row_ref=f'{source}#{i}')
           if tx['cryptocurrency'] != 'BTC']

    print(f'  {len(rows)} rows read, {len(txs)} non-BTC legs to write')

    # A crypto withdrawal carrying a fiat subtotal is recorded as a sale (see
    # the reader): the export cannot tell that from a transfer with a
    # valuation attached, so every such row is printed for confirmation
    # against the bank side instead of passing unnoticed.
    converts = [r for r in rows if r['kind'] == 'CONVERT']
    if converts:
        print(f'  ⚠ {len(converts)} withdrawal row(s) recorded as a SALE — confirm each '
              f'against the receiving account:', file=sys.stderr)
        for r in converts:
            print(f"      {r['when'][:10]}  {r['give_amount']:.8f} {r['give_asset']} "
                  f"→ {r['take_amount']:.2f} {r['take_asset']}", file=sys.stderr)

    # The opposite misclassification deserves a net too. A withdrawal whose
    # proceeds are zero is recorded as a transfer, and a real disposal whose
    # value merely *rounds* to zero is indistinguishable from one that genuinely
    # had no fiat side. This list cannot be narrowed to the doubtful rows: which
    # label produced a TRANSFER_OUT — a plain Send, or a withdrawal whose fiat
    # subtotal came back blank — is a fact of the reader's classification and is
    # not carried in the normalized row. So it names the whole class and says so,
    # rather than claiming a precision it does not have.
    zero_out = [r for r in rows
                if r['kind'] == 'TRANSFER_OUT' and r['give_amount'] > 0 and not r['take_asset']]
    if zero_out:
        print(f'  ⚠ {len(zero_out)} outgoing transfer(s) recorded as non-taxable — '
              f'none of these produced a disposal; confirm that is right:', file=sys.stderr)
        for r in zero_out:
            print(f"      {r['when'][:10]}  {r['give_amount']:.8f} {r['give_asset']} out",
                  file=sys.stderr)

    def do_inserts(conn):
        cursor = conn.cursor()
        for tx in txs:
            cursor.execute("""
                INSERT INTO transactions (
                    transaction_date, transaction_type, exchange_name, cryptocurrency,
                    amount, price_per_unit, total_value, fee_amount, fee_currency,
                    currency, transaction_id, notes, source, imported_at, record_hash,
                    swap_group_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                tx['transaction_date'], tx['transaction_type'], tx['exchange_name'],
                tx['cryptocurrency'], tx['amount'], tx['price_per_unit'],
                tx['total_value'], tx['fee_amount'], 'EUR', 'EUR',
                tx['transaction_id'], f"non-BTC leg from {os.path.basename(filepath)}",
                source, imported_at,
                compute_record_hash(source, tx['transaction_date'], tx['transaction_type'],
                                    tx['exchange_name'], tx['cryptocurrency'],
                                    tx['amount'], tx['total_value'], tx['fee_amount']),
                tx['swap_group_id'],
            ))
        return len(txs)

    return import_and_verify(db_path or DATABASE_PATH, source, do_inserts)


if __name__ == '__main__':
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)
    import_non_btc_legs(sys.argv[1], sys.argv[3] if len(sys.argv) > 3 else 'Unknown', sys.argv[2])
```

- [ ] **Step 4: Run them and watch them pass**

Run: `venv/bin/pytest tests/test_import_non_btc_legs.py -v`
Expected: 3 passed (migration + the two above).

- [ ] **Step 5: Pin the fee contract on its own**

`_apply_fee` is exercised today only through `value_row`'s branches. Tasks 9-11
will depend on its `give_total` — "what was actually disposed" — so pin the
contract where it can be read without the valuation around it. Add to
`tests/test_non_btc_valuation.py`:

```python
from importers.non_btc_valuation import _apply_fee


def test_apply_fee_no_fee_is_a_passthrough():
    assert _apply_fee('BTC', 2.0, 'USDT', 1000.0, '', 0.0) == (1000.0, 2.0)


def test_apply_fee_in_the_received_asset_reduces_it():
    take, give_total = _apply_fee('BTC', 2.0, 'USDT', 1000.0, 'USDT', 2.0)
    assert (take, give_total) == (998.0, 2.0)


def test_apply_fee_in_the_given_asset_inflates_the_disposal():
    take, give_total = _apply_fee('BTC', 2.0, 'USDT', 1000.0, 'BTC', 0.01)
    assert take == 1000.0
    assert give_total == pytest.approx(2.01)


def test_apply_fee_refuses_a_blank_asset():
    with pytest.raises(ValueError, match='no asset'):
        _apply_fee('BTC', 2.0, 'USDT', 1000.0, '', 5.0)


def test_apply_fee_refuses_a_third_asset():
    with pytest.raises(ValueError, match='neither leg'):
        _apply_fee('BTC', 2.0, 'USDT', 1000.0, 'BNB', 0.5)


def test_apply_fee_refuses_consuming_the_whole_leg():
    with pytest.raises(ValueError, match='consumes the whole'):
        _apply_fee('BTC', 2.0, 'USDT', 1000.0, 'USDT', 1000.0)
```

- [ ] **Step 6: Run ruff and the whole suite, then commit**

```bash
venv/bin/ruff check .
venv/bin/pytest tests/ -q
git add importers/import_non_btc_legs.py tests/test_import_non_btc_legs.py tests/test_non_btc_valuation.py
git commit -m "feat(importers): import_non_btc_legs with an additive source tag"
```

---

### Task 7: `swap_realizes` as a setting

**Files:**
- Modify: `config.py` (every entry of `COUNTRY_PROFILES`, and the module-level exports near line 132)
- Modify: `calculators/crypto_fifo_tracker.py` (the imports only — tasks 9-11 are what act on the flag)
- Modify: `calculators/calculate_fifo.py` (print the rule in force)
- Test: `tests/test_swap_realizes.py`

- [ ] **Step 1: Write the failing test**

```python
"""The crypto-to-crypto rule is a setting, and an unsupported value must fail loudly."""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import config


def test_profiles_declare_swap_realizes():
    for code, profile in config.COUNTRY_PROFILES.items():
        assert 'swap_realizes' in profile['tax_rules'], f'{code} does not declare the rule'


def test_module_exports_the_flag():
    assert config.SWAP_REALIZES is True


def test_engine_reads_the_flag():
    """The engine must import the flag, not re-read config at call time."""
    import calculators.crypto_fifo_tracker as engine

    assert hasattr(engine, 'SWAP_REALIZES')
```

- [ ] **Step 2: Run it and watch it fail**

Run: `venv/bin/pytest tests/test_swap_realizes.py -v`
Expected: FAIL — `KeyError: 'swap_realizes'` on the first test, `AttributeError`
on the third.

- [ ] **Step 3: Add the setting to `config.py`**

In each profile's `tax_rules` (PT first, then the commented templates):

```python
            "swap_realizes": True,   # crypto→crypto realizes at the swap.
                                     # ASSUMPTION, not verified law — doc/compliance.md
```

and with the other module-level exports (after `DECLARE_EXEMPT`, line ~134):

```python
# No .get() default: a profile that forgets to declare the rule must fail at
# import, not silently inherit someone else's tax treatment.
SWAP_REALIZES = _profile["tax_rules"]["swap_realizes"]
```

- [ ] **Step 4: Let the engine read it**

In `calculators/crypto_fifo_tracker.py`, with the imports at the top:

```python
from config import SWAP_REALIZES
```

Nothing else in this task: Tasks 9-11 are what act on it. Importing it by name
(rather than reading `config` inside the loop) is what lets the tests set it
with `monkeypatch.setattr('calculators.crypto_fifo_tracker.SWAP_REALIZES', …)`.

- [ ] **Step 5: Run it and watch it pass**

Run: `venv/bin/pytest tests/test_swap_realizes.py -v`
Expected: 3 passed.

- [ ] **Step 6: Print the rule wherever a realized figure reaches a human**

Three places, the same line in each, so they are greppable as one thing:
`calculators/calculate_fifo.py`'s `[4/4] Summary`,
`calculators/generate_annual_summary.py`'s final totals, and
`calculators/generate_irs_report.py`'s console summary.

```python
    print(f"Tax rule in force: swap_realizes={SWAP_REALIZES} (config.py, profile {COUNTRY})")
```

with `from config import COUNTRY, SWAP_REALIZES` added to each file's existing
config import.

**Deliberately not done, decided with the owner on 2026-10-16:** the
`IRS_Crypto_FIFO_<year>.xlsx` that `generate_irs_report.py` writes — the file
that goes into the real filing — carries no trace of the rule either, and will
not until the rule actually changes. Today's value is the one already filed, so
nothing on paper is wrong; the risk is that two reports generated under
opposite rules would be indistinguishable as files.

When the jurisdiction answer arrives, the place is already identified:
`write_summary()`'s metadata cell `A2` on the "Resumo" sheet, which today reads
`Generato: … | Metodo FIFO | EUR` and takes `| swap_realizes=…` as one more
segment. That sheet is generated reporting. `write_quadro7` and
`write_quadro94a` mirror Anexo G1 Quadro 7 and Anexo J Quadro 9.4A cell for
cell and must stay untouched, or they stop matching the official layout.

- [ ] **Step 7: Run the suite and commit**

```bash
venv/bin/ruff check .
venv/bin/pytest tests/ -q
git add config.py calculators/crypto_fifo_tracker.py calculators/calculate_fifo.py tests/test_swap_realizes.py
git commit -m "feat(config): swap_realizes as a declared tax rule"
```

---

### Task 8: `swap_lot_transfers` — the table that carries inheritance

Under deferral, a swap does not realize: the lots of the asset given up are
consumed and their **acquisition date and cost** move to the asset received.
That crossing has to survive between two per-crypto passes of the engine, and
it has to be auditable, so it is a table and not a variable.

**Files:**
- Create: `calculators/update_fifo_schema_swap_transfers.py`
- Modify: `doc/schema.sql`
- Test: `tests/test_swap_deferral.py`

- [ ] **Step 1: Write the failing test**

```python
"""Deferral path: a swap that does not realize, and the inheritance it creates."""
import os
import re
import sqlite3
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

PROJECT_ROOT = os.path.join(os.path.dirname(__file__), '..')


def test_migration_creates_swap_lot_transfers(tmp_path):
    """Exercise the real transition: a database that does not have the table yet.

    Building the fixture from doc/schema.sql would be a false positive — the
    schema file carries the table, so CREATE TABLE IF NOT EXISTS would be a
    no-op and the migration's own SQL would never run. Task 1 shipped exactly
    that mistake; do not repeat it.
    """
    script = os.path.join(PROJECT_ROOT, 'calculators', 'update_fifo_schema_swap_transfers.py')
    schema = open(os.path.join(PROJECT_ROOT, 'doc', 'schema.sql')).read()

    # Pre-migration schema: drop the swap_lot_transfers DDL block.
    before = re.sub(r'CREATE (TABLE|INDEX)[^;]*swap_lot_transfers[^;]*;', '', schema,
                    flags=re.IGNORECASE | re.DOTALL)
    assert 'swap_lot_transfers' not in before, 'the strip pattern no longer matches the schema'

    db = str(tmp_path / 'pre_migration.db')
    conn = sqlite3.connect(db)
    try:
        conn.executescript(before)
        conn.commit()
        assert conn.execute("SELECT name FROM sqlite_master WHERE name='swap_lot_transfers'").fetchone() is None
    finally:
        conn.close()

    for _ in range(2):  # first run creates, second must be a safe no-op
        r = subprocess.run([sys.executable, script, db], capture_output=True, text=True)
        assert r.returncode == 0, r.stderr

    conn = sqlite3.connect(db)
    try:
        cols = [row[1] for row in conn.execute('PRAGMA table_info(swap_lot_transfers)')]
        idx = [row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='swap_lot_transfers'")]
        indexed = [row[2] for row in conn.execute('PRAGMA index_info(idx_swap_lot_transfers_group)')]
    finally:
        conn.close()

    assert cols == ['id', 'swap_group_id', 'from_cryptocurrency', 'to_cryptocurrency',
                    'swap_date', 'source_purchase_date', 'amount_from', 'cost_basis',
                    'created_at']
    assert 'idx_swap_lot_transfers_group' in idx
    assert indexed == ['swap_group_id'], 'the index must target the column it is named after'
```

- [ ] **Step 2: Run it and watch it fail**

Run: `venv/bin/pytest tests/test_swap_deferral.py -v`
Expected: FAIL — the script does not exist.

- [ ] **Step 3: Write the migration**

```python
#!/usr/bin/env python3
"""Create swap_lot_transfers — lot inheritance across a deferred swap.

One row per lot slice that moves from the asset given up to the asset received:
the acquisition date and cost travel, the gain does not.

Idempotent (CREATE TABLE IF NOT EXISTS). Takes the database path as argv[1].
"""
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import DATABASE_PATH

# One statement per element, executed inside the explicit transaction.
# NOT executescript(): it commits whatever transaction is open before it runs,
# so a BEGIN around it is theatre and the rollback below would save nothing.
STATEMENTS = (
    """CREATE TABLE IF NOT EXISTS swap_lot_transfers (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    swap_group_id        TEXT NOT NULL,
    from_cryptocurrency  TEXT NOT NULL,
    to_cryptocurrency    TEXT NOT NULL,
    swap_date            TEXT NOT NULL,
    source_purchase_date TEXT NOT NULL,
    amount_from          REAL NOT NULL,
    cost_basis           REAL NOT NULL,
    created_at           TEXT NOT NULL DEFAULT (datetime('now'))
)""",
    """CREATE INDEX IF NOT EXISTS idx_swap_lot_transfers_group
    ON swap_lot_transfers(swap_group_id)""",
    """CREATE INDEX IF NOT EXISTS idx_swap_lot_transfers_to
    ON swap_lot_transfers(to_cryptocurrency)""",
)


def migrate(db_path):
    conn = sqlite3.connect(db_path)
    try:
        conn.execute('BEGIN')
        for statement in STATEMENTS:
            conn.execute(statement)
        conn.commit()
        print('✓ swap_lot_transfers present')
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == '__main__':
    migrate(sys.argv[1] if len(sys.argv) > 1 else DATABASE_PATH)
```

- [ ] **Step 4: Run it and watch it pass**

Run: `venv/bin/pytest tests/test_swap_deferral.py -v`
Expected: 1 passed.

- [ ] **Step 5: Add the same DDL to `doc/schema.sql`** (the three statements from `STATEMENTS` above, each terminated with `;`), so test fixtures and a fresh install carry it. The test's strip pattern matches on `swap_lot_transfers`, so keep the table and index names on the same statement as the keyword.

- [ ] **Step 6: Run the suite and commit**

```bash
venv/bin/pytest tests/ -q
git add calculators/update_fifo_schema_swap_transfers.py doc/schema.sql tests/test_swap_deferral.py
git commit -m "feat(schema): swap_lot_transfers, lot inheritance across a deferred swap"
```

---

### Task 9: Give side — consume the lots, realize nothing, record the inheritance

**Files:**
- Modify: `calculators/crypto_fifo_tracker.py` (the query at line ~115, the SELL branch at ~169, the bulk write at ~249)
- Test: `tests/test_swap_deferral.py`

- [ ] **Step 1: Write the failing test** (append)

Task 8 removed `import pytest` from this file, because at that point nothing in
it used pytest (`tmp_path` is injected by the runner, not imported). The tests
below use `pytest.raises`, so add the import back at the top of the file before
appending.

```python
from calculators.crypto_fifo_tracker import CryptoFIFOTracker


def _tx(conn, **kw):
    """Insert one transaction; returns its id."""
    cols = ('transaction_date', 'transaction_type', 'cryptocurrency', 'amount',
            'price_per_unit', 'total_value', 'fee_amount', 'fee_currency', 'currency',
            'exchange_name', 'source', 'imported_at', 'record_hash', 'swap_group_id')
    row = {c: kw.get(c) for c in cols}
    row.setdefault('fee_amount', 0)
    cur = conn.execute(
        f"INSERT INTO transactions ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
        tuple(row[c] for c in cols))
    conn.commit()
    return cur.lastrowid


def _seed_swap(db_path):
    """Buy 1 ETH for EUR 1000 in 2020, swap it for 2000 USDT in 2024."""
    conn = sqlite3.connect(db_path)
    try:
        _tx(conn, transaction_date='2020-01-10T10:00:00+00:00', transaction_type='BUY',
            cryptocurrency='ETH', amount=1.0, price_per_unit=1000.0, total_value=1000.0,
            fee_currency='EUR', currency='EUR', exchange_name='Test',
            source='seed', imported_at='2026-01-01T00:00:00', record_hash='h1')
        _tx(conn, transaction_date='2024-04-22T15:00:00+00:00', transaction_type='SELL',
            cryptocurrency='ETH', amount=1.0, price_per_unit=3000.0, total_value=3000.0,
            fee_currency='EUR', currency='EUR', exchange_name='Test',
            source='seed', imported_at='2026-01-01T00:00:00', record_hash='h2',
            swap_group_id='swap-1')
        _tx(conn, transaction_date='2024-04-22T15:00:00+00:00', transaction_type='BUY',
            cryptocurrency='USDT', amount=2000.0, price_per_unit=1.5, total_value=3000.0,
            fee_currency='EUR', currency='EUR', exchange_name='Test',
            source='seed', imported_at='2026-01-01T00:00:00', record_hash='h3',
            swap_group_id='swap-1')
    finally:
        conn.close()


def test_deferred_swap_realizes_nothing_and_records_the_transfer(db_path, monkeypatch):
    _seed_swap(db_path)
    monkeypatch.setattr('calculators.crypto_fifo_tracker.SWAP_REALIZES', False)

    tracker = CryptoFIFOTracker(db_path)
    try:
        tracker.calculate_fifo_lots('ETH')
    finally:
        tracker.close()

    conn = sqlite3.connect(db_path)
    try:
        matches = conn.execute("SELECT COUNT(*) FROM sale_lot_matches WHERE cryptocurrency='ETH'").fetchone()[0]
        transfers = conn.execute("""SELECT swap_group_id, from_cryptocurrency, to_cryptocurrency,
                                           source_purchase_date, amount_from, cost_basis
                                    FROM swap_lot_transfers""").fetchall()
        # The JOIN is the assertion: source_fifo_lot_id must resolve to the lot
        # actually consumed. Two buys on the same day make two lots with the
        # same purchase_date, so a date alone does not identify one.
        inherited = conn.execute("""SELECT l.cryptocurrency, l.purchase_date
                                   FROM swap_lot_transfers t
                                   JOIN fifo_lots l ON l.id = t.source_fifo_lot_id""").fetchall()
        remaining = conn.execute("SELECT ROUND(SUM(remaining_amount),8) FROM fifo_lots "
                                 "WHERE cryptocurrency='ETH'").fetchone()[0]
    finally:
        conn.close()

    assert matches == 0, 'a deferred swap must not produce a realized match'
    assert transfers == [('swap-1', 'ETH', 'USDT', '2020-01-10T10:00:00+00:00', 1.0, 1000.0)]
    assert inherited == [('ETH', '2020-01-10T10:00:00+00:00')], \
        'the transfer must point at the lot it consumed, not merely carry its date'
    assert remaining == 0.0, 'the lots of the asset given up are consumed'


def test_deferred_swap_without_its_buy_leg_refuses_to_lose_the_cost_basis(db_path, monkeypatch):
    """The one way this branch could destroy a number silently.

    A SELL carrying a swap_group_id whose BUY leg was never imported: the lots
    are consumed, and under deferral there is no realized match either. Before
    this guard the row was written with an empty to_cryptocurrency, which the
    take side never queries — cost basis out of the chain, no error, wrong tax.
    """
    _seed_swap(db_path)
    conn = sqlite3.connect(db_path)
    try:  # remove the BUY leg of the swap, keep the disposal
        conn.execute("DELETE FROM transactions WHERE cryptocurrency='USDT'")
        conn.commit()
    finally:
        conn.close()
    monkeypatch.setattr('calculators.crypto_fifo_tracker.SWAP_REALIZES', False)

    tracker = CryptoFIFOTracker(db_path)
    try:
        with pytest.raises(ValueError, match='swap-1'):
            tracker.calculate_fifo_lots('ETH')
    finally:
        tracker.close()


def test_realizing_swap_still_produces_a_match(db_path):
    """With the default setting nothing changes: the swap is a disposal."""
    _seed_swap(db_path)

    tracker = CryptoFIFOTracker(db_path)
    try:
        tracker.calculate_fifo_lots('ETH')
    finally:
        tracker.close()

    conn = sqlite3.connect(db_path)
    try:
        gain = conn.execute("SELECT ROUND(SUM(gain_loss),2) FROM sale_lot_matches "
                            "WHERE cryptocurrency='ETH'").fetchone()[0]
        transfers = conn.execute('SELECT COUNT(*) FROM swap_lot_transfers').fetchone()[0]
    finally:
        conn.close()

    assert gain == 2000.0
    assert transfers == 0
```

- [ ] **Step 2: Run them and watch them fail**

Run: `venv/bin/pytest tests/test_swap_deferral.py -v`
Expected: the deferred test FAILs (`sale_lot_matches` has 1 row, `swap_lot_transfers` empty); the realizing test passes already.

- [ ] **Step 3: Select the column and the swap's other side**

In `calculate_fifo_lots`, extend the query (line ~115) with `swap_group_id`:

```python
        query = '''
            SELECT id, transaction_date, transaction_type, amount,
                   price_per_unit, total_value, fee_amount, fee_currency, exchange_name,
                   swap_group_id
            FROM transactions
            WHERE cryptocurrency = ?
            AND transaction_type IN ('BUY', 'SELL', 'DEPOSIT', 'WITHDRAWAL')
            ORDER BY transaction_date ASC, id ASC
        '''
```

and right after `transactions = self.cursor.fetchall()` add the map from a swap
group to the asset received, plus a clean slate for this crypto's transfers:

```python
        # Which asset each swap group delivers — needed on the give side, where
        # the other leg belongs to a different cryptocurrency.
        self.cursor.execute("""
            SELECT swap_group_id, cryptocurrency FROM transactions
            WHERE swap_group_id IS NOT NULL AND transaction_type = 'BUY'
        """)
        swap_takes = {row['swap_group_id']: row['cryptocurrency'] for row in self.cursor.fetchall()}

        # Idempotent per crypto, like the DELETEs above.
        self.cursor.execute('DELETE FROM swap_lot_transfers WHERE from_cryptocurrency = ?',
                            (cryptocurrency,))
        transfers_batch = []
```

- [ ] **Step 3b: Require the table instead of re-creating it**

`_ensure_fifo_tables` (line ~48) creates `fifo_lots` and `sale_lot_matches` with
`IF NOT EXISTS` on every instance: in this repo every table the engine writes is
also self-healed by the engine. `swap_lot_transfers` deliberately breaks that
pattern, and the reason is the defect the task 8 review found: two copies of the
same DDL drift apart silently, and only one of them gets tested. A third copy
inside the engine would be a third thing to drift — and this table carries
`CHECK` constraints that SQLite cannot add after the fact, so a copy that loses
one would produce a table that accepts exactly the rows the constraints exist to
refuse.

So the engine requires it rather than creating it, and only when deferral is
actually in use — someone running with the default `swap_realizes = True` never
touches this table and must not be stopped. At the top of `calculate_fifo_lots`,
before the query:

```python
        if not SWAP_REALIZES:
            exists = self.cursor.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='swap_lot_transfers'"
            ).fetchone()
            if not exists:
                raise ValueError(
                    'tax_rules.swap_realizes is False, which needs the swap_lot_transfers '
                    'table to carry lot inheritance across a deferred swap, and this '
                    'database does not have it. Run: python3 '
                    'calculators/update_fifo_schema_swap_transfers.py <db>'
                )
```

A test for it: a database built without the table, `SWAP_REALIZES` patched to
`False`, and `pytest.raises(ValueError, match='update_fifo_schema_swap_transfers')`.
Build the fixture the way task 8 does — the whole schema, then `DROP TABLE
swap_lot_transfers` — not by hand-writing a partial schema.

- [ ] **Step 4: Defer instead of realizing, inside the SELL loop**

In the SELL branch, replace the `matches_batch.append((...))` block with:

```python
                    deferred = bool(trans['swap_group_id']) and not SWAP_REALIZES
                    if deferred:
                        # No realization: the acquisition date and cost travel to
                        # the asset received (spec §7, deferral regime).
                        take_asset = swap_takes.get(trans['swap_group_id'])
                        if not take_asset:
                            raise ValueError(
                                f"Swap group {trans['swap_group_id']!r} disposes of "
                                f'{cryptocurrency} but its BUY leg was never imported. '
                                'Under deferral the cost basis of the lots consumed here '
                                'would have nowhere to land: it would leave the chain '
                                'silently, with no realized gain and no inherited lot. '
                                'Import the other leg with importers/import_non_btc_legs.py, '
                                'or set tax_rules.swap_realizes = True in config.py.'
                            )
                        transfers_batch.append((
                            trans['swap_group_id'],
                            i,                 # lot index (replaced with db_id later)
                            cryptocurrency,
                            take_asset,
                            sale_date,
                            lot['purchase_date'],
                            float(amount_from_lot),
                            _to_eur(cost_basis),
                        ))
                    else:
                        matches_batch.append((
                            trans['id'],       # sale_transaction_id
                            i,                 # lot index (replaced with db_id later)
                            sale_date,
                            lot['purchase_date'],
                            cryptocurrency,
                            float(amount_from_lot),
                            _to_price(purchase_price),
                            _to_price(sale_price),
                            _to_eur(cost_basis),
                            _to_eur(proceeds),
                            _to_eur(gain_loss),
                            holding_days,
                        ))
```

- [ ] **Step 5: Write the transfers with the lots**

In the bulk-write section, after the lots are inserted and before the matches
(so a failure rolls back together with them):

```python
        if transfers_batch:
            print(f"  Writing {len(transfers_batch):,} swap lot transfers (deferral)...")
            # Replace the lot index with the real DB id, exactly as the matches
            # above do: the pointer is what makes the inheritance auditable back
            # to the lot it consumed, and a date alone does not identify a lot.
            for transfer in transfers_batch:
                self.cursor.execute('''
                    INSERT INTO swap_lot_transfers
                    (swap_group_id, source_fifo_lot_id, from_cryptocurrency,
                     to_cryptocurrency, swap_date, source_purchase_date,
                     amount_from, cost_basis)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    transfer[0], lot_db_ids[transfer[1]],
                    transfer[2], transfer[3], transfer[4], transfer[5],
                    transfer[6], transfer[7],
                ))
```

- [ ] **Step 6: Run them and watch them pass**

Run: `venv/bin/pytest tests/test_swap_deferral.py tests/test_swap_realizes.py -v`
Expected: all passed.

- [ ] **Step 7: Commit**

```bash
git add calculators/crypto_fifo_tracker.py tests/test_swap_deferral.py
git commit -m "feat(fifo): deferred swap consumes lots without realizing, records inheritance"
```

---

### Task 10: Take side — lots that inherit date and cost

**Files:**
- Modify: `calculators/crypto_fifo_tracker.py` (BUY branch, line ~141)
- Test: `tests/test_swap_deferral.py`

- [ ] **Step 1: Write the failing test** (append)

```python
def test_received_asset_inherits_date_and_cost(db_path, monkeypatch):
    """The USDT received keeps the ETH acquisition date: the clock does not restart."""
    _seed_swap(db_path)
    monkeypatch.setattr('calculators.crypto_fifo_tracker.SWAP_REALIZES', False)

    tracker = CryptoFIFOTracker(db_path)
    try:
        tracker.calculate_fifo_lots('ETH')    # give side first
        tracker.calculate_fifo_lots('USDT')   # take side
    finally:
        tracker.close()

    conn = sqlite3.connect(db_path)
    try:
        lots = conn.execute("""SELECT purchase_date, ROUND(original_amount,8),
                                      ROUND(cost_basis,2), ROUND(purchase_price_per_unit,8)
                               FROM fifo_lots WHERE cryptocurrency='USDT'""").fetchall()
    finally:
        conn.close()

    assert lots == [('2020-01-10T10:00:00+00:00', 2000.0, 1000.0, 0.5)]


def test_two_source_lots_become_two_inherited_lots(db_path, monkeypatch):
    """One swap of two lots creates two lots, each with its own inherited date."""
    conn = sqlite3.connect(db_path)
    try:
        _tx(conn, transaction_date='2019-01-01T10:00:00+00:00', transaction_type='BUY',
            cryptocurrency='ETH', amount=0.5, price_per_unit=200.0, total_value=100.0,
            fee_currency='EUR', currency='EUR', exchange_name='Test', source='seed',
            imported_at='2026-01-01T00:00:00', record_hash='a1')
        _tx(conn, transaction_date='2021-01-01T10:00:00+00:00', transaction_type='BUY',
            cryptocurrency='ETH', amount=0.5, price_per_unit=600.0, total_value=300.0,
            fee_currency='EUR', currency='EUR', exchange_name='Test', source='seed',
            imported_at='2026-01-01T00:00:00', record_hash='a2')
        _tx(conn, transaction_date='2024-04-22T15:00:00+00:00', transaction_type='SELL',
            cryptocurrency='ETH', amount=1.0, price_per_unit=3000.0, total_value=3000.0,
            fee_currency='EUR', currency='EUR', exchange_name='Test', source='seed',
            imported_at='2026-01-01T00:00:00', record_hash='a3', swap_group_id='swap-2')
        _tx(conn, transaction_date='2024-04-22T15:00:00+00:00', transaction_type='BUY',
            cryptocurrency='USDT', amount=1000.0, price_per_unit=3.0, total_value=3000.0,
            fee_currency='EUR', currency='EUR', exchange_name='Test', source='seed',
            imported_at='2026-01-01T00:00:00', record_hash='a4', swap_group_id='swap-2')
    finally:
        conn.close()
    monkeypatch.setattr('calculators.crypto_fifo_tracker.SWAP_REALIZES', False)

    tracker = CryptoFIFOTracker(db_path)
    try:
        tracker.calculate_fifo_lots('ETH')
        tracker.calculate_fifo_lots('USDT')
    finally:
        tracker.close()

    conn = sqlite3.connect(db_path)
    try:
        lots = conn.execute("""SELECT purchase_date, ROUND(original_amount,8), ROUND(cost_basis,2)
                               FROM fifo_lots WHERE cryptocurrency='USDT'
                               ORDER BY purchase_date""").fetchall()
    finally:
        conn.close()

    # 1000 USDT split pro-rata on the ETH consumed (0.5 + 0.5), each slice keeping its cost
    assert lots == [('2019-01-01T10:00:00+00:00', 500.0, 100.0),
                    ('2021-01-01T10:00:00+00:00', 500.0, 300.0)]


def test_take_side_without_its_give_side_fails_loudly(db_path, monkeypatch):
    """Processing the received asset before the one given up is an ordering bug."""
    _seed_swap(db_path)
    monkeypatch.setattr('calculators.crypto_fifo_tracker.SWAP_REALIZES', False)

    tracker = CryptoFIFOTracker(db_path)
    try:
        with pytest.raises(ValueError, match='swap-1'):
            tracker.calculate_fifo_lots('USDT')  # give side not computed yet
    finally:
        tracker.close()
```

- [ ] **Step 2: Run them and watch them fail**

Run: `venv/bin/pytest tests/test_swap_deferral.py -k inherit -v`
Expected: FAIL — the USDT lot is still priced from its own `price_per_unit`
(`2000.0, 3000.0, 1.5` instead of the inherited values).

- [ ] **Step 3: Build inherited lots in the BUY branch**

At the start of the `if trans_type == 'BUY':` branch, before `amount = …`:

```python
            if trans_type == 'BUY' and trans['swap_group_id'] and not SWAP_REALIZES:
                # Deferral: this asset was received in a swap. It does not get a
                # new cost basis — it inherits one lot per lot consumed on the
                # other side, with that lot's acquisition date.
                self.cursor.execute('''
                    SELECT source_purchase_date, amount_from, cost_basis
                    FROM swap_lot_transfers
                    WHERE swap_group_id = ? AND to_cryptocurrency = ?
                    ORDER BY source_purchase_date ASC, id ASC
                ''', (trans['swap_group_id'], cryptocurrency))
                slices = self.cursor.fetchall()
                if not slices:
                    raise ValueError(
                        f"swap {trans['swap_group_id']}: no lot transfer recorded. "
                        f"Compute the asset given up before {cryptocurrency} — "
                        f"use swap_dependency_order()."
                    )
                received = Decimal(str(trans['amount']))
                total_from = sum(Decimal(str(s['amount_from'])) for s in slices)
                allocated = Decimal('0')
                for n, sl in enumerate(slices, 1):
                    share = (Decimal(str(sl['amount_from'])) / total_from
                             if total_from > 0 else Decimal('0'))
                    # Last slice takes the remainder: the sum must equal what arrived.
                    qty = (received - allocated) if n == len(slices) else (received * share)
                    allocated += qty
                    cost = Decimal(str(sl['cost_basis']))
                    mem_lots.append({
                        'db_id': None,
                        'purchase_transaction_id': trans['id'],
                        'cryptocurrency': cryptocurrency,
                        'purchase_date': sl['source_purchase_date'],   # inherited
                        'original_amount': qty,
                        'remaining_amount': qty,
                        'purchase_price_per_unit': (cost / qty) if qty > 0 else Decimal('0'),
                        'cost_basis': cost,                            # inherited
                        'purchase_fee_total': Decimal('0'),
                        'exchange_name': trans['exchange_name'],
                    })
                buys += 1
                continue

            if trans_type == 'BUY':
```

The inherited lots are appended in `source_purchase_date` order, which keeps
`mem_lots` sorted the way the FIFO pointer expects.

- [ ] **Step 4: Run them and watch them pass**

Run: `venv/bin/pytest tests/test_swap_deferral.py -v`
Expected: all passed (6 tests in this module).

- [ ] **Step 5: Commit**

```bash
git add calculators/crypto_fifo_tracker.py tests/test_swap_deferral.py
git commit -m "feat(fifo): received asset inherits date and cost, one lot per source lot"
```

---

### Task 11: Processing order

Deferral makes one crypto's lots depend on another's. The driver must compute
the asset given up before the asset received, and say so when that is
impossible.

**Files:**
- Modify: `calculators/crypto_fifo_tracker.py` (new module-level function)
- Modify: `calculators/calculate_fifo.py` (the crypto list at line ~49)
- Test: `tests/test_swap_deferral.py`

- [ ] **Step 1: Write the failing test** (append)

```python
from calculators.crypto_fifo_tracker import swap_dependency_order


def test_order_puts_the_asset_given_up_first(db_path):
    _seed_swap(db_path)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        order = swap_dependency_order(conn, ['USDT', 'ETH'])
    finally:
        conn.close()

    assert order.index('ETH') < order.index('USDT')


def test_order_keeps_unrelated_cryptos_and_is_stable(db_path):
    _seed_swap(db_path)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        order = swap_dependency_order(conn, ['USDT', 'BTC', 'ETH'])
    finally:
        conn.close()

    assert sorted(order) == ['BTC', 'ETH', 'USDT']
    assert order.index('ETH') < order.index('USDT')


def test_order_refuses_a_cycle(db_path):
    """A → B and B → A cannot both inherit: stop instead of picking one."""
    conn = sqlite3.connect(db_path)
    try:
        _tx(conn, transaction_date='2024-01-01T10:00:00+00:00', transaction_type='SELL',
            cryptocurrency='AAA', amount=1.0, price_per_unit=1.0, total_value=1.0,
            fee_currency='EUR', currency='EUR', exchange_name='Test', source='s',
            imported_at='2026-01-01T00:00:00', record_hash='c1', swap_group_id='s1')
        _tx(conn, transaction_date='2024-01-01T10:00:00+00:00', transaction_type='BUY',
            cryptocurrency='BBB', amount=1.0, price_per_unit=1.0, total_value=1.0,
            fee_currency='EUR', currency='EUR', exchange_name='Test', source='s',
            imported_at='2026-01-01T00:00:00', record_hash='c2', swap_group_id='s1')
        _tx(conn, transaction_date='2024-02-01T10:00:00+00:00', transaction_type='SELL',
            cryptocurrency='BBB', amount=1.0, price_per_unit=1.0, total_value=1.0,
            fee_currency='EUR', currency='EUR', exchange_name='Test', source='s',
            imported_at='2026-01-01T00:00:00', record_hash='c3', swap_group_id='s2')
        _tx(conn, transaction_date='2024-02-01T10:00:00+00:00', transaction_type='BUY',
            cryptocurrency='AAA', amount=1.0, price_per_unit=1.0, total_value=1.0,
            fee_currency='EUR', currency='EUR', exchange_name='Test', source='s',
            imported_at='2026-01-01T00:00:00', record_hash='c4', swap_group_id='s2')
    finally:
        conn.close()

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        with pytest.raises(ValueError, match='cycle'):
            swap_dependency_order(conn, ['AAA', 'BBB'])
    finally:
        conn.close()
```

- [ ] **Step 2: Run them and watch them fail**

Run: `venv/bin/pytest tests/test_swap_deferral.py -k order -v`
Expected: FAIL — `ImportError: cannot import name 'swap_dependency_order'`.

- [ ] **Step 3: Write the ordering function**

Add at module level in `calculators/crypto_fifo_tracker.py`, after
`DUST_THRESHOLD`:

```python
def swap_dependency_order(conn, cryptos):
    """Order cryptos so a swap's give side is computed before its take side.

    Only matters under deferral, where the asset received inherits the lots of
    the asset given up. Alphabetical within each layer, so the order is stable
    between runs. Raises ValueError on a cycle instead of choosing a side.
    """
    cur = conn.execute("""
        SELECT give.cryptocurrency AS give_crypto, take.cryptocurrency AS take_crypto
        FROM transactions give
        JOIN transactions take ON take.swap_group_id = give.swap_group_id
                              AND take.transaction_type = 'BUY'
        WHERE give.swap_group_id IS NOT NULL AND give.transaction_type = 'SELL'
    """)
    edges = {c: set() for c in cryptos}          # give → takes that depend on it
    indegree = {c: 0 for c in cryptos}
    for row in cur.fetchall():
        give, take = row['give_crypto'], row['take_crypto']
        if give in edges and take in indegree and take not in edges[give] and give != take:
            edges[give].add(take)
            indegree[take] += 1

    ready = sorted(c for c in cryptos if indegree[c] == 0)
    ordered = []
    while ready:
        current = ready.pop(0)
        ordered.append(current)
        for dependent in sorted(edges[current]):
            indegree[dependent] -= 1
            if indegree[dependent] == 0:
                ready.append(dependent)
        ready.sort()

    if len(ordered) != len(cryptos):
        stuck = sorted(set(cryptos) - set(ordered))
        raise ValueError(f'swap dependency cycle between {stuck}: cannot decide '
                         f'which asset inherits from which')
    return ordered
```

- [ ] **Step 4: Use it in the driver**

In `calculators/calculate_fifo.py`, after the `cryptos` list is read (line ~49)
and before the per-crypto loop:

```python
    from calculators.crypto_fifo_tracker import swap_dependency_order
    cryptos = swap_dependency_order(tracker.conn, cryptos)
    print(f"✓ Processing order: {', '.join(cryptos)}")
```

If `tracker.conn` is not exposed, use `tracker.cursor.connection`.

- [ ] **Step 5: Run them and watch them pass**

Run: `venv/bin/pytest tests/test_swap_deferral.py -v`
Expected: 9 passed.

- [ ] **Step 6: Run ruff and the whole suite, then commit**

```bash
venv/bin/ruff check .
venv/bin/pytest tests/ -q
git add calculators/crypto_fifo_tracker.py calculators/calculate_fifo.py tests/test_swap_deferral.py
git commit -m "feat(fifo): topological processing order for deferred swaps"
```

---

### Task 12: Documentation

**Files:**
- Create: `doc/compliance.md`
- Modify: `doc/en/crypto_to_crypto_guide.md` (lines 9 and 116), `doc/pt/crypto_to_crypto_guide.md` (lines 9 and 122), `doc/known_import_issues.md`, `doc/TODO.md`, `CLAUDE.md`

- [ ] **Step 1: Write `doc/compliance.md`**

```markdown
# Compliance — making an import traceable

Every row in `transactions` must be traceable to a document a third party can
read. Three layers, and nothing crosses between them.

## 1. The documents stay out of this repository

This repo is public. Statements, conversion histories and withdrawal histories
live in the operator's own archive, and their identifiers are never written
into a versioned file. `data/` is gitignored as a whole; everything personal
stays there or outside the repo.

## 2. The database carries the provenance of every row

- `source` — the file the row came from. Name imports after the document:
  `YYYY-MM[-DD]_<exchange>_<what-it-is>.csv`.
- `record_hash` — deterministic over the core fields, so a re-import of the
  same file produces the same hashes and `import_and_verify` stays idempotent.
- `swap_group_id` — the two legs of one swap.
- the `/audit` page drills from a report line to the row, its source and hash.

## 3. The local register links file to document

`data/evidence_map.md`, one row per imported file:

| import file | document in the archive | what it proves |
|---|---|---|
| `<filename>.csv` | `<archive reference>` | `<the leg or balance it evidences>` |

A leg that exists only as a PDF is transcribed into a CSV, imported with
`import_standard_csv.py`, and written up in `data/manual_entries_log.md`: what,
why, the evidence, how, the effect, what is left to check.

## 4. The tax rule is declared, never assumed in prose

`tax_rules.swap_realizes` in `config.py` says whether a crypto-to-crypto swap
realizes a gain. Its value is an assumption until a local tax professional
confirms it, and every report that depends on it prints it.
```

- [ ] **Step 2: Demote the rule in both guides**

Before writing, run `grep -rn 'doc/compliance.md\|doc/TODO.md' config.py doc/`
and reconcile **two** divergences, both left behind by task 7:

1. the three comments in `config.py` point at `doc/compliance.md`, which did not
   exist until this task — nothing resolves them at runtime, so they failed
   silently rather than loudly. Fix them if this file ended up with a different
   name;
2. the one-sentence pointers task 7 added near the top of each guide point at
   `doc/TODO.md`, because `doc/compliance.md` did not exist yet. They now point
   here. Code and guides must not point at two different files for the same
   assumption.

**And remove a caveat that this task makes false.** Task 7's pointers say the
flag is declared and printed but that *the engine does not branch on it yet
(tasks 9-11), so changing it does not change any figure*. That was true when it
was written and is the opposite of true once tasks 9-11 have landed. Check it
against the code before rewriting: `grep -rn 'SWAP_REALIZES' calculators/` must
show the engine actually branching, not just the `# noqa: F401` re-export. A
stale caveat telling a reader the setting is inert is worse than the claim it
replaced, because it invites them to leave a legal assumption unexamined.

This step is the full rewrite that replaces the "settled law" framing.

`doc/en/crypto_to_crypto_guide.md`, replace line 9:

```markdown
**Whether a crypto-to-crypto swap is a taxable event depends on your
jurisdiction**, and this tool does not decide for you: the answer is the
setting `tax_rules.swap_realizes` in `config.py` (see `doc/compliance.md`).
The default, `True`, treats every swap as a disposal of the asset given up —
the common treatment, and the one this guide describes. Ask a local tax
professional before relying on it.
```

and line 116 (stablecoin conversion):

```markdown
Same logic, under the same setting: with `swap_realizes = True` converting to a
stablecoin is a disposal of the original crypto, and the stablecoin received
has cost basis = value at the time of conversion. Buying a stablecoin **with
fiat** is not a swap at all: it is an acquisition, and selling it back is a
disposal whose result is the exchange-rate drift.
```

Mirror both in `doc/pt/crypto_to_crypto_guide.md` (lines 9 and 122), in
Portuguese.

- [ ] **Step 3: Update `doc/known_import_issues.md`**

Add to the Binance table:

```markdown
| Gambe non-BTC dei trade non importate | L'importer dei trade tiene solo il lato BTC dei pair | Risolto 2026-10-16 | `import_non_btc_legs.py` con shape `trade_history` scrive l'altra gamba, collegata da `swap_group_id` |
```

and a new section:

```markdown
### Non-BTC legs (`import_non_btc_legs.py`)

| Problema | Causa | Stato | Note |
|----------|-------|-------|------|
| Lotti stablecoin a parità 1,00 | Lotto inserito a mano senza tasso del giorno | Risolto 2026-10-16 | La valorizzazione usa il fiat effettivamente pagato sulla sede d'acquisto |
| Lotti orfani | `purchase_transaction_id` senza riga in `transactions` | Rilevato | Il passo di verifica li segnala invece di portarli avanti |
```

- [ ] **Step 4: Tick the TODO items**

In `doc/TODO.md`, mark the stablecoin and documentation items done, dated
2026-10-16, pointing at `doc/specs/2026-10-16-non-btc-legs-design.md`.

- [ ] **Step 5: Add the new files to CLAUDE.md**

In the architecture section, one line for `importers/non_btc_readers.py`,
`importers/non_btc_valuation.py`, `importers/import_non_btc_legs.py` and
`doc/compliance.md`.

- [ ] **Step 6: Commit**

```bash
git add doc/compliance.md doc/en/crypto_to_crypto_guide.md doc/pt/crypto_to_crypto_guide.md doc/known_import_issues.md doc/TODO.md CLAUDE.md
git commit -m "docs: compliance method, swap rule demoted to a declared assumption"
```

---

### Task 13: Run it on the real data

Not a code task: the operator runs it, with a backup and a regression check.
Figures stay out of this repo — they go in the local register.

- [ ] **Step 1: Back up the database**

```bash
cp data/crypto_fifo.db "data/backups/crypto_fifo.$(date +%Y%m%d-%H%M)-pre-non-btc-legs.db"
```

- [ ] **Step 2: Record the BTC baseline**

```bash
venv/bin/python - <<'PY'
import sqlite3
from config import DATABASE_PATH
c = sqlite3.connect(f'file:{DATABASE_PATH}?mode=ro', uri=True)
print(c.execute("""SELECT COUNT(*), ROUND(SUM(remaining_amount),8),
                          ROUND(SUM(cost_basis*remaining_amount/original_amount),2)
                   FROM fifo_lots WHERE cryptocurrency='BTC' AND remaining_amount>0""").fetchone())
PY
```

Write the three numbers down: lots, BTC remaining, cost basis.

- [ ] **Step 3: Migrate the schema**

```bash
venv/bin/python calculators/update_fifo_schema_swap_group.py
venv/bin/python calculators/update_fifo_schema_swap_transfers.py
```

- [ ] **Step 4: Import each export, one per shape**

```bash
venv/bin/python importers/import_non_btc_legs.py data/binance_trade_history_all.csv trade_history Binance
venv/bin/python importers/import_non_btc_legs.py data/coinbase_history.csv account_history Coinbase
venv/bin/python importers/import_non_btc_legs.py <path-to-deposit-withdrawal-export> deposit_withdrawal Bybit
```

For a leg that exists only as a PDF: transcribe it into a CSV in the
`template_manual_transactions.csv` shape, import it with
`importers/import_standard_csv.py`, and write the register entry.

- [ ] **Step 5: Recalculate and check the baseline did not move**

```bash
venv/bin/python calculators/calculate_fifo.py
```

Re-run Step 2. The three BTC numbers **must be identical**. If they moved,
restore the backup and stop: the importer touched the BTC ledger, which is the
one thing it may not do.

- [ ] **Step 6: Write the register entries**

`data/evidence_map.md` — one row per imported file, with the archive reference.
`data/manual_entries_log.md` — one entry for any transcription, with effect
(transactions before → after, lots, realized per year).

- [ ] **Step 7: Measure what the other rule would cost**

With the legs in place, run the engine under both rules and write both results
in the register — this is the number the tax professional needs in order to
answer, instead of an abstract question:

```bash
venv/bin/python calculators/calculate_fifo.py          # swap_realizes=True (profile default)
FIFO_SWAP_REALIZES=0 venv/bin/python calculators/calculate_fifo.py   # see note
```

If reading the flag from the environment is not wired, change the profile value
in `config.py`, run, record, and change it back — the run prints the rule in
force, so a recorded output can never be mistaken for the other one. Keep the
`True` run as the final state of the database.

- [ ] **Step 8: Open the questions the numbers raise**

Realized results of a past tax year have changed. That is a question for the
tax professional before any amended return, together with the one that decides
`swap_realizes`.

---

## Self-review

**Spec coverage:** §3 → Task 1. §4 readers → Tasks 2-4, importer → Task 6. §5
valuation → Task 5. §6 evidence → Task 12 (`doc/compliance.md`) and Task 13
(registers). §7 setting → Task 7 for the declaration, Tasks 8-11 for both
behaviours: the inheritance table, the give side that realizes nothing, the
take side that inherits date and cost one lot per source lot, and the
processing order that makes the dependency safe. §8 "what does not change" →
the regression tests in Tasks 6 and 13, plus `test_realizing_swap_still_produces_a_match`
in Task 9, which pins today's behaviour while the new path is added. §9 testing
→ the test steps of every task. §10 risks → Task 13 steps 5, 7 and 8.

**Placeholders:** the only angle bracket left is `<path-to-deposit-withdrawal-export>`
in Task 9, which is an operator path that cannot live in a public repo.

**Type consistency:** the normalized row keys are defined once at the top and
used verbatim in Tasks 2-5; `value_row(row, rates)` has the same signature in
Task 5 and Task 6; `rates` implements `fiat_to_eur` and `crypto_to_eur` in both
the test fake and the production `Rates`; `SOURCE_SUFFIX` is defined in Task 6
and imported by its test; `swap_lot_transfers` has the same nine columns in the
migration (Task 8), the insert (Task 9) and the select (Task 10);
`swap_dependency_order(conn, cryptos)` has the same signature in Task 11 and in
the driver patch, and both pass a connection with `row_factory = sqlite3.Row`,
which `CryptoFIFOTracker` already sets.

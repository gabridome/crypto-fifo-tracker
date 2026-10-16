# Design — Non-BTC legs: stablecoins and crypto-to-crypto swaps

> Date: 2026-10-16 · Status: approved (brainstorming) · Spec for implementation
> Read with `doc/project_guidelines.md` and `doc/code_guidelines.md`.
> No account data in this file: the repo is public (MIT). Figures and evidence
> live in the operator's own records, outside git — see §6.

## 1. Problem

The FIFO engine is already multi-currency: `calculators/calculate_fifo.py`
iterates over `SELECT DISTINCT cryptocurrency FROM transactions`. The data is
not. On a real account the gaps show up as:

- **stablecoin sales with no lot to match.** The sale is imported, the purchase
  never was, so the FIFO reports an unmatched sale and no cost basis.
- **swap legs recorded on one side only.** `import_binance_with_fees.py`
  filters the pairs `BTCEUR/BTCUSDT/BTCBUSD` (`doc/known_import_issues.md`):
  for a BTC→USDT trade the BTC disposal is recorded, the USDT acquired is not.
- **lots patched by hand at assumed parity.** A stablecoin lot entered manually
  with `purchase_price_per_unit = 1.0` against the reporting currency
  overstates cost basis whenever the exchange rate was not 1.00 — and it never
  is for a USD-pegged coin in a EUR profile.
- **orphan lots.** A lot whose `purchase_transaction_id` has no row in
  `transactions`: nothing to audit back to, nothing to recompute from.
- **a rebuild can silently drop them.** An earlier database state contained the
  stablecoin chain (deposits, sales, dust) imported from the exchange history;
  a later rebuild, run with the BTC importers only, lost it. Nothing failed and
  no report complained.

The fiat-to-stablecoin-to-fiat round trip deserves its own line, because it is
the case that looks harmless and is not: buying a stablecoin with fiat to move
liquidity between venues is **not** a crypto-to-crypto swap. It is an
acquisition of a crypto-asset and, on the way out, a disposal — held days, so
never in the long-term exemption, and the exchange-rate drift between the two
ends is a taxable result that nobody notices because "a stablecoin is worth 1".

## 2. Approved decisions

1. **Perimeter: what the active tax profile can still be asked about.** Legs
   with a tax effect in the years governed by the current crypto regime come
   in. Older assets outside that window — coins from a 2017-era fork, dead
   exchanges — stay out, with a line in `doc/` saying why, not silently.
2. **A new additive importer**, `importers/import_non_btc_legs.py`, which
   re-reads the same exchange exports and emits **only** the non-BTC rows. The
   BTC path is not touched: the `record_hash` of existing BTC rows stays
   identical, and numbers already filed in tax returns do not move.
3. **A swap is two legs carrying the same `swap_group_id`.** The disposal stays
   a `SELL` and the acquisition is added as a `BUY`, as today's engine expects;
   the shared id makes "which disposals were swaps?" a question the data can
   answer.
4. **The tax rule becomes a profile parameter**, not prose in a guide:
   `tax_rules.swap_realizes` in `config.py`. The guides stop asserting a rule
   for a jurisdiction and state the assumption with a pointer to who confirmed
   it.
5. **Evidence lives in three layers** (§6): the documents stay in the
   operator's own archive, a local gitignored register maps imported file →
   document, and this repo carries only the method.

## 3. Schema

One column, added by `calculators/update_fifo_schema.py` the way the project
already does it (idempotent, column-existence check, never a bare `ALTER`):

```sql
-- transactions
swap_group_id TEXT          -- same value on both legs of one swap; NULL otherwise
CREATE INDEX IF NOT EXISTS idx_transactions_swap_group
    ON transactions(swap_group_id);
```

Nothing else changes. `record_hash` keeps its current definition, so existing
rows keep their hashes and `import_and_verify` stays idempotent.

## 4. `importers/import_non_btc_legs.py`

CLI contract as every other importer (`doc/project_guidelines.md` §Pattern
importers):

```
python3 importers/import_non_btc_legs.py <filepath> [exchange_name]
```

It follows the mandatory pattern — `import_and_verify()`, explicit `BEGIN`,
`delete_by_source` then insert, `source` / `imported_at` / `record_hash` /
`currency` / `fee_currency` on every row.

**Distinct source tag.** The file it reads is often the same file a BTC
importer already consumed, and `import_and_verify` deletes by `source` before
inserting. The non-BTC rows are therefore written with `source =
"<filename>#non-btc"`. Without this, re-importing the legs would delete the BTC
rows of that file.

**Readers.** One small reader per export shape, each returning normalized
dicts, so a new exchange is a new function and not a new branch inside a
monolith: trade history (pair-based), account history (per-asset rows with a
type column), deposit/withdrawal history (fiat↔crypto conversions and
transfers). Readers are pure functions over an open file: no DB, no network,
unit-testable.

## 5. Valuation rules

The rules, in the order the importer applies them:

| case | cost basis / proceeds | lot? |
|---|---|---|
| crypto bought with fiat | the fiat actually paid, from the acquiring venue's record — **not** the value at arrival somewhere else. Fiat other than the reporting currency is converted with `importers/ecb_rates.py` at the day's rate, and the import aborts if that rate is missing (`doc/project_guidelines.md` §Conversione valute) | creates a lot |
| crypto received in exchange for crypto | value of the asset given up on that day: `ecb_rates.py` when the pair is quoted in a fiat currency, `importers/crypto_prices.py` when neither side is fiat. Same `swap_group_id` as the disposal | creates a lot |
| crypto sold for fiat | the fiat actually received, same conversion rule as above | consumes lots, FIFO |
| transfer between venues (same owner) | not an acquisition and not a disposal | no lot, no match |
| network fee paid in the asset moved | reduces the quantity transferred | no lot |
| quantity below `DUST_THRESHOLD` | skipped, counted in the import log | no |

Two consequences worth stating, because they are where a hand-made entry goes
wrong:

- the acquisition is valued **where it happened**. A stablecoin bought on
  venue A and withdrawn to venue B has the cost basis paid on A; the value at
  arrival on B is not a price, it is a coincidence of that day's rate.
- the quantity that creates the lot is the quantity **acquired**, while the
  quantity that arrives elsewhere is net of network fees. The two differ, and
  the difference is not a loss: it is a fee on a transfer.

## 6. Evidence and traceability

Three layers, and nothing crosses between them:

1. **The documents** — exchange statements, conversion histories, withdrawal
   histories with transaction ids — stay in the operator's own archive
   (cloud storage, data room, whatever it is). They are never copied into this
   repo and their identifiers are never written into a versioned file.
2. **The bridge** is `data/evidence_map.md`, sibling of the existing
   `data/manual_entries_log.md`; `data/` is gitignored as a whole, for the same
   reason. One row per imported file with the archive reference and what the
   document proves. The file name of an import follows the archive's naming
   convention, so `source` in the database already carries the provenance of
   every row; the map adds the link from that name to the document.
3. **The method** is `doc/compliance.md`, new and public: what makes an import
   traceable (`source`, `record_hash`, the `/audit` drill-down), the naming
   convention, the fact that the register is local, and the template for a
   register entry. No data, only the procedure.

Legs that exist only as a PDF are transcribed into a CSV by hand, imported with
`import_standard_csv.py`, and recorded in `manual_entries_log.md` with the
transcription noted — the procedure already used for a missing conversion
order.

Deliberately out of scope, and cheaper once this lands: an `evidence` column
per lot and a "source of funds per lot" report.

## 7. The tax rule as a parameter

```python
"tax_rules": {
    "exempt_holding_days": 365,
    "short_term_rate": 0.28,
    "declare_exempt": True,
    "swap_realizes": True,   # crypto→crypto realizes a gain at the swap.
                             # ASSUMPTION, not verified law: see doc/compliance.md
}
```

`swap_realizes` is read by the FIFO and by the report generators; it is never
hardcoded in prose. With `True` the behaviour is today's — swap legs are a
disposal and an acquisition, which keeps the engine consistent with figures
already filed. With `False`, the disposal legs carrying a `swap_group_id` are
excluded from realized results and the acquired asset inherits the acquisition
date and value of the asset given up, which is what a deferral regime
requires. Switching it is a **recalculation, not a re-import**.

`doc/en/crypto_to_crypto_guide.md` and `doc/pt/crypto_to_crypto_guide.md` must
stop presenting the swap rule as settled law for a named jurisdiction. They
state the assumption, point at `swap_realizes`, and say that the answer is a
question for a local tax professional.

## 8. What does not change

- Every BTC row, its `record_hash`, the open BTC lots and their cost basis.
- `calculators/calculate_fifo.py`: already multi-currency, untouched except for
  honouring `swap_realizes`.
- The existing importers, the web app, the IRS report layout.
- Assets outside the perimeter: still absent, now documented as a decision.

## 9. Testing

TDD, test before code (`doc/code_guidelines.md` §5.2). Fixtures are synthetic —
the real export shapes with invented numbers, because this repo is public.

Readers:
- each export shape parses into normalized rows; unknown columns raise instead
  of guessing;
- a non-numeric or blank quantity is dropped, not raised, and counted.

Valuation:
- fiat→crypto uses the fiat paid, not the arrival value;
- crypto→crypto values the leg with the rate of the day and links both legs;
- a transfer creates no lot;
- network fee reduces the transferred quantity without creating a loss;
- sub-dust rows are skipped.

Importer:
- idempotent: importing the same file twice leaves the database identical;
- the `#non-btc` source tag does not delete the BTC rows of the same file —
  the regression test for the one way this importer could destroy data;
- a swap writes both legs with the same `swap_group_id`.

FIFO:
- a fiat→stablecoin→fiat round trip closes the lot and produces the
  rate-drift result, with the holding period from the actual purchase;
- with `swap_realizes = False` the swap disposals leave realized results and
  the acquired lot inherits date and value;
- an orphan lot (no `purchase_transaction_id` row) is reported by the
  verification step instead of being silently carried.

Regression, on the real database before and after the import: open BTC lots,
BTC remaining and BTC cost basis must be **unchanged** — the import adds
non-BTC rows and must not move a satoshi of the BTC ledger.

## 10. Risks

- **The import changes a filed year.** Adding stablecoin disposals changes the
  realized result of a past tax year. That is the point, but it is a question
  for the tax professional before any amended return, not a silent correction.
- **A manual transcription from a PDF can be wrong.** Mitigated by the register
  entry, by the cross-check against the counterparty leg (the net quantity of
  one leg must equal the gross of the next, minus documented fees), and by a
  test that the chain closes.
- **`swap_realizes` makes the engine's output depend on a setting.** Every
  report that uses it prints its value, so a number can never be read without
  the rule that produced it.

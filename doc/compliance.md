# Compliance — making an import traceable

Every row in `transactions` must be traceable to a document a third party can
read. Three layers, and nothing crosses between them.

This file describes the **method** only. It carries no amounts, no quantities,
no account identifiers, no row counts and no names — by design, because it is
the layer that is public.

---

## 1. The documents stay out of this repository

This repo is public (MIT). Statements, conversion histories and withdrawal
histories live in the operator's own archive, and their identifiers are never
written into a versioned file. `data/` is gitignored as a whole; everything
personal stays there or outside the repo.

That rule has no exceptions for convenience: not in a fixture, not in a test,
not in a comment. Where a real identifier would make a comment clearer, a
placeholder goes in instead — the code never depends on the value.

## 2. The database carries the provenance of every row

- **`source`** — the file the row came from. Name imports after the document:
  `YYYY-MM[-DD]_<exchange>_<what-it-is>.csv`. A re-import deletes by `source`
  and inserts again, so the name is also the unit of rollback.
- **`record_hash`** — a SHA256 over the normalised core fields
  (`source | date | type | exchange | crypto | amount | value | fee`). It is
  deterministic, so re-importing the same file produces the same hashes and
  `import_and_verify()` stays idempotent; and it lets a reviewer recompute the
  hash from the document and check that the stored row never moved.
- **`imported_at`** — when the row entered the database.
- **`swap_group_id`** — the two legs of one swap, so a disposal and the
  acquisition it paid for can be read as one operation.
- The **`/audit`** page drills from a report line down to the row, its `source`
  and its `record_hash`.

### What the audit trail does not yet cover

Two limits, both open and both recorded in `doc/TODO.md`. They are stated here
because an audit trail presented as complete is worse than one whose edges are
known:

- `/audit` joins `sale_lot_matches`, so it shows **realized** disposals. Under
  `tax_rules.swap_realizes = False` (below) a swap disposal realizes nothing
  and writes no row there, so it does not appear on that page at all.
- For a lot that **inherited** its date and cost across a deferred swap, the
  drill-down reaches the swap's acquisition leg, not the purchase the cost
  actually comes from. The link to that purchase exists in
  `swap_lot_transfers.source_fifo_lot_id`; no query in the project follows it
  yet.

## 3. The local register links file to document

The bridge between layer 1 and layer 2 is a register at `data/evidence_map.md`,
alongside `data/manual_entries_log.md`. It is **local and gitignored**, for the
same reason the documents are: it names them.

One row per imported file:

| import file | document in the archive | what it proves |
|---|---|---|
| `<filename>.csv` | `<archive reference>` | `<the leg or balance it evidences>` |

A leg that exists only as a PDF is transcribed into a CSV by hand, imported
with `import_standard_csv.py`, and written up in `data/manual_entries_log.md`:
what, why, the evidence, how, the effect, and what is left to check. A
transcription is the weakest link in the chain, so it is the one that gets
written down in full.

## 4. The tax rule is declared, never assumed in prose

`tax_rules.swap_realizes` in `config.py` says whether a crypto-to-crypto swap
realizes a gain. **Its value is an assumption until a local tax professional
confirms it for the jurisdiction in question**, and every report that depends on
it prints it (`config.tax_rule_banner()`, printed by `calculate_fifo.py`,
`generate_annual_summary.py` and `generate_irs_report.py`).

A missing declaration is an error, not a default: a country profile that does
not state the rule fails at import rather than inheriting someone else's tax
treatment.

Switching the value is a **recalculation, not a re-import**. It does not change
the totals over the whole history — it changes the **dates**, and the dates
decide which gains clear a holding-period exemption. So a change of this
setting is a change to the figures of individual years, which is a question for
the tax professional before any amended return.

The banner names the setting **configured when the report is printed** — which
is not necessarily the rule that produced the figures on record. It therefore
checks. Handed the report's database connection, it compares the setting against
three kinds of positive evidence in the data: a deferred swap disposal and a lot
that inherited its acquisition date and cost can only come from a `False` run, a
realized swap disposal only from a `True` one. Where any of them contradicts the
setting, it says so in a block that cannot be skimmed past, names which evidence
fired and how much of the data is affected, and gives the two ways out —
recalculate, or read the figures under the rule they were born under. A report
that prints that block is not fit to file from.

What a clean line establishes, and no more: that no disposal and no inherited lot
**on record** contradicts the setting named. Two limits, both stated in the code
and recorded in `doc/TODO.md`: a swap made on the same day as the purchase it
consumed **whose give side has already been recalculated under the other rule**
— same-day alone still leaves the give-side traces — and a database older than
the `swap_group_id` migration, where nothing is identifiable as a swap leg at
all, so the line is clean for want of evidence.

### What `sale_lot_matches` is, and is not

It is the register of **realized gains**, not the register of disposals. A
deferred swap disposal consumes lots and writes no row in it — correctly,
because it is not a taxable event. Any figure derived from that table and read
as "how much was disposed of" is therefore short by exactly those rows, while
any figure derived from `transactions` includes them at full value.

`calculators/swap_reporting.py` holds the one definition of that difference, so
that a report can say it rather than let a reader discover it. The readers still
affected, and what each of them under-reports, are listed in `doc/TODO.md`.

#!/usr/bin/env python3
"""
Rebuild the database from the exports in `data/`, in one command.

This is the script `calculators/calculate_fifo.py` has always told you to run.
Until 2026-10-19 it did not exist: the recipe — which export goes through which
importer, with which exchange label, and in which order — lived in the web UI's
detection table and in whoever happened to remember the two post-import steps.
That made the database an artefact nobody could reproduce. It is reproducible:
measured on 2026-10-19, a rebuild from `data/` alone returns the same lots, the
same sale matches and the same figures for every tax year, down to the cent.
See `doc/rebuild.md` for the proof and for the one documented difference
(`fee_currency`).

The chain has four phases, and the order is not cosmetic:

  1. the 22 exchange exports            → `transactions` (BTC and fiat legs)
  2. the non-BTC legs of three exports  → the stablecoin rows the BTC
                                          importers drop by design
  3. `swap_group_id` on the counterparty legs already written
  4. FIFO lots and sale matches

Usage:
    python3 import_all_data.py                      # into the configured DB
    python3 import_all_data.py --db /tmp/new.db     # into a new database
    python3 import_all_data.py --dry-run            # print the plan, run nothing
    python3 import_all_data.py --no-fifo            # stop after phase 3
    python3 import_all_data.py --reimport           # allow a non-empty database

Then verify against a known-good database:
    python3 verify_rebuild.py data/crypto_fifo.db /tmp/new.db
"""

import os
import shlex
import subprocess
import sys
from collections import namedtuple

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, PROJECT_ROOT)

from config import DATABASE_PATH  # noqa: E402

Step = namedtuple('Step', 'filename importer exchange')
Leg = namedtuple('Leg', 'filename shape exchange')
Link = namedtuple('Link', 'filename shape exchange counterparty_source')
PlanStep = namedtuple('PlanStep', 'label argv env')

# ── Phase 1: the exchange exports ────────────────────────────────────────────
# Alphabetical by filename. The order within this phase does not change any
# figure — the FIFO engine reads by transaction date, not by insertion order —
# but a fixed order keeps the row ids stable between two rebuilds, which makes
# two rebuilds diffable and not merely equivalent.
#
# The exchange label is the third argument of the importer contract
# (`doc/project_guidelines.md` §Pattern importers). For the files in the
# standard shape the label inside the file wins: that is why two of them had to
# be corrected in the file itself (`doc/rebuild.md` §Etichette).
IMPORTS = [
    Step('binance_card.csv',              'importers/import_binance_card.py',        'Binance Card'),
    Step('binance_convert_2024.csv',      'importers/import_standard_csv.py',        'Binance'),
    Step('binance_convert_legs_2024.csv', 'importers/import_standard_csv.py',        'Binance'),
    Step('binance_otc_2024.csv',          'importers/import_standard_csv.py',        'Binance OTC'),
    Step('binance_trade_history_all.csv', 'importers/import_binance_with_fees.py',   'Binance'),
    Step('bitfinex_trades.csv',           'importers/import_bitfinex_ecb.py',        'Bitfinex'),
    Step('bitstamp_history.csv',          'importers/import_bitstamp_with_fees.py',  'Bitstamp'),
    Step('bybit_uta_2024.csv',            'importers/import_bybit.py',               'Bybit'),
    Step('changely_trades.csv',           'importers/import_standard_csv.py',        'changely'),
    Step('coinbase_history.csv',          'importers/import_coinbase_standalone.py', 'Coinbase'),
    Step('coinbaseprime_orders.csv',      'importers/import_coinbase_prime.py',      'Coinbase Prime'),
    Step('coinpal.csv',                   'importers/import_standard_csv.py',        'Coinpal'),
    Step('gdtre.csv',                     'importers/import_standard_csv.py',        'GDTRE'),
    Step('inheritance_2024.csv',          'importers/import_standard_csv.py',        'Inheritance'),
    Step('kraken_ledgers.csv',            'importers/import_kraken_with_fees.py',    'Kraken'),
    Step('mtgox.csv',                     'importers/import_mtgox_with_fees.py',     'Mt.Gox'),
    Step('otc_2017.csv',                  'importers/import_standard_csv.py',        'OTC'),
    Step('revolut_crypto.csv',            'importers/import_revolut.py',             'Revolut'),
    Step('trt.csv',                       'importers/import_trt_with_fees.py',       'TRT'),
    Step('wirex_2023.csv',                'importers/import_wirex.py',               'Wirex'),
    Step('wirex_2024.csv',                'importers/import_wirex.py',               'Wirex'),
    Step('wirex_2025.csv',                'importers/import_wirex.py',               'Wirex'),
]

# ── Phase 2: the non-BTC legs ────────────────────────────────────────────────
# The BTC importers filter the stablecoin side of a trade by design
# (`doc/known_import_issues.md`), so a BTC→USDT sale entered the database as a
# BTC disposal with no USDT acquisition. These three readers put the other leg
# on record. Two of the three files are *not* imported in phase 1 on purpose:
# their BTC rows are already in the full export of the same exchange, and
# importing them again would double-count real disposals.
NON_BTC_LEGS = [
    Leg('binance_trade_history_2024.csv',    'trade_history',      'Binance'),
    Leg('coinbase_history_jun2024.csv',      'account_history',    'Coinbase'),
    Leg('bybit_deposit_withdrawal_2024.csv', 'deposit_withdrawal', 'Bybit'),
]

# ── Phase 3: link the two legs of a swap ─────────────────────────────────────
# Only Binance needs it, and the measurement says why: that export is the only
# one holding crypto-to-crypto rows whose disposal leg was written by the BTC
# importer without a `swap_group_id`. The other two have no counterparty leg to
# link.
LINKS = [
    Link('binance_trade_history_2024.csv', 'trade_history', 'Binance',
         'binance_trade_history_all.csv'),
]

# Not exports, but the import dies halfway without them: the ECB cache feeds
# every USD conversion, the price cache values the crypto-to-crypto legs.
RATE_FILES = ['eurusd.csv', 'crypto_prices.csv']

# Gli script dei passi 2 e 3 non sono nominati in IMPORTS, quindi un test che
# controlla solo quelli non li copre: se mancassero, la catena morirebbe a
# ricostruzione iniziata.
EXTRA_SCRIPTS = ['importers/import_non_btc_legs.py',
                 'importers/link_swap_counterparty_legs.py']


def missing_scripts():
    """Gli script della catena che non sono al loro posto nel repo."""
    return sorted(p for p in ([s.importer for s in IMPORTS] + EXTRA_SCRIPTS)
                  if not os.path.exists(os.path.join(PROJECT_ROOT, p)))


def missing_inputs(data_dir):
    """Every file the chain needs and cannot find, sorted. Empty list is good.

    Checked before anything is written: a rebuild that stops partway leaves a
    database that looks populated and is not.

    Caveat on `data_dir`, measured on 2026-10-19: this function looks for the
    two rate caches **in `data_dir`**, but the importers read them from a
    hardcoded `<repo>/data/` (`importers/import_standard_csv.py:57,68` and the
    other USD importers). So pointing `--data-dir` elsewhere redirects the
    exports and NOT the rates. `main()` says so out loud rather than letting
    the operator believe the rebuild was isolated; threading the directory into
    the importers is in `doc/TODO.md`.
    """
    needed = (
        [s.filename for s in IMPORTS]
        + [leg.filename for leg in NON_BTC_LEGS]
        + [link.filename for link in LINKS]
        + RATE_FILES
    )
    return sorted({n for n in needed
                   if not os.path.exists(os.path.join(data_dir, n))})


def build_plan(db_path, data_dir):
    """The whole chain as a list of commands, in the order they must run.

    Every step carries `FIFO_DB` in its environment because the importers read
    the database from `config.DATABASE_PATH`, which honours that variable. The
    link step takes it as `--db` instead, so it gets both.
    """
    env = dict(os.environ)
    env['FIFO_DB'] = db_path
    # Durante una ricostruzione le cache dei tassi sono INGRESSI, non cache:
    # `importers/ecb_rates.py` con auto_fetch attivo chiama l'API Frankfurter
    # quando il file e' stantio e **riscrive `data/eurusd.csv`**. Una
    # ricostruzione che riscrive uno dei file che non si rigenerano
    # contraddirebbe tutto cio' per cui questo script esiste, e legherebbe il
    # risultato al calendario e alla rete invece che al contenuto di `data/`.
    env['ECB_AUTO_FETCH'] = '0'
    python = sys.executable
    plan = []

    for step in IMPORTS:
        plan.append(PlanStep(
            label=f'import {step.filename}',
            argv=[python, os.path.join(PROJECT_ROOT, step.importer),
                  os.path.join(data_dir, step.filename), step.exchange],
            env=env,
        ))

    for leg in NON_BTC_LEGS:
        plan.append(PlanStep(
            label=f'gambe non-BTC {leg.filename}',
            argv=[python, os.path.join(PROJECT_ROOT, 'importers/import_non_btc_legs.py'),
                  os.path.join(data_dir, leg.filename), leg.shape, leg.exchange],
            env=env,
        ))

    for link in LINKS:
        plan.append(PlanStep(
            label=f'link swap_group_id {link.filename}',
            argv=[python, os.path.join(PROJECT_ROOT, 'importers/link_swap_counterparty_legs.py'),
                  os.path.join(data_dir, link.filename), link.shape, link.exchange,
                  link.counterparty_source, '--db', db_path],
            env=env,
        ))

    return plan


def ensure_schema(db_path):
    """Create the schema from `doc/schema.sql` when the database has none.

    `doc/schema.sql` is the single source (`doc/project_guidelines.md` §Schema
    DB); this function never alters an existing table.
    """
    import sqlite3

    os.makedirs(os.path.dirname(os.path.abspath(db_path)) or '.', exist_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        had = conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' "
            "AND name='transactions'").fetchone()[0]
        # Applicato SEMPRE, non solo su un database vuoto: `doc/schema.sql` è
        # tutto `CREATE ... IF NOT EXISTS` (code_guidelines §4.4), quindi
        # rieseguirlo non tocca una tabella che c'è già — ma crea quelle che
        # mancano. Uscire presto perché `transactions` esiste lasciava un
        # database privo di `swap_lot_transfers` o degli indici, che poi moriva
        # a metà catena.
        with open(os.path.join(PROJECT_ROOT, 'doc', 'schema.sql')) as f:
            conn.executescript(f.read())
        conn.commit()
        return not had
    finally:
        conn.close()


def row_count(db_path, table='transactions'):
    """Rows in `table`, or 0 when the database or the table is not there."""
    import sqlite3

    if not os.path.exists(db_path):
        return 0
    conn = sqlite3.connect(f'file:{db_path}?mode=ro', uri=True)
    try:
        has = conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name=?",
            (table,)).fetchone()[0]
        if not has:
            return 0
        return conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
    finally:
        conn.close()


def _parse_args(argv):
    opts = {'db': None, 'data_dir': None, 'dry_run': False,
            'fifo': True, 'reimport': False}
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == '--db':
            if i + 1 >= len(argv):
                raise SystemExit('--db richiede un percorso')
            opts['db'] = argv[i + 1]
            i += 2
        elif a == '--data-dir':
            if i + 1 >= len(argv):
                raise SystemExit('--data-dir richiede un percorso')
            opts['data_dir'] = argv[i + 1]
            i += 2
        elif a == '--dry-run':
            opts['dry_run'] = True
            i += 1
        elif a == '--no-fifo':
            opts['fifo'] = False
            i += 1
        elif a == '--reimport':
            opts['reimport'] = True
            i += 1
        elif a in ('-h', '--help'):
            print(__doc__)
            raise SystemExit(0)
        else:
            raise SystemExit(f'argomento sconosciuto: {a}\n{__doc__}')
    return opts


def main(argv=None):
    opts = _parse_args(argv if argv is not None else sys.argv[1:])
    db_path = os.path.abspath(opts['db'] or DATABASE_PATH)
    data_dir = os.path.abspath(opts['data_dir'] or os.path.join(PROJECT_ROOT, 'data'))

    print('=' * 70)
    print('RICOSTRUZIONE DEL DATABASE DAI FILE IN data/')
    print('=' * 70)
    print(f'  database:  {db_path}')
    print(f'  esportati: {data_dir}')

    gone = missing_scripts()
    if gone:
        print(f'\n✗ {len(gone)} script della catena non sono al loro posto:')
        for name in gone:
            print(f'    {name}')
        return 1

    repo_data = os.path.join(PROJECT_ROOT, 'data')
    if os.path.abspath(data_dir) != os.path.abspath(repo_data):
        print('\n  ⚠ --data-dir redirige gli EXPORT ma non le due cache dei tassi:')
        print(f'    gli importer leggono eurusd.csv e crypto_prices.csv da {repo_data}')
        print('    comunque. La ricostruzione NON è isolata da quella cartella.')

    missing = missing_inputs(data_dir)
    if missing:
        print(f'\n✗ {len(missing)} file mancanti in {data_dir}:')
        for name in missing:
            print(f'    {name}')
        print('\nNiente è stato scritto. Un import che si ferma a metà lascia un '
              'database che sembra pieno e non lo è.')
        return 1
    print(f'  ingressi:  {len(IMPORTS)} export + {len(NON_BTC_LEGS)} gambe non-BTC '
          f'+ {len(RATE_FILES)} file di tassi — tutti presenti')

    plan = build_plan(db_path, data_dir)

    if opts['dry_run']:
        print(f'\n--dry-run: {len(plan)} passi, nessuno eseguito\n')
        for n, step in enumerate(plan, 1):
            print(f'  {n:2}. {step.label}')
            print(f'      {shlex.join(step.argv[1:])}')
        print(f'  {len(plan) + 1:2}. calcolo FIFO'
              if opts['fifo'] else '      (FIFO escluso da --no-fifo)')
        return 0

    existing = row_count(db_path) + row_count(db_path, 'fifo_lots')
    if existing and not opts['reimport']:
        print(f'\n✗ {db_path} contiene già {row_count(db_path):,} transazioni e '
              f'{row_count(db_path, "fifo_lots"):,} lotti.')
        print('  Gli importer cancellano per `source` e reinseriscono, quindi '
              'rieseguire è\n  sicuro riga per riga — ma su un database vivo è una '
              'decisione, non un\n  dettaglio. Per procedere: --reimport. Per una '
              'ricostruzione pulita: --db su un\n  percorso nuovo.')
        return 2

    if ensure_schema(db_path):
        print('  schema:    creato da doc/schema.sql')
    else:
        print('  schema:    già presente, lasciato com\'è')

    failures = []
    total_warnings = 0
    for n, step in enumerate(plan, 1):
        print(f'\n[{n}/{len(plan)}] {step.label}')
        result = subprocess.run(step.argv, env=step.env, cwd=PROJECT_ROOT,
                                capture_output=True, text=True)
        if result.returncode == 0:
            # Gli avvisi NON si perdono sul percorso di successo. `ecb_rates.py`
            # stampa «⚠️ ECB rate for <data>: nearest is <altra data>» e
            # restituisce il tasso di un altro giorno con exit 0; prima finiva
            # tutto sotto un «✓ ok». Per uno strumento che serve a fidarsi di
            # cifre che vanno in una dichiarazione, un cambio preso da tre
            # settimane prima non può diventare invisibile proprio nel momento
            # in cui la ricostruzione si dichiara riproducibile.
            warnings = [ln for ln in (result.stdout + result.stderr).splitlines()
                        if '⚠' in ln]
            print(f'    ✓ ok — {row_count(db_path):,} transazioni in totale')
            for ln in warnings[:5]:
                print(f'      {ln.strip()}')
            if len(warnings) > 5:
                print(f'      … e altri {len(warnings) - 5} avvisi')
            total_warnings += len(warnings)
        else:
            failures.append(step.label)
            print(f'    ✗ exit {result.returncode}')
            for line in (result.stdout + result.stderr).strip().splitlines()[-8:]:
                print(f'      {line}')
            break   # fermarsi al primo errore: i passi successivi dipendono da questo

    if failures:
        print('\n' + '=' * 70)
        print(f'✗ RICOSTRUZIONE INTERROTTA su: {failures[0]}')
        print('Il database è incompleto. Correggi l\'errore qui sopra e rilancia; '
              'gli importer\ncancellano per `source`, quindi ripartire da zero non '
              'duplica nulla.')
        return 1

    if not opts['fifo']:
        print('\n' + '=' * 70)
        print('✓ Import completati. FIFO escluso da --no-fifo: le tabelle '
              '`fifo_lots` e\n  `sale_lot_matches` sono ancora quelle di prima (o '
              'vuote). Prossimo passo:')
        print(f'    FIFO_DB={db_path} python3 calculators/calculate_fifo.py')
        return 0

    print('\n' + '=' * 70)
    print(f'[{len(plan) + 1}/{len(plan) + 1}] calcolo FIFO')
    print('=' * 70)
    result = subprocess.run(
        [sys.executable, os.path.join(PROJECT_ROOT, 'calculators/calculate_fifo.py')],
        env=plan[0].env, cwd=PROJECT_ROOT)
    if result.returncode != 0:
        print('\n✗ Il calcolo FIFO è fallito. Le transazioni sono importate, i '
              'lotti no.')
        return 1

    print('\n' + '=' * 70)
    print('✓ RICOSTRUZIONE COMPLETATA')
    print('=' * 70)
    if total_warnings:
        print(f'  ⚠ {total_warnings} avvisi dagli importer durante la catena '
              '(riportati sopra, passo per passo; il passo FIFO stampa i suoi '
              'direttamente\n    e non sono conteggiati qui):')
        print('    vanno guardati prima di usare queste cifre — un tasso preso '
              'da un altro\n    giorno è un avviso, non un errore.')
    print('  transazioni      ', f'{row_count(db_path):,}')
    print('  lotti FIFO       ', f'{row_count(db_path, "fifo_lots"):,}')
    print('  match di vendita ', f'{row_count(db_path, "sale_lot_matches"):,}')
    print('\nPer dimostrare che è identico a un database di riferimento:')
    print(f'    python3 verify_rebuild.py <riferimento.db> {db_path}')
    return 0


if __name__ == '__main__':
    sys.exit(main())

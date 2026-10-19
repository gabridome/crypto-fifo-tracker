"""The orchestrator's recipe: what gets imported, in which order, from where.

These tests pin the *recipe*, not the data. `data/` is gitignored, so a test
that asserted the real exports exist would fail on any other clone; the input
check is exercised against a temporary directory instead.
"""
import os
import sys

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from import_all_data import (  # noqa: E402
    EXTRA_SCRIPTS,
    IMPORTS,
    LINKS,
    NON_BTC_LEGS,
    build_plan,
    missing_inputs,
    missing_scripts,
)


def test_every_importer_in_the_recipe_exists_in_the_repo():
    """A recipe naming a script that is not there fails halfway through a
    rebuild, with part of the database already written."""
    for step in IMPORTS:
        path = os.path.join(PROJECT_ROOT, step.importer)
        assert os.path.exists(path), f'{step.filename}: manca {step.importer}'


def test_no_file_is_imported_twice():
    """Importers delete by source before inserting, so a double entry would not
    duplicate rows — it would silently make the second entry's exchange label
    win. The recipe must say once what it means once."""
    names = [s.filename for s in IMPORTS]
    assert len(names) == len(set(names)), f'duplicati: {sorted(names)}'


def test_the_non_btc_exports_are_not_also_imported_as_btc():
    """`binance_trade_history_2024.csv` and `coinbase_history_jun2024.csv` hold
    trades already covered by the full exports: only their non-BTC legs are
    wanted. Importing them again as BTC would double-count real disposals."""
    btc_files = {s.filename for s in IMPORTS}
    for leg in NON_BTC_LEGS:
        assert leg.filename not in btc_files, (
            f'{leg.filename} è sia fra gli import BTC sia fra le gambe non-BTC')


def test_non_btc_shapes_are_readers_that_exist():
    from importers.import_non_btc_legs import READERS

    for leg in NON_BTC_LEGS:
        assert leg.shape in READERS, (
            f'{leg.filename}: forma {leg.shape!r} sconosciuta, '
            f'attese {sorted(READERS)}')


def test_every_link_has_its_counterparty_among_the_imports():
    """The link step writes `swap_group_id` on rows another importer wrote. If
    the counterparty export is not imported, the step finds nothing and leaves
    the swap legs unlinked — with no error."""
    imported = {s.filename for s in IMPORTS}
    for link in LINKS:
        assert link.counterparty_source in imported, (
            f'{link.filename}: controparte {link.counterparty_source} '
            'non è importata dalla ricetta')


def test_missing_inputs_names_every_absent_file(tmp_path):
    data = tmp_path / 'data'
    data.mkdir()
    (data / IMPORTS[0].filename).write_text('x')

    missing = missing_inputs(str(data))

    assert IMPORTS[0].filename not in missing
    assert IMPORTS[1].filename in missing
    assert NON_BTC_LEGS[0].filename in missing
    # eurusd.csv e crypto_prices.csv non sono export ma servono alla
    # valorizzazione: senza di loro l'import muore a metà.
    assert 'eurusd.csv' in missing
    assert 'crypto_prices.csv' in missing


def test_missing_inputs_is_empty_when_everything_is_there(tmp_path):
    data = tmp_path / 'data'
    data.mkdir()
    for name in missing_inputs(str(data)):
        (data / name).write_text('x')

    assert missing_inputs(str(data)) == []


def test_plan_runs_imports_then_legs_then_link():
    """Order is not cosmetic: the legs need the BTC rows on record to value
    them, and the link needs both legs already written."""
    plan = build_plan(db_path='/tmp/x.db', data_dir='data')
    labels = [p.label for p in plan]

    last_import = max(i for i, s in enumerate(labels) if s.startswith('import'))
    first_leg = min(i for i, s in enumerate(labels) if s.startswith('gambe'))
    first_link = min(i for i, s in enumerate(labels) if s.startswith('link'))

    assert last_import < first_leg < first_link


def test_plan_points_every_step_at_the_given_database():
    """A step that forgets the database writes into the configured one — which
    during a rebuild is the production database."""
    plan = build_plan(db_path='/tmp/rebuild.db', data_dir='data')

    assert plan, 'piano vuoto'
    for step in plan:
        assert step.env.get('FIFO_DB') == '/tmp/rebuild.db', (
            f'{step.label}: FIFO_DB non impostato')

    link = [s for s in plan if s.label.startswith('link')][0]
    assert '--db' in link.argv and '/tmp/rebuild.db' in link.argv, (
        'lo script di link prende il database da --db, non da FIFO_DB')


def test_plan_file_paths_are_under_the_given_data_dir():
    """Only the files the step READS are paths. `counterparty_source` is not a
    path at all: it is the value stored in `transactions.source`, matched
    against the database, and prefixing it with a directory would make the link
    step find nothing."""
    plan = build_plan(db_path='/tmp/x.db', data_dir='/altro/posto')
    counterparty_values = {link.counterparty_source for link in LINKS}

    for step in plan:
        for arg in step.argv:
            if not arg.endswith('.csv') or arg in counterparty_values:
                continue
            assert arg.startswith('/altro/posto'), f'{step.label}: {arg}'

    link = [s for s in plan if s.label.startswith('link')][0]
    assert 'binance_trade_history_all.csv' in link.argv, (
        'il source della controparte va passato nudo, come sta nel database')


def test_recipe_covers_the_exchanges_the_tracker_knows():
    """Every exchange label in the recipe must be one the web app's detection
    table can produce, so the UI and the orchestrator agree on naming."""
    sys.path.insert(0, os.path.join(PROJECT_ROOT, 'web'))
    from web.app import EXCHANGE_PATTERNS

    known = {label for _, label, _ in EXCHANGE_PATTERNS}
    # Binance OTC e OTC sono etichette della ricetta: il file le porta dentro
    # (v. doc/rebuild.md §Etichette), quindi devono esistere anche nella
    # tabella della UI, altrimenti le due strade danno nomi diversi.
    for step in IMPORTS:
        assert step.exchange in known, (
            f'{step.filename}: etichetta {step.exchange!r} assente da '
            'EXCHANGE_PATTERNS in web/app.py')


@pytest.mark.parametrize('step', IMPORTS, ids=lambda s: s.filename)
def test_each_import_step_has_a_non_empty_exchange_label(step):
    assert step.exchange and step.exchange.strip()


def test_the_scripts_of_phases_2_and_3_are_checked_too():
    """`IMPORTS` non nomina gli script delle fasi 2 e 3, quindi un test che
    guarda solo quelli non li copre: se mancassero, la catena morirebbe a
    ricostruzione iniziata. (Rilievo della review del 2026-10-19.)"""
    for path in EXTRA_SCRIPTS:
        assert os.path.exists(os.path.join(PROJECT_ROOT, path)), path
    assert missing_scripts() == [], missing_scripts()


def test_the_plan_disables_the_ecb_fetch():
    """Durante una ricostruzione le cache dei tassi sono ingressi, non cache:
    `importers/ecb_rates.py` con auto_fetch attivo riscrive `data/eurusd.csv`
    quando lo trova stantio. Una ricostruzione che riscrive un file che non si
    rigenera contraddice lo scopo di questo script, e lega il risultato al
    calendario e alla rete invece che al contenuto di `data/`.
    """
    plan = build_plan(db_path='/tmp/x.db', data_dir='data')

    assert plan
    for step in plan:
        assert step.env.get('ECB_AUTO_FETCH') == '0', step.label


def test_ecb_rates_honours_the_environment_switch(monkeypatch):
    """E il modulo deve davvero leggerla, altrimenti il piano la imposta a
    vuoto. Un argomento esplicito vince sull'ambiente."""
    from importers.ecb_rates import ECBRates

    monkeypatch.setenv('ECB_AUTO_FETCH', '0')
    calls = []
    monkeypatch.setattr(ECBRates, '_update_from_api',
                        lambda self, *a, **k: calls.append('fetch'))
    monkeypatch.setattr(ECBRates, '_is_stale', lambda self: True)

    ECBRates(os.path.join(PROJECT_ROOT, 'data', 'eurusd.csv'))
    assert calls == [], 'ha chiamato la rete con ECB_AUTO_FETCH=0'

    ECBRates(os.path.join(PROJECT_ROOT, 'data', 'eurusd.csv'), auto_fetch=True)
    assert calls == ['fetch'], 'un auto_fetch esplicito deve vincere sull\'ambiente'

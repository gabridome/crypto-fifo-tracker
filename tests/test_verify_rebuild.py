"""`verify_rebuild.py`: dimostra che due database dicono le stesse cifre.

Il punto di questi test e' che lo strumento **non** diventi un timbro: deve
accettare la sola differenza nota e spiegata (`fee_currency` vuota nelle righe
importate da versioni vecchie, riempita dal default dello schema) e bocciare
tutto il resto, comprese le varianti vicine di quella stessa differenza.
"""
import os
import sqlite3
import sys

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from verify_rebuild import compare  # noqa: E402

SCHEMA = os.path.join(PROJECT_ROOT, 'doc', 'schema.sql')


def _make_db(path, fee_currency='EUR', amount=1.5, extra_legacy_column=False):
    conn = sqlite3.connect(path)
    with open(SCHEMA) as f:
        conn.executescript(f.read())
    if extra_legacy_column:
        conn.execute('ALTER TABLE transactions ADD COLUMN trade_pair TEXT')
    conn.execute(
        """INSERT INTO transactions
           (transaction_date, transaction_type, cryptocurrency, amount,
            price_per_unit, total_value, fee_amount, fee_currency, currency,
            exchange_name, source, record_hash, imported_at)
           VALUES ('2024-01-10T00:00:00+00:00','BUY','BTC',?,40000,60000,0,?,
                   'EUR','Test','t.csv','h1','2026-01-01T00:00:00')""",
        (amount, fee_currency))
    conn.execute(
        """INSERT INTO transactions
           (transaction_date, transaction_type, cryptocurrency, amount,
            price_per_unit, total_value, fee_amount, fee_currency, currency,
            exchange_name, source, record_hash, imported_at)
           VALUES ('2025-06-10T00:00:00+00:00','SELL','BTC',1.0,
                   50000,50000,0,?,'EUR','Test','t.csv','h2',
                   '2026-01-01T00:00:00')""",
        (fee_currency,))
    conn.execute(
        """INSERT INTO fifo_lots
           (purchase_transaction_id, cryptocurrency, purchase_date,
            original_amount, remaining_amount, purchase_price_per_unit,
            cost_basis, exchange_name)
           VALUES (1,'BTC','2024-01-10T00:00:00+00:00',?,?,40000,60000,'Test')""",
        (amount, amount - 1.0))
    conn.execute(
        """INSERT INTO sale_lot_matches
           (sale_transaction_id, fifo_lot_id, sale_date, purchase_date,
            cryptocurrency, amount_sold, purchase_price_per_unit,
            sale_price_per_unit, cost_basis, proceeds, gain_loss,
            holding_period_days)
           VALUES (2,1,'2025-06-10T00:00:00+00:00','2024-01-10T00:00:00+00:00',
                   'BTC',1.0,40000,50000,40000,50000,10000,517)""")
    conn.commit()
    conn.close()


@pytest.fixture
def ref(tmp_path):
    p = str(tmp_path / 'ref.db')
    _make_db(p)
    return p


def _status(report, name):
    return [c.status for c in report.checks if c.name == name][0]


def test_identical_databases_pass(ref, tmp_path):
    new = str(tmp_path / 'new.db')
    _make_db(new)

    report = compare(ref, new)

    assert report.ok, [c for c in report.checks if c.status == 'DIVERSO']
    assert all(c.status != 'DIVERSO' for c in report.checks)


def test_a_changed_amount_is_caught(ref, tmp_path):
    """Una quantita' diversa e' una cifra diversa: deve bocciare."""
    new = str(tmp_path / 'new.db')
    _make_db(new, amount=1.6)

    report = compare(ref, new)

    assert not report.ok
    assert _status(report, 'transazioni (colonne confrontabili)') == 'DIVERSO'


def test_legacy_empty_fee_currency_is_explained_not_ignored(ref, tmp_path):
    """Il caso reale: nel database di riferimento le righe importate da versioni
    vecchie hanno `fee_currency` vuota, nel ricostruito il default dello schema
    la riempie con EUR. Va riportata come differenza spiegata, non nascosta —
    e non deve bocciare la verifica."""
    legacy = str(tmp_path / 'legacy.db')
    _make_db(legacy, fee_currency=None)
    new = str(tmp_path / 'new.db')
    _make_db(new, fee_currency='EUR')

    report = compare(legacy, new)

    assert report.ok, [c for c in report.checks if c.status == 'DIVERSO']
    assert _status(report, 'fee_currency') == 'spiegato'
    assert any('fee_currency' in n for n in report.notes)


def test_a_different_fee_currency_is_not_explained_away(ref, tmp_path):
    """La deroga vale solo per vuoto → EUR. USD → EUR e' un errore di
    conversione, e nascondercelo dentro sarebbe il modo di non vederlo mai."""
    other = str(tmp_path / 'other.db')
    _make_db(other, fee_currency='USD')
    new = str(tmp_path / 'new.db')
    _make_db(new, fee_currency='EUR')

    report = compare(other, new)

    assert not report.ok
    assert _status(report, 'fee_currency') == 'DIVERSO'


def test_missing_rows_are_caught(ref, tmp_path):
    new = str(tmp_path / 'new.db')
    _make_db(new)
    conn = sqlite3.connect(new)
    conn.execute('DELETE FROM sale_lot_matches')
    conn.commit()
    conn.close()

    report = compare(ref, new)

    assert not report.ok
    assert _status(report, 'conteggio sale_lot_matches') == 'DIVERSO'


def test_columns_only_in_the_reference_are_reported_as_legacy(ref, tmp_path):
    """Il database vivo porta colonne che `doc/schema.sql` non dichiara piu'.
    Vanno dette — ma se sono vuote non sono una differenza di contenuto."""
    legacy = str(tmp_path / 'legacy.db')
    _make_db(legacy, extra_legacy_column=True)
    new = str(tmp_path / 'new.db')
    _make_db(new)

    report = compare(legacy, new)

    assert report.ok, [c for c in report.checks if c.status == 'DIVERSO']
    assert any('trade_pair' in n for n in report.notes)


def test_a_populated_column_missing_from_the_rebuild_is_not_ok(ref, tmp_path):
    """Se la colonna legacy contiene dati, il ricostruito li ha persi: boccia."""
    legacy = str(tmp_path / 'legacy.db')
    _make_db(legacy, extra_legacy_column=True)
    conn = sqlite3.connect(legacy)
    conn.execute("UPDATE transactions SET trade_pair='BTCEUR' WHERE id=1")
    conn.commit()
    conn.close()
    new = str(tmp_path / 'new.db')
    _make_db(new)

    report = compare(legacy, new)

    assert not report.ok
    assert _status(report, 'colonne presenti da un solo lato') == 'DIVERSO'


def test_yearly_tax_figures_are_part_of_the_report(ref, tmp_path):
    """Le cifre per anno sono quelle che finiscono nella dichiarazione: devono
    stare nel report anche quando tutto combacia, non solo in caso di errore."""
    new = str(tmp_path / 'new.db')
    _make_db(new)

    report = compare(ref, new)

    assert _status(report, 'aggregati fiscali per anno') == 'identico'
    assert report.yearly, 'il report non porta la tabella per anno'
    assert report.yearly[0]['year'] == '2025'


def test_fingerprint_survives_legacy_columns_and_empty_fee_currency(tmp_path):
    """L'impronta serve a una cosa sola: verificare una ricostruzione **senza
    avere** il database di partenza. Quindi deve dare le stesse righe sul
    database vivo e sul ricostruito, nonostante le due differenze note — le
    colonne legacy che solo il vivo ha, e `fee_currency` vuota nelle righe
    vecchie. Il 2026-10-19 non le dava: l'hash dipendeva dall'ordine delle
    colonne restituito da PRAGMA, che nei due database non è lo stesso.
    """
    from verify_rebuild import fingerprint

    live = str(tmp_path / 'live.db')
    _make_db(live, fee_currency=None, extra_legacy_column=True)
    rebuilt = str(tmp_path / 'rebuilt.db')
    _make_db(rebuilt, fee_currency='EUR')

    assert fingerprint(live)[1:] == fingerprint(rebuilt)[1:], (
        'impronte diverse a dati identici')


def test_fingerprint_catches_a_changed_figure(tmp_path):
    """E deve cambiare quando cambia un numero, altrimenti non prova nulla."""
    from verify_rebuild import fingerprint

    a = str(tmp_path / 'a.db')
    _make_db(a)
    b = str(tmp_path / 'b.db')
    _make_db(b, amount=1.6)

    assert fingerprint(a)[1:] != fingerprint(b)[1:]


def test_fingerprint_refuses_a_database_missing_a_canonical_column(tmp_path):
    """Meglio un errore che un'impronta non confrontabile archiviata come se
    lo fosse."""
    import sqlite3

    import pytest as _pytest

    from verify_rebuild import fingerprint

    p = str(tmp_path / 'mutilato.db')
    _make_db(p)
    conn = sqlite3.connect(p)
    conn.execute('ALTER TABLE transactions DROP COLUMN notes')
    conn.commit()
    conn.close()

    with _pytest.raises(RuntimeError, match='impronta'):
        fingerprint(p)


# ── I buchi trovati dalla review del 2026-10-19, ciascuno col suo test rosso ──

def _add_swap_transfer(path, group='s1', to='USDT', amount=1.0, cost=100.0):
    conn = sqlite3.connect(path)
    conn.execute(
        """INSERT INTO swap_lot_transfers
           (swap_group_id, source_fifo_lot_id, from_cryptocurrency,
            to_cryptocurrency, swap_date, source_purchase_date, amount_from,
            cost_basis)
           VALUES (?,1,'BTC',?,'2025-03-01T00:00:00+00:00',
                   '2024-01-10T00:00:00+00:00',?,?)""",
        (group, to, amount, cost))
    conn.commit()
    conn.close()


def test_a_usd_to_eur_change_is_not_absorbed_by_the_deroga(tmp_path):
    """Il buco che la review ha trovato in un minuto.

    Una fonte che ha **sia** righe legacy vuote **sia** righe non-EUR dava alla
    vecchia deroga, basata sui conteggi, lo spazio per assorbire una riga
    passata da USD a EUR — la firma dell'errore di conversione — riportando
    «nessun'altra differenza». La deroga ora è identificata per riga.
    """
    from verify_rebuild import compare as cmp2

    ref = str(tmp_path / 'ref.db')
    _make_db(ref, fee_currency=None)
    conn = sqlite3.connect(ref)
    conn.execute("UPDATE transactions SET fee_currency='USD' WHERE id=2")
    conn.commit()
    conn.close()

    new = str(tmp_path / 'new.db')
    _make_db(new, fee_currency='USD')
    conn = sqlite3.connect(new)
    conn.execute("UPDATE transactions SET fee_currency='EUR' WHERE id=2")
    conn.commit()
    conn.close()

    report = cmp2(ref, new)

    assert not report.ok, 'un USD→EUR è passato come spiegato'
    assert _status(report, 'fee_currency') == 'DIVERSO'


def test_a_populated_column_missing_from_fifo_lots_is_caught(tmp_path):
    """Il controllo delle colonne guardava solo `transactions`: una colonna
    popolata persa in `fifo_lots` passava in silenzio."""
    ref = str(tmp_path / 'ref.db')
    _make_db(ref)
    conn = sqlite3.connect(ref)
    conn.execute('ALTER TABLE fifo_lots ADD COLUMN manual_cost_override REAL')
    conn.execute('UPDATE fifo_lots SET manual_cost_override = 12345.67')
    conn.commit()
    conn.close()
    new = str(tmp_path / 'new.db')
    _make_db(new)

    report = compare(ref, new)

    assert not report.ok
    assert _status(report, 'colonne presenti da un solo lato') == 'DIVERSO'


def test_a_populated_column_present_only_in_the_rebuild_is_caught(tmp_path):
    """E guardava in una sola direzione: una colonna aggiunta allo schema non
    veniva né confrontata né riportata, quindi nasceva esente da verifica."""
    ref = str(tmp_path / 'ref.db')
    _make_db(ref)
    new = str(tmp_path / 'new.db')
    _make_db(new)
    conn = sqlite3.connect(new)
    conn.execute('ALTER TABLE transactions ADD COLUMN tax_year_override TEXT')
    conn.execute("UPDATE transactions SET tax_year_override='2019'")
    conn.commit()
    conn.close()

    report = compare(ref, new)

    assert not report.ok
    assert _status(report, 'colonne presenti da un solo lato') == 'DIVERSO'


def test_swap_lot_transfers_content_is_compared_not_just_counted(tmp_path):
    """`swap_lot_transfers` era confrontata solo per numero di righe: due
    database con l'eredità dei lotti diversa in ogni colonna davano
    «✓ IDENTICI». È la tabella che porta data e costo attraverso una permuta,
    cioè ciò che decide l'esenzione oltre i 365 giorni."""
    ref = str(tmp_path / 'ref.db')
    _make_db(ref)
    _add_swap_transfer(ref, group='s1', to='USDT', amount=1.0, cost=100.0)
    new = str(tmp_path / 'new.db')
    _make_db(new)
    _add_swap_transfer(new, group='s2', to='USDC', amount=2.0, cost=999.0)

    report = compare(ref, new)

    assert not report.ok
    assert _status(report, 'trasferimenti di lotto (permute differite)') == 'DIVERSO'


def test_fingerprint_covers_swap_lot_transfers(tmp_path):
    from verify_rebuild import fingerprint

    a = str(tmp_path / 'a.db')
    _make_db(a)
    _add_swap_transfer(a, cost=100.0)
    b = str(tmp_path / 'b.db')
    _make_db(b)
    _add_swap_transfer(b, cost=999.0)

    assert fingerprint(a)[1:] != fingerprint(b)[1:]


def test_fingerprint_refuses_a_database_missing_a_table(tmp_path):
    import pytest as _pytest

    from verify_rebuild import fingerprint

    p = str(tmp_path / 'senza.db')
    _make_db(p)
    conn = sqlite3.connect(p)
    conn.execute('DROP TABLE swap_lot_transfers')
    conn.commit()
    conn.close()

    with _pytest.raises(RuntimeError, match='impronta'):
        fingerprint(p)


def test_a_table_missing_on_one_side_is_reported_not_raised(tmp_path):
    """Il caso per cui lo strumento esiste: un database vecchio, da
    `data/backups`, che non ha ancora `swap_lot_transfers`. Prima della
    correzione del 2026-10-19 `compare()` costruiva `SELECT  FROM "<tabella>"`
    e moriva con un OperationalError, perdendo la diagnosi di tutto il resto.
    """
    a = str(tmp_path / 'a.db')
    _make_db(a)
    b = str(tmp_path / 'b.db')
    _make_db(b)
    conn = sqlite3.connect(b)
    conn.execute('DROP TABLE swap_lot_transfers')
    conn.commit()
    conn.close()

    report = compare(a, b)   # non deve sollevare

    assert not report.ok
    assert _status(report, 'conteggio swap_lot_transfers') == 'DIVERSO'
    assert _status(report, 'trasferimenti di lotto (permute differite)') == 'DIVERSO'
    # e il resto della diagnosi resta disponibile
    assert _status(report, 'transazioni (colonne confrontabili)') == 'identico'

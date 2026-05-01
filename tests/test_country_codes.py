"""Test for config.EXCHANGE_COUNTRIES — Portuguese AT (Tabela X) country codes.

The IRS report Anexo J Quadro 9.4A requires a country code for the
exchange where the sale occurred. Unknown codes (000) trigger a manual
review warning.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from config import get_exchange_at_country, EXCHANGE_COUNTRIES


class TestExchangeATCountryCodes:
    """All exchanges in EXCHANGE_COUNTRIES must have a real AT code (not 000)."""

    def test_bybit_uae(self):
        code, name = get_exchange_at_country('Bybit')
        assert code == '784', f"Bybit must be AE/784 (Dubai HQ), got {code}"
        assert 'Arabes' in name or 'Emirados' in name or 'Emirati' in name, name

    def test_inheritance_italy(self):
        code, name = get_exchange_at_country('Inheritance')
        assert code == '380', f"Inheritance from IT estate must be 380, got {code}"
        assert 'Italia' in name

    def test_otc_italy(self):
        code, name = get_exchange_at_country('OTC')
        assert code == '380', f"OTC P2P transactions in IT must be 380, got {code}"
        assert 'Italia' in name

    def test_changely_estonia(self):
        code, name = get_exchange_at_country('changely')
        assert code == '233', f"Changelly is registered in EE/233, got {code}"
        assert 'Eston' in name, name

    def test_no_exchange_has_000_code(self):
        """Regression guard: every exchange in the map must have a real code."""
        offenders = [
            (ex, info['at_code'], info['at_name'])
            for ex, info in EXCHANGE_COUNTRIES.items()
            if info['at_code'] == '000'
        ]
        assert not offenders, (
            f"Exchange con AT code '000' (Desconhecido): {offenders}. "
            f"Per il filing IRS PT serve un codice paese reale per ogni exchange."
        )

    def test_unknown_exchange_returns_000_with_no_name(self):
        """Fallback for truly unknown exchanges still returns 000 (caught by report warning)."""
        code, name = get_exchange_at_country('NonExistentExchange12345')
        assert code == '000'
        assert name == 'Desconhecido'


class TestReportWarningOnUnknownCountry:
    """generate_irs_report must surface a warning when any sale row has AT code 000.

    This is defensive: future imports may introduce new exchanges before the
    config is updated, and we don't want those rows to silently land in the
    Excel with country=Desconhecido.
    """

    def test_main_emits_warning_for_unknown_country(self, tmp_path, monkeypatch, capsys, db_path):
        """Synthetic DB with one sale on an exchange not in EXCHANGE_COUNTRIES."""
        import sqlite3

        # Insert a BUY + SELL on an unknown exchange that triggers Anexo J/Q7
        # (i.e. holding < 365d → goes to Quadro 9.4A which needs country code)
        conn = sqlite3.connect(db_path)
        conn.execute(
            """INSERT INTO transactions
               (transaction_date, transaction_type, cryptocurrency, amount,
                price_per_unit, total_value, fee_amount, exchange_name)
               VALUES ('2024-01-15T12:00:00+00:00', 'BUY', 'XYZ',
                       1.0, 100, 100, 0, 'NonExistentExchange12345')"""
        )
        conn.execute(
            """INSERT INTO transactions
               (transaction_date, transaction_type, cryptocurrency, amount,
                price_per_unit, total_value, fee_amount, exchange_name)
               VALUES ('2024-06-15T12:00:00+00:00', 'SELL', 'XYZ',
                       1.0, 200, 200, 0, 'NonExistentExchange12345')"""
        )
        conn.commit()
        conn.close()

        from calculators.crypto_fifo_tracker import CryptoFIFOTracker
        tracker = CryptoFIFOTracker(db_path)
        tracker.calculate_fifo_lots('XYZ')
        tracker.close()

        # Run the report's check_country_codes function (we'll add it)
        from calculators.generate_irs_report import warn_on_unknown_country_codes
        unknown = warn_on_unknown_country_codes(db_path, year=2024)
        assert 'NonExistentExchange12345' in unknown, (
            f"L'exchange sconosciuto deve essere riportato; got {unknown}"
        )

    def test_warn_returns_empty_for_all_known_exchanges(self, db_path):
        """If all exchanges are mapped (real codes), warn returns empty."""
        # Fresh DB has no transactions → no rows → no warnings
        from calculators.generate_irs_report import warn_on_unknown_country_codes
        unknown = warn_on_unknown_country_codes(db_path, year=2024)
        assert unknown == [], f"Expected no unknown exchanges; got {unknown}"

"""The crypto-to-crypto rule is a setting, not prose in a guide."""
import importlib
import importlib.machinery
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


def test_engine_module_exposes_swap_realizes_for_patching():
    """The engine imports the flag by name so a test can monkeypatch it
    (monkeypatch.setattr('calculators.crypto_fifo_tracker.SWAP_REALIZES', ...)).
    This only checks the name is exposed — tasks 9-11 make the engine read it."""
    import calculators.crypto_fifo_tracker as engine

    assert hasattr(engine, 'SWAP_REALIZES')


class _InMemorySourceLoader(importlib.machinery.SourceFileLoader):
    """Execs the given source text instead of reading config.py from disk.

    importlib.reload() re-finds the module's spec from the filesystem (see
    importlib._bootstrap), so it cannot be redirected this way; calling
    exec_module() directly on the module object is what reload() does
    internally once it has a spec, and is enough to reproduce "re-importing
    a broken profile" without ever touching the real file on disk.
    """

    def __init__(self, name, path, source):
        super().__init__(name, path)
        self._source = source

    def get_source(self, fullname):
        return self._source

    def get_code(self, fullname):
        return compile(self._source, self.path, 'exec')


def _source_with_profile_missing_swap_realizes():
    """config.py's real source, plus a 'ZZ' profile that is a structurally
    valid copy of PT's except it omits tax_rules.swap_realizes entirely."""
    source = open(config.__file__, encoding='utf-8').read()
    marker = 'COUNTRY_PROFILES = {\n'
    assert marker in source, 'config.py layout changed; update this test'

    zz_profile = '''    "ZZ": {
        "name": "Test profile missing the rule",
        "currency": "EUR",
        "timezone": "Europe/Lisbon",
        "tax_rules": {
            "exempt_holding_days": 365,
            "short_term_rate": 0.28,
            "declare_exempt": True,
        },
        "report": {
            "exempt_form": "Test",
            "taxable_form": "Test",
            "language": "en",
            "aggregate_by_day": True,
            "form_reference_url": "",
        },
        "ecb_rates": {
            "enabled": True,
            "rates_file": "data/eurusd.csv",
        },
    },
'''
    broken_source = source.replace(marker, marker + zz_profile, 1)
    assert broken_source != source
    return broken_source


def test_profile_missing_swap_realizes_fails_loudly_at_import():
    """A profile that forgets to declare swap_realizes must fail at import
    with a message naming the profile and the fix — not a bare KeyError."""
    original_env = os.environ.get('FIFO_COUNTRY')
    loader = _InMemorySourceLoader(
        config.__name__, config.__file__, _source_with_profile_missing_swap_realizes()
    )

    os.environ['FIFO_COUNTRY'] = 'ZZ'
    try:
        with pytest.raises(ValueError, match="Profile 'ZZ'.*swap_realizes"):
            loader.exec_module(config)
    finally:
        # Restore the environment first, then re-exec the real file through
        # the normal import machinery so later tests see a clean config —
        # this runs whether the assertion above passed or failed.
        if original_env is None:
            os.environ.pop('FIFO_COUNTRY', None)
        else:
            os.environ['FIFO_COUNTRY'] = original_env
        importlib.reload(config)

    assert config.COUNTRY_PROFILES.keys() == {'PT'}, 'config left in a broken state'
    assert config.SWAP_REALIZES is True

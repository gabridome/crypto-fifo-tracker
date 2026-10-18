"""The crypto-to-crypto rule is a setting, not prose in a guide."""
import os
import re
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import config

PROJECT_ROOT = os.path.join(os.path.dirname(__file__), '..')


def _read(rel_path):
    with open(os.path.join(PROJECT_ROOT, rel_path), encoding='utf-8') as f:
        return f.read()


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


# ---- the import-time guard: config._require_swap_realizes ----


def test_require_swap_realizes_returns_the_declared_value():
    profile = {'name': 'Test', 'tax_rules': {'swap_realizes': False}}
    assert config._require_swap_realizes(profile, 'XX') is False


def test_require_swap_realizes_fails_loudly_naming_profile_and_fix():
    """A profile missing the key must fail with a message naming the
    profile, the missing key, and the fix — not a bare KeyError."""
    profile = {'name': 'Test Country', 'tax_rules': {}}

    with pytest.raises(ValueError) as exc_info:
        config._require_swap_realizes(profile, 'XX')

    message = str(exc_info.value)
    assert "'XX'" in message
    assert 'Test Country' in message
    assert 'swap_realizes' in message
    assert "COUNTRY_PROFILES['XX']['tax_rules']" in message


def test_config_module_calls_the_swap_realizes_guard():
    """The guard is only effective if config.py actually calls it at module
    level — deleting that one line would bypass it while leaving the
    function (and the two tests above) intact, so this is a separate test."""
    assert 'SWAP_REALIZES = _require_swap_realizes(_profile, COUNTRY)' in _read('config.py')


# ---- the banner: config.tax_rule_banner() ----


def test_tax_rule_banner_names_rule_value_and_profile():
    """Pins the exact text: which rule, its value, and which profile."""
    assert config.tax_rule_banner() == (
        'Tax rule in force: swap_realizes=True (config.py, profile PT)'
    )


@pytest.mark.parametrize('rel_path', [
    'calculators/calculate_fifo.py',
    'calculators/generate_annual_summary.py',
    'calculators/generate_irs_report.py',
])
def test_summaries_still_print_the_banner(rel_path):
    """Each generator's main() must still print the banner. The web UI
    drops subprocess stdout on the success path (doc/TODO.md), so this
    console print is the only channel that reaches a user today — losing
    it in one place (tax_rule_banner was deduplicated into one function)
    would silence all three reports at once."""
    assert re.search(r'print\(tax_rule_banner\([\w.]*\)\)', _read(rel_path)), (
        f'{rel_path} no longer prints the tax rule banner'
    )


@pytest.mark.parametrize('rel_path,conn_expr', [
    ('calculators/calculate_fifo.py', 'tracker.conn'),
    ('calculators/generate_annual_summary.py', 'conn'),
    ('calculators/generate_irs_report.py', 'banner_conn'),
])
def test_every_generator_hands_the_banner_a_connection(rel_path, conn_expr):
    """Called bare, the banner can only name the setting `config.py` carries
    now — which for the two reports that read figures out of the database is
    not necessarily the rule that produced them. Only the connection lets it
    check, and a caller that quietly drops it would put `swap_realizes=True`
    on top of figures calculated under deferral, on a tax return.

    Pinned per file rather than as one pattern: the expression differs, and a
    loose pattern would let a caller pass something that is not a connection
    to this database.
    """
    assert f'print(tax_rule_banner({conn_expr}))' in _read(rel_path), (
        f'{rel_path} must hand the banner {conn_expr} so it can check the data '
        f'against the setting'
    )

"""The crypto-to-crypto rule is a setting, not prose in a guide."""
import os
import sys

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

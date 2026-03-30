"""
Crypto FIFO Tracker — Configuration

Centralized configuration for country-specific tax rules, currency,
timezone, and report formats. Edit this file to adapt the tracker
to your jurisdiction.

To add a new country:
  1. Add a new entry to COUNTRY_PROFILES
  2. Set COUNTRY = "your_country_code"
  3. Adapt report generation if your tax authority uses a different format
"""

import os

# Project root — all relative paths are resolved from here
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))

# ============================================================
# ACTIVE COUNTRY — change this to switch jurisdiction
# ============================================================
COUNTRY = os.environ.get("FIFO_COUNTRY", "PT")

# ============================================================
# COUNTRY PROFILES
# ============================================================
COUNTRY_PROFILES = {
    "PT": {
        "name": "Portugal",
        "currency": "EUR",
        "timezone": "Europe/Lisbon",
        "tax_rules": {
            # Holdings >= this many days are exempt from capital gains tax
            "exempt_holding_days": 365,
            # Flat tax rate on short-term capital gains (< exempt_holding_days)
            "short_term_rate": 0.28,
            # Whether long-term gains must still be declared (even if exempt)
            "declare_exempt": True,
        },
        "report": {
            # Tax form names for exempt and taxable gains
            "exempt_form": "Anexo G1 Quadro 07",
            "taxable_form": "Anexo J Quadro 9.4A",
            # Report language
            "language": "pt",
            # Daily aggregation required by tax authority
            "aggregate_by_day": True,
            # Official source URL for form structure
            "form_reference_url": "https://www.lexpoint.pt/Fileget.aspx?FileId=52911",
        },
        "ecb_rates": {
            # Whether USD→EUR conversion is needed for some exchanges
            "enabled": True,
            "rates_file": "data/eurusd.csv",
        },
    },

    # --------------------------------------------------------
    # TEMPLATE: Add new countries below
    # --------------------------------------------------------
    # "IT": {
    #     "name": "Italia",
    #     "currency": "EUR",
    #     "timezone": "Europe/Rome",
    #     "tax_rules": {
    #         "exempt_holding_days": 0,  # Italy: no holding period exemption
    #         "short_term_rate": 0.26,   # 26% flat rate on all crypto gains
    #         "declare_exempt": False,
    #     },
    #     "report": {
    #         "exempt_form": None,
    #         "taxable_form": "Quadro RT",
    #         "language": "it",
    #         "aggregate_by_day": False,
    #         "form_reference_url": "",
    #     },
    #     "ecb_rates": {
    #         "enabled": True,
    #         "rates_file": "data/eurusd.csv",
    #     },
    # },
    #
    # "DE": {
    #     "name": "Deutschland",
    #     "currency": "EUR",
    #     "timezone": "Europe/Berlin",
    #     "tax_rules": {
    #         "exempt_holding_days": 365,   # 1 year holding = tax free
    #         "short_term_rate": None,      # Germany: taxed at personal income rate
    #         "declare_exempt": True,
    #     },
    #     "report": {
    #         "exempt_form": "Anlage SO (exempt)",
    #         "taxable_form": "Anlage SO",
    #         "language": "de",
    #         "aggregate_by_day": False,
    #         "form_reference_url": "",
    #     },
    #     "ecb_rates": {
    #         "enabled": True,
    #         "rates_file": "data/eurusd.csv",
    #     },
    # },
}

# ============================================================
# DERIVED SETTINGS — do not edit below this line
# ============================================================

def get_profile():
    """Return the active country profile."""
    if COUNTRY not in COUNTRY_PROFILES:
        raise ValueError(
            f"Country '{COUNTRY}' not configured. "
            f"Available: {', '.join(COUNTRY_PROFILES.keys())}"
        )
    return COUNTRY_PROFILES[COUNTRY]


# Shortcuts for the most commonly used settings
try:
    _profile = get_profile()
except ValueError as e:
    import sys
    print(f"WARNING: {e}. Falling back to 'PT'.", file=sys.stderr)
    _profile = COUNTRY_PROFILES["PT"]

COUNTRY_NAME = _profile["name"]
CURRENCY = _profile["currency"]
TIMEZONE = _profile["timezone"]

EXEMPT_HOLDING_DAYS = _profile["tax_rules"]["exempt_holding_days"]
SHORT_TERM_RATE = _profile["tax_rules"]["short_term_rate"]
DECLARE_EXEMPT = _profile["tax_rules"]["declare_exempt"]

EXEMPT_FORM = _profile["report"]["exempt_form"]
TAXABLE_FORM = _profile["report"]["taxable_form"]
REPORT_LANGUAGE = _profile["report"]["language"]
AGGREGATE_BY_DAY = _profile["report"]["aggregate_by_day"]

ECB_RATES_ENABLED = _profile["ecb_rates"]["enabled"]
ECB_RATES_FILE = os.path.join(PROJECT_ROOT, _profile["ecb_rates"]["rates_file"])

# ============================================================
# DATABASE
# ============================================================
DATABASE_PATH = os.environ.get(
    "FIFO_DB",
    os.path.join(PROJECT_ROOT, "data", "crypto_fifo.db")
)

# ============================================================
# EXCHANGE CLASSIFICATION
# ============================================================
# Country classification for each exchange
# iso: ISO 3166-1 alpha-2 (used in config/general)
# at_code: numeric code for Portuguese Autoridade Tributária (Anexo J)
# at_name: country name in Portuguese (for IRS report)
EXCHANGE_COUNTRIES = {
    "Binance":        {"iso": "MT", "at_code": "136", "at_name": "Ilhas Caimao"},
    "Binance Card":   {"iso": "MT", "at_code": "136", "at_name": "Ilhas Caimao"},
    "Binance OTC":    {"iso": "MT", "at_code": "136", "at_name": "Ilhas Caimao"},
    "Bitstamp":       {"iso": "GB", "at_code": "826", "at_name": "Reino Unido"},
    "Bitfinex":       {"iso": "VG", "at_code": "092", "at_name": "Ilhas Virgens Britanicas"},
    "Coinbase":       {"iso": "US", "at_code": "840", "at_name": "Estados Unidos"},
    "Coinbase Prime": {"iso": "US", "at_code": "840", "at_name": "Estados Unidos"},
    "Coinpal":        {"iso": "US", "at_code": "840", "at_name": "Estados Unidos"},
    "GDTRE":          {"iso": "IT", "at_code": "380", "at_name": "Italia"},
    "Kraken":         {"iso": "US", "at_code": "840", "at_name": "Estados Unidos"},
    "Mt.Gox":         {"iso": "JP", "at_code": "392", "at_name": "Japao"},
    "OTC":            {"iso": "XX", "at_code": "000", "at_name": "Desconhecido"},
    "Revolut":        {"iso": "LT", "at_code": "440", "at_name": "Lituania"},
    "TRT":            {"iso": "IT", "at_code": "380", "at_name": "Italia"},
    "Wirex":          {"iso": "GB", "at_code": "826", "at_name": "Reino Unido"},
}

def get_exchange_country(exchange_name):
    """Return the ISO country code for an exchange, or 'XX' if unknown."""
    entry = EXCHANGE_COUNTRIES.get(exchange_name)
    return entry["iso"] if entry else "XX"

def get_exchange_at_country(exchange_name):
    """Return (at_code, at_name) for Portuguese IRS reports."""
    entry = EXCHANGE_COUNTRIES.get(exchange_name)
    if entry:
        return entry["at_code"], entry["at_name"]
    return "000", "Desconhecido"


# ============================================================
# DISPLAY
# ============================================================
if __name__ == "__main__":
    print(f"Crypto FIFO Tracker — Configuration")
    print(f"  Country:          {COUNTRY_NAME} ({COUNTRY})")
    print(f"  Currency:         {CURRENCY}")
    print(f"  Timezone:         {TIMEZONE}")
    print(f"  Database:         {DATABASE_PATH}")
    print(f"  Exempt threshold: {EXEMPT_HOLDING_DAYS} days")
    print(f"  Tax rate:         {SHORT_TERM_RATE:.0%}" if SHORT_TERM_RATE else "  Tax rate:         (personal income rate)")
    print(f"  Exempt form:      {EXEMPT_FORM}")
    print(f"  Taxable form:     {TAXABLE_FORM}")
    print(f"  ECB rates:        {'enabled' if ECB_RATES_ENABLED else 'disabled'}")

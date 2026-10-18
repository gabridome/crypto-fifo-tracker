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
            "swap_realizes": True,   # crypto→crypto realizes at the swap.
                                     # ASSUMPTION, not verified law — doc/compliance.md
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
    #         "swap_realizes": True,    # ASSUMPTION, not verified law — doc/compliance.md
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
    #         "swap_realizes": True,        # ASSUMPTION, not verified law — doc/compliance.md
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


def _require_swap_realizes(profile, country):
    """Return profile['tax_rules']['swap_realizes'], or fail loudly.

    No .get() default: a profile that forgets to declare the rule must fail
    at import, not silently inherit someone else's tax treatment. Extracted
    from the module-level assignment so a test can call it directly against
    a fake profile dict, instead of re-executing config.py's source.
    """
    try:
        return profile["tax_rules"]["swap_realizes"]
    except KeyError:
        raise ValueError(
            f"Profile '{country}' ({profile['name']}) is missing tax_rules.swap_realizes. "
            f"Add it explicitly (True or False) to COUNTRY_PROFILES['{country}']['tax_rules'] "
            f"in config.py — there is no safe default for a legal assumption."
        ) from None


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

SWAP_REALIZES = _require_swap_realizes(_profile, COUNTRY)

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
    "Bybit":          {"iso": "AE", "at_code": "784", "at_name": "Emirados Arabes Unidos"},
    "changely":       {"iso": "EE", "at_code": "233", "at_name": "Estonia"},
    "Coinbase":       {"iso": "US", "at_code": "840", "at_name": "Estados Unidos"},
    "Coinbase Prime": {"iso": "US", "at_code": "840", "at_name": "Estados Unidos"},
    "Coinpal":        {"iso": "US", "at_code": "840", "at_name": "Estados Unidos"},
    "GDTRE":          {"iso": "IT", "at_code": "380", "at_name": "Italia"},
    "Inheritance":    {"iso": "IT", "at_code": "380", "at_name": "Italia"},
    "Kraken":         {"iso": "US", "at_code": "840", "at_name": "Estados Unidos"},
    "Mt.Gox":         {"iso": "JP", "at_code": "392", "at_name": "Japao"},
    "OTC":            {"iso": "IT", "at_code": "380", "at_name": "Italia"},
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


def tax_rule_banner(conn=None) -> str:
    """One line naming the tax rule **configured right now** — not the rule
    that produced any figures.

    That distinction is the whole point of this signature. `SWAP_REALIZES` is
    what `config.py` carries at the moment of the call. For
    `calculators/calculate_fifo.py` the two coincide by construction: it prints
    this immediately after calculating. For a report that reads figures out of
    the database — `generate_annual_summary.py`, `generate_irs_report.py` —
    they do not: those rows were written by whatever run last touched them,
    under whatever setting was in force then. This docstring used to say the
    banner named the rule behind the figures it heads — false for two of its
    three callers, and corrected in task 12.

    Hand over `conn` and the banner also says what the **database** shows, from
    three kinds of positive evidence in `calculators/swap_reporting.py`: a
    deferred swap disposal and an inherited lot can only be written by a
    `swap_realizes = False` run, a realized swap disposal only by a `True` one.
    Where any of them contradicts the configured setting, the figures this line
    heads were produced under the other rule and the setting changed afterwards
    — so the banner says so loudly, names which evidence fired, and says what
    to do, because those figures go on a tax return.

    A clean line therefore establishes that no disposal and no inherited lot on
    record contradicts the setting named. It does not establish more than that.
    Two blind spots, both narrow and both stated in that module: a swap made on
    the same day as the purchase it consumed whose give side has **already** been
    recalculated under the other rule — same-day alone is still caught by the
    give-side traces — and a database older than the `swap_group_id` migration,
    where nothing is identifiable as a swap leg at all, so all three read zero
    and the line is clean for want of evidence rather than for want of a
    contradiction. That last case is a
    silence indistinguishable from agreement, and it is recorded in
    `doc/TODO.md` rather than turned into an alarm: it can only arise under the
    non-default rule, on a database the engine would refuse to recalculate.

    No connection, no claim about data: the return value is then exactly the
    single line it has always been, and so is the clean case — a database whose
    swap disposals all agree with the setting adds nothing. A warning that
    fires on every run is a warning nobody reads.
    """
    line = f"Tax rule in force: swap_realizes={SWAP_REALIZES} (config.py, profile {COUNTRY})"
    if conn is None:
        return line

    # Imported here rather than at module level: `import config` must not
    # acquire a dependency on the calculators package (doc/code_guidelines.md
    # §4.2 — importing config must never fail). Only a caller that hands over a
    # connection needs it, and every such caller already has PROJECT_ROOT on
    # sys.path because it imports config from there.
    import textwrap

    from calculators.swap_reporting import (
        deferred_disposals, inherited_lots, realized_swap_disposals,
    )

    deferred = deferred_disposals(conn)['count']
    realized = realized_swap_disposals(conn)['count']
    inherited = inherited_lots(conn)['count']

    # A list, and more than one entry does occur: the first two branches below
    # are both evidence of a deferred run and both fire on a clean deferred
    # database read under a setting of True. The third is gated on the opposite
    # value, so it never joins them. Each is named separately because the fix
    # differs — a stale disposal and a stranded inherited lot are recalculated
    # from different sides.
    found = []
    if SWAP_REALIZES and deferred:
        found.append(f"{deferred:,} deferred swap disposal(s)")
    if SWAP_REALIZES and inherited:
        found.append(f"{inherited:,} lot(s) whose acquisition date and cost were "
                     f"inherited across a swap")
    if not SWAP_REALIZES and realized:
        found.append(f"{realized:,} swap disposal(s) that realized a gain")

    if found:
        other = not SWAP_REALIZES
        rule = '!!! '
        body = [rule + ' THE SETTING AND THE DATA DISAGREE',
                rule,
                rule + f' config.py is set to swap_realizes={SWAP_REALIZES}, but the database holds']
        for item in found:
            # Wrapped: an item longer than the frame reads as a formatting
            # accident, and a warning that looks accidental is one a reader
            # discounts.
            body += textwrap.wrap(item + '.', width=62,
                                  initial_indent=rule + '   ',
                                  subsequent_indent=rule + '   ')
        body += [
            rule,
            rule + f' Only a swap_realizes={other} run writes those.',
            rule + ' So the figures above do not all come from the setting named above.',
            # Not "the figures above were not produced by this setting": on a
            # database recalculated for only some cryptocurrencies, some of
            # them were. The counted rows are the ones that were not.
            rule + f' The rows counted here were produced under swap_realizes={other},',
            rule + ' and the setting changed afterwards.',
            rule,
            rule + ' Before you use them, do one of two things:',
            rule + '   - run calculators/calculate_fifo.py, which recalculates every',
            rule + '     cryptocurrency under the setting now in force; or',
            rule + f'   - read them under swap_realizes={other} — the rule they were born under —',
            rule + "     and do not mix them with figures produced by this setting.",
            rule,
            rule + ' Do not file a tax return from a report that printed this.',
        ]
        # The frame is measured from the content, not a constant: a line that
        # sticks out of its own box is how this went wrong the first time, and
        # the texts here vary with the setting and the counts. Deliberately
        # untested — measuring it from the content makes "no line exceeds the
        # frame" true by construction, and a test could only forbid going back
        # to a constant, which this comment does more cheaply.
        bar = rule + '=' * (max(len(b) for b in body) - len(rule))
        return '\n'.join([line, bar] + body + [bar])

    # Agreement. Only the non-default rule gets a confirming line: under
    # deferral a reader is entitled to see that it actually reached the data,
    # rather than infer it from a setting. Under the default there is nothing
    # to add, which keeps the line that heads every ordinary report unchanged.
    if deferred:
        return (f"{line}\n  Database: {deferred:,} deferred swap disposal(s) on record, "
                f"consistent with this setting.")
    return line


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

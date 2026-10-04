"""The markets a board is designed for: their supply and the rules their codes set.

A company names its market in its profile (``market``), and the page picks
one for a new visitor from where their browser says they are. The market
sets what differs from country to country and is not the company's to
decide: the supply's voltage and frequency, its usual earthing, the currency
a quotation is priced in, and the few wiring rules its code states as
numbers. Everything else stays IEC 60364 as the default profile applies it,
and a company's own settings still win over its market's.

Values and where they are from (a code the research could not reach is
named as not found, and its market then keeps the IEC default):

* **Palestine (PS)**: 230/400 V 50 Hz; no national wiring code was found,
  the Palestine Standards Institution adopts IEC and EN standards
  (psi.pna.ps). Priced in ILS, the currency in daily use.
* **Israel (IL)**: 230/400 V 50 Hz, TN-C-S. Electricity Regulations: a
  residual current device of 30 mA on every final circuit of a residence
  (Panel Regulations, 1991), and at most 3 % drop from the consumer's
  terminals (Final Circuits Regulations, 1984, reg. 2). ILS.
* **Jordan (JO)**: 230/400 V 50 Hz; Jordanian National Building Code for
  Electrical Installations (2008). JOD.
* **Saudi Arabia (SA)**: 230/400 V **60 Hz**; Saudi Building Code SBC 401
  (2007 edition clause 52-5: 4 % drop). SAR.
* **United Arab Emirates (AE)**: 230/400 V 50 Hz, TN-S; DEWA Regulations for
  Electrical Installations (2017): 4 % drop. AED.
* **Qatar (QA)**: 240/415 V 50 Hz; KAHRAMAA Electricity Wiring Code. QAR.
* **Kuwait (KW)**: 240/415 V 50 Hz, TT; MEW/R-1: 2.5 % drop from the
  service intake (clause 700-2). KWD.
* **Egypt (EG)**: 220/380 V 50 Hz, TN-S; Egyptian Code 302. EGP.
* **Lebanon (LB)**: 220/400 V 50 Hz; no code found. Priced in USD, which
  installation contracts there are commonly priced in (an assumption).
* **Iraq (IQ)**: 230/400 V 50 Hz; no national code found, project
  specifications cite BS 7671. IQD.

A voltage or earthing not found falls back to 230/400 V and TN-S. These
are defaults for a new board: the engineer enters the real supply.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from app.core.errors import ValidationError
from app.models.schemas.design import LoadKind

#: Final circuits, as Israel's rule for an RCD on every one reads.
_FINAL_CIRCUITS = (
    LoadKind.LIGHTING,
    LoadKind.SOCKET,
    LoadKind.AIR_CONDITIONING,
    LoadKind.WATER_HEATER,
    LoadKind.KITCHEN,
    LoadKind.FAN,
    LoadKind.CONTROL,
    LoadKind.DATA,
    LoadKind.OTHER,
)


@dataclass(frozen=True)
class Market:
    """One market's supply and stated rules.

    Attributes:
        code: ISO 3166-1 alpha-2.
        name: In English.
        voltage_v: The line-to-line voltage of a three-phase supply.
        phase_voltage_v: Line to neutral.
        frequency_hz: 50 or 60.
        earthing: The usual system, where the code or utility states one.
        currency: ISO 4217, for a quotation.
        regulation: The code a design there answers to.
        max_voltage_drop_percent: The code's limit for every load, where it
            states one.
        rcd_every_final_circuit: Whether the code asks a 30 mA residual
            current device on every final circuit.
        languages: The drawing languages customary there, first preferred.
    """

    code: str
    name: str
    voltage_v: int
    phase_voltage_v: int
    frequency_hz: int
    earthing: str
    currency: str
    regulation: str
    max_voltage_drop_percent: Decimal | None = None
    rcd_every_final_circuit: bool = False
    languages: tuple[str, ...] = field(default=("en",))


MARKETS: dict[str, Market] = {
    market.code: market
    for market in (
        Market(
            "PS",
            "Palestine",
            400,
            230,
            50,
            "TN-S",
            "ILS",
            "IEC 60364 (adopted by the Palestine Standards Institution)",
            languages=("ar", "en"),
        ),
        Market(
            "IL",
            "Israel",
            400,
            230,
            50,
            "TN-C-S",
            "ILS",
            "Israeli Electricity Law and Regulations",
            max_voltage_drop_percent=Decimal(3),
            rcd_every_final_circuit=True,
            languages=("he", "en"),
        ),
        Market(
            "JO",
            "Jordan",
            400,
            230,
            50,
            "TN-S",
            "JOD",
            "Jordanian National Building Code for Electrical Installations",
            languages=("ar", "en"),
        ),
        Market(
            "SA",
            "Saudi Arabia",
            400,
            230,
            60,
            "TN-S",
            "SAR",
            "Saudi Building Code SBC 401",
            max_voltage_drop_percent=Decimal(4),
            languages=("ar", "en"),
        ),
        Market(
            "AE",
            "United Arab Emirates",
            400,
            230,
            50,
            "TN-S",
            "AED",
            "DEWA Regulations for Electrical Installations",
            max_voltage_drop_percent=Decimal(4),
            languages=("en", "ar"),
        ),
        Market(
            "QA",
            "Qatar",
            415,
            240,
            50,
            "TN-S",
            "QAR",
            "KAHRAMAA Electricity Wiring Code",
            languages=("en", "ar"),
        ),
        Market(
            "KW",
            "Kuwait",
            415,
            240,
            50,
            "TT",
            "KWD",
            "MEW/R-1 Regulations",
            max_voltage_drop_percent=Decimal("2.5"),
            languages=("en", "ar"),
        ),
        Market(
            "EG",
            "Egypt",
            380,
            220,
            50,
            "TN-S",
            "EGP",
            "Egyptian Code 302",
            languages=("ar", "en"),
        ),
        Market("LB", "Lebanon", 400, 220, 50, "TN-S", "USD", "IEC 60364", languages=("ar", "en")),
        Market("IQ", "Iraq", 400, 230, 50, "TN-S", "IQD", "BS 7671", languages=("ar", "en")),
    )
}


def market(code: str) -> Market:
    """Look a market up by its country code.

    Args:
        code: ISO 3166-1 alpha-2, any case.

    Returns:
        The market.

    Raises:
        ValidationError: If no market has that code.
    """
    found = MARKETS.get(code.strip().upper())
    if found is None:
        raise ValidationError(
            f"no market {code!r}; markets are {', '.join(MARKETS)}",
            code="market_unknown",
            params={"market": code},
        )
    return found


def profile_settings(code: str, drop_limits: dict[LoadKind, Decimal]) -> dict[str, object]:
    """The profile settings a market sets, as a company's would be stored.

    Args:
        code: The market.
        drop_limits: The per-load voltage-drop limits it starts from.

    Returns:
        JSON-shaped settings: the market's voltage-drop limit for every load
        (never above a limit already lower) and a 30 mA device on every
        final circuit, where its code asks them.
    """
    found = market(code)
    settings: dict[str, object] = {}
    if found.max_voltage_drop_percent is not None:
        limit = found.max_voltage_drop_percent
        settings["default_max_voltage_drop_percent"] = str(limit)
        # Only ever tightened: a code's general limit does not relax the
        # lighting limit the default already holds lower.
        settings["max_voltage_drop_percent"] = {
            kind.value: str(min(limit, current)) for kind, current in drop_limits.items()
        }
    if found.rcd_every_final_circuit:
        settings["circuit_rules"] = {
            kind.value: {"residual_current_ma": "30"} for kind in _FINAL_CIRCUITS
        }
    return settings

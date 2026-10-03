"""A moulded-case breaker for a circuit beyond the largest miniature breaker.

Above 125 A, the circuit's breaker is an ABB SACE Tmax with a thermomagnetic
trip unit, read from ABB's technical catalogues:

* **Tmax XT** (1SDC210033D0203, "SACE Tmax XT ... up to 250 A"): XT3 TMD,
  In 160, 200 and 250 A, ``I3`` fixed at 10 In (catalogue p. 2/5); where the
  fault level is above XT3's 50 kA, XT4 TMA, ``I3`` 5...10 In (p. 2/6).
  Icu at 380/415 V from p. 1/3.
* **Tmax T** (1SDC210015D0208, May 2016): T5 TMA, In 320, 400 and 500 A, and
  T6 TMA, In 630 and 800 A, ``I3`` 5...10 In (p. 2/7). Icu at 380/400/415 V
  from p. 2/2.

Widths are the fixed versions' (XT p. 1/3, T p. 2/2).

The version letter is the lowest whose Icu clears the board's fault level.
An adjustable ``I3`` is taken at its top, 10 In, which is what the far-end
short circuit and earth fault checks then hold it to; the board says so,
since setting it lower lets a longer cable pass. The catalogue's Icu values
are for 380-415 V, so no breaker is selected on another supply voltage.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

#: The catalogues the frames are read from.
SOURCE_XT = "ABB SACE Tmax XT technical catalogue (1SDC210033D0203)"
SOURCE_T = "ABB SACE Tmax T technical catalogue (1SDC210015D0208)"

#: The supply voltages the catalogues' Icu values are given for.
VOLTAGES_V = (Decimal(380), Decimal(400), Decimal(415))


@dataclass(frozen=True)
class _Frame:
    name: str
    release: str
    ratings_a: tuple[int, ...]
    #: Width of the fixed version, 3 and 4 poles, mm.
    widths_mm: tuple[int, int]
    #: Icu at 380-415 V by version letter, lowest first.
    icu_ka: tuple[tuple[str, int], ...]
    adjustable: bool
    source: str
    page: str


_FRAMES: tuple[_Frame, ...] = (
    _Frame(
        "XT3",
        "TMD",
        (160, 200, 250),
        (105, 140),
        (("N", 36), ("S", 50)),
        False,
        SOURCE_XT,
        "1/3, 2/5",
    ),
    _Frame(
        "XT4",
        "TMA",
        (160, 200, 250),
        (105, 140),
        (("N", 36), ("S", 50), ("H", 70), ("L", 120), ("V", 150)),
        True,
        SOURCE_XT,
        "1/3, 2/6",
    ),
    _Frame(
        "T5",
        "TMA",
        (320, 400, 500),
        (140, 186),
        (("N", 36), ("S", 50), ("H", 70), ("L", 120), ("V", 200)),
        True,
        SOURCE_T,
        "2/2, 2/7",
    ),
    _Frame(
        "T6",
        "TMA",
        (630, 800),
        (210, 280),
        (("N", 36), ("S", 50), ("H", 70), ("L", 100), ("V", 150)),
        True,
        SOURCE_T,
        "2/2, 2/7",
    ),
)

#: ``I3`` as a multiple of In: fixed for TMD, the top of 5...10 In for TMA.
MAGNETIC_MULTIPLE = Decimal(10)


@dataclass(frozen=True)
class Mccb:
    """A selected moulded-case breaker.

    Attributes:
        type_number: As ABB writes it, e.g. "XT3N 250 TMD 250".
        rated_a: In.
        magnetic_trip_a: ``I3``, at the top of its range where adjustable.
        adjustable: Whether ``I3`` can be set lower (5...10 In).
        icu_ka: Its ultimate breaking capacity at 380-415 V.
        source: The catalogue and its pages.
    """

    type_number: str
    rated_a: Decimal
    magnetic_trip_a: Decimal
    adjustable: bool
    icu_ka: Decimal
    source: str


def _frame_size(name: str, rated: int) -> int:
    """The frame's own current in ABB's type designation: T5 400 or 630, T6 630 or 800."""
    if name == "T5":
        return 400 if rated <= 400 else 630
    return rated


def ratings() -> tuple[Decimal, ...]:
    """Every In a moulded-case breaker is selected at, lowest first."""
    return tuple(sorted({Decimal(r) for frame in _FRAMES for r in frame.ratings_a}))


def select(current_a: Decimal, fault_level_ka: Decimal | None, voltage_v: Decimal) -> Mccb | None:
    """The smallest Tmax breaker carrying a current and clearing a fault.

    Args:
        current_a: What it must carry (Ib, or more for discrimination).
        fault_level_ka: The board's prospective fault; where unknown, the
            lowest version is taken and the board already says so.
        voltage_v: The supply's line voltage.

    Returns:
        The breaker; ``None`` beyond 800 A, beyond every version's Icu, or on
        a voltage the catalogues' Icu is not given for.
    """
    if voltage_v not in VOLTAGES_V:
        return None
    for frame in _FRAMES:
        rated = next((r for r in frame.ratings_a if r >= current_a), None)
        if rated is None:
            continue
        version = next(
            (
                (letter, icu)
                for letter, icu in frame.icu_ka
                if fault_level_ka is None or icu >= fault_level_ka
            ),
            None,
        )
        if version is None:
            continue
        letter, icu = version
        size = 250 if frame.name.startswith("XT") else _frame_size(frame.name, rated)
        return Mccb(
            type_number=f"{frame.name}{letter} {size} {frame.release} {rated}",
            rated_a=Decimal(rated),
            magnetic_trip_a=MAGNETIC_MULTIPLE * rated,
            adjustable=frame.adjustable,
            icu_ka=Decimal(icu),
            source=f"{frame.source}, p. {frame.page}",
        )
    return None


def source_of(type_number: str) -> str | None:
    """The catalogue a breaker this module selects is read from; ``None`` for any other."""
    if " TMD " not in type_number and " TMA " not in type_number:
        return None
    return SOURCE_XT if type_number.startswith("XT") else SOURCE_T


def width_mm(type_number: str, poles: int) -> Decimal | None:
    """The width of a breaker this module selects, fixed version; ``None`` for any other."""
    if source_of(type_number) is None:
        return None
    frame = next((f for f in _FRAMES if type_number.startswith(f.name)), None)
    if frame is None:
        return None
    three, four = frame.widths_mm
    return Decimal(four if poles == 4 else three)

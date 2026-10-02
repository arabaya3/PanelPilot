"""Voltage drop along a circuit's cable, held within the company's limit.

The limit is from the origin of the installation to the load (IEC 60364-5-52
Annex G: 3 % for lighting, 5 % for other uses, on a public LV supply), so a
circuit on a sub-board has what its feeders dropped already taken from it.

* The drop is read from the tabulated V/(A·km) figures ``cable_sizing``
  holds: copper from Schneider's Fig. G28, aluminium from ABB's §2.2.2
  tables. Neither interpolates between power factors, so a circuit is taken
  at the worst of the columns its power factor could fall between: Fig. G28's
  lighting (cos phi 1) and motor (cos phi 0.8) columns, or every one of ABB's
  five tables. That errs on the side of a larger cable.
* It is a share of the voltage across what the cable feeds: the line voltage
  for a three-phase circuit, the phase voltage for a single-phase one.
* A star-delta motor's six conductors are three loops, each carrying the
  winding current Ir/√3 to a winding across the line voltage, so it is read
  from the single-phase columns at that current and voltage.
* A cable that drops too much is stepped up through the tabulated sections
  until it does not; one that no section brings within the limit keeps its
  size, and the board says so.

Only a circuit whose length is given is checked; the board counts the rest.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from app.ai.tools import cable_sizing
from app.models.schemas.calculations import ConductorMaterial
from app.models.schemas.design import CompanyProfile, InstallationConditions, LoadKind

#: The cross-sections both voltage-drop tables hold, smallest first.
SECTIONS: tuple[Decimal, ...] = tuple(
    Decimal(s)
    for s in (
        "1.5",
        "2.5",
        "4",
        "6",
        "10",
        "16",
        "25",
        "35",
        "50",
        "70",
        "95",
        "120",
        "150",
        "185",
        "240",
        "300",
    )
)

#: The power factors each table is read at; the drop is the largest of them.
_COPPER_COLUMNS = (
    (Decimal(1), cable_sizing.LoadType.LIGHTING),
    (Decimal("0.8"), cable_sizing.LoadType.MOTOR),
)
_ALUMINIUM_POWER_FACTORS = tuple(Decimal(p) for p in ("1", "0.9", "0.85", "0.8", "0.75"))

_HUNDREDTH = Decimal("0.01")


@dataclass(frozen=True)
class Run:
    """What a cable carries and how far, for its voltage drop.

    Attributes:
        current_a: The current each conductor carries.
        length_m: The route length, one way.
        three_phase: Read from the three-phase columns, rather than as a
            single-phase loop out and back.
        voltage_v: The voltage the drop is a share of.
    """

    current_a: Decimal
    length_m: Decimal
    three_phase: bool
    voltage_v: Decimal


@dataclass(frozen=True)
class Budget:
    """How much drop a board's circuits may have.

    Attributes:
        upstream_percent: Already dropped on the feeders from the origin to
            this board.
        feeder_limits: For each sub-board this board feeds, by name, the limit
            of the strictest load anywhere below it, which its feeder is held
            to instead of the limit for a sub-board.
    """

    upstream_percent: Decimal = Decimal(0)
    feeder_limits: dict[str, Decimal] = field(default_factory=dict)

    def limit(self, profile: CompanyProfile, kind: LoadKind, feeds: str | None) -> Decimal:
        """The limit from the origin for one circuit.

        Args:
            profile: The company whose limits apply.
            kind: The circuit's kind of load.
            feeds: The sub-board it feeds, for a feeder.

        Returns:
            The limit, in percent.
        """
        if feeds is not None and feeds in self.feeder_limits:
            return self.feeder_limits[feeds]
        return limit_for(profile, kind)


def limit_for(profile: CompanyProfile, kind: LoadKind) -> Decimal:
    """The company's limit from the origin for a kind of load.

    Args:
        profile: The company whose limits apply.
        kind: The kind of load.

    Returns:
        The limit, in percent.
    """
    return profile.max_voltage_drop_percent.get(kind, profile.default_max_voltage_drop_percent)


def percent(run: Run, section_mm2: Decimal, conditions: InstallationConditions) -> Decimal:
    """The drop along a run, as a share of its voltage.

    Args:
        run: What the cable carries and how far.
        section_mm2: A section in :data:`SECTIONS`.
        conditions: How the cable is installed, for its material and, for
            aluminium, whether it is single-core.

    Returns:
        The drop in percent, at the worst power factor the tables hold.
    """
    material = conditions.conductor_material
    if material is ConductorMaterial.ALUMINIUM:
        readings = [
            cable_sizing.voltage_drop(
                current_a=run.current_a,
                length_m=run.length_m,
                cross_section_mm2=section_mm2,
                conductor_material=material,
                power_factor=power_factor,
                three_phase=run.three_phase,
                installation_method=conditions.installation_method,
            )
            for power_factor in _ALUMINIUM_POWER_FACTORS
        ]
    else:
        readings = [
            cable_sizing.voltage_drop(
                current_a=run.current_a,
                length_m=run.length_m,
                cross_section_mm2=section_mm2,
                conductor_material=material,
                power_factor=power_factor,
                three_phase=run.three_phase,
                load_type=load_type,
            )
            for power_factor, load_type in _COPPER_COLUMNS
        ]
    return max(readings) / run.voltage_v * 100


@dataclass(frozen=True)
class Checked:
    """A cable after its voltage drop is checked.

    Attributes:
        section_mm2: Its section, enlarged where the drop needed it.
        percent: The drop along it, rounded to a hundredth.
        within: Whether the drop from the origin is within the limit.
    """

    section_mm2: Decimal
    percent: Decimal
    within: bool


def fit(
    run: Run,
    section_mm2: Decimal,
    allowed_percent: Decimal,
    conditions: InstallationConditions,
) -> Checked | None:
    """Enlarge a cable until its drop is within what is allowed it.

    Args:
        run: What the cable carries and how far.
        section_mm2: Its section as sized for current.
        allowed_percent: What its own drop may be: the limit less what was
            dropped before the board.
        conditions: How the cable is installed.

    Returns:
        The smallest section at or above ``section_mm2`` whose drop is within
        ``allowed_percent``; or, where none is, ``section_mm2`` itself with
        ``within`` false. ``None`` if ``section_mm2`` is beyond the tables.
    """
    if section_mm2 not in SECTIONS:
        return None
    for candidate in SECTIONS[SECTIONS.index(section_mm2) :]:
        drop = percent(run, candidate, conditions)
        if drop <= allowed_percent:
            return Checked(candidate, drop.quantize(_HUNDREDTH), within=True)
    drop = percent(run, section_mm2, conditions)
    return Checked(section_mm2, drop.quantize(_HUNDREDTH), within=False)

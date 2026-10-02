"""Tests for `app/design/voltage_drop.py`.

Mirrors the module 1:1 — if you add a function there, add its test here.
"""

from __future__ import annotations

from decimal import Decimal

from app.design import profile, voltage_drop
from app.models.schemas.calculations import ConductorMaterial, InstallationMethod
from app.models.schemas.design import InstallationConditions, LoadKind

_COPPER = InstallationConditions()


def _run(
    amps: str, metres: str, *, three_phase: bool = False, volts: str = "230"
) -> voltage_drop.Run:
    return voltage_drop.Run(Decimal(amps), Decimal(metres), three_phase, Decimal(volts))


def test_limit_for() -> None:
    company = profile.default_profile()
    # IEC 60364-5-52 Annex G: 3 % for lighting, 5 % for anything else.
    assert voltage_drop.limit_for(company, LoadKind.LIGHTING) == 3
    assert voltage_drop.limit_for(company, LoadKind.SOCKET) == 5
    strict = company.model_copy(update={"default_max_voltage_drop_percent": Decimal(4)})
    assert voltage_drop.limit_for(strict, LoadKind.SOCKET) == 4


def test_budget_holds_a_feeder_to_what_is_below_it() -> None:
    company = profile.default_profile()
    budget = voltage_drop.Budget(feeder_limits={"DB-1": Decimal(3)})
    assert budget.limit(company, LoadKind.SUB_BOARD, "DB-1") == 3
    assert budget.limit(company, LoadKind.SUB_BOARD, None) == 5


def test_percent_takes_the_worse_copper_column() -> None:
    # Fig. G28, 10 mm² three-phase: lighting 3.6, motor 3.2 V/A/km.
    # 3.6 x 20 A x 0.1 km = 7.2 V of 400 V.
    run = _run("20", "100", three_phase=True, volts="400")
    assert voltage_drop.percent(run, Decimal(10), _COPPER) == Decimal("1.8")
    # 2.5 mm² single-phase: lighting 18, motor 14.4; 18 x 16 x 0.03 = 8.64 V of 230 V.
    single = voltage_drop.percent(_run("16", "30"), Decimal("2.5"), _COPPER)
    assert single.quantize(Decimal("0.001")) == Decimal("3.757")


def test_percent_reads_aluminium_from_every_power_factor() -> None:
    aluminium = InstallationConditions(
        conductor_material=ConductorMaterial.ALUMINIUM,
        installation_method=InstallationMethod.C,
    )
    # ABB §2.2.2, 16 mm² three-core three-phase: cos phi 1 is the worst, 4.08 V/(A·km).
    run = _run("10", "100", three_phase=True, volts="400")
    assert voltage_drop.percent(run, Decimal(16), aluminium) == Decimal("1.02")


def test_fit_enlarges_a_cable_until_it_is_within() -> None:
    # 2.5 mm² drops 3.76 %; 4 mm² drops 11.2 x 16 x 0.03 = 5.376 V, 2.34 %.
    checked = voltage_drop.fit(_run("16", "30"), Decimal("2.5"), Decimal(3), _COPPER)
    assert checked == voltage_drop.Checked(Decimal(4), Decimal("2.34"), within=True)


def test_fit_keeps_a_cable_that_is_already_within() -> None:
    checked = voltage_drop.fit(_run("16", "10"), Decimal("2.5"), Decimal(3), _COPPER)
    assert checked is not None
    assert checked.section_mm2 == Decimal("2.5")
    assert checked.within


def test_fit_says_when_no_section_is_enough() -> None:
    checked = voltage_drop.fit(_run("100", "5000"), Decimal(35), Decimal(3), _COPPER)
    assert checked is not None
    assert not checked.within
    assert checked.section_mm2 == Decimal(35)
    # Nothing is allowed once the feeders have dropped the whole limit.
    spent = voltage_drop.fit(_run("1", "1"), Decimal("2.5"), Decimal(0), _COPPER)
    assert spent is not None
    assert not spent.within


def test_fit_leaves_a_section_beyond_the_tables_unchecked() -> None:
    assert voltage_drop.fit(_run("500", "50"), Decimal(400), Decimal(5), _COPPER) is None


def test_starting_percent_reads_the_start_up_column() -> None:
    # Fig. G28, 10 mm² three-phase, cos phi 0.35: 1.5 V/A/km.
    # 252 A x 0.12 km x 1.5 = 45.36 V of 400 V.
    run = _run("252", "120", three_phase=True, volts="400")
    assert voltage_drop.starting_percent(run, Decimal(10), _COPPER) == Decimal("11.34")


def test_fit_can_measure_the_start() -> None:
    run = _run("252", "200", three_phase=True, volts="400")
    checked = voltage_drop.fit(
        run, Decimal(10), Decimal(15), _COPPER, voltage_drop.starting_percent
    )
    assert checked is not None
    assert checked.section_mm2 == Decimal(16)

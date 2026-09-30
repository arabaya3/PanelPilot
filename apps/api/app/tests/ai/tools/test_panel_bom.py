"""Tests for `app/ai/tools/panel_bom.py`.

Mirrors the module 1:1 — if you add a function there, add its test here.

The heat balance is checked against the worked example in Rittal's
*Enclosure and process cooling*, p. 39 as printed; the lines against the
drive and cable tables' own tested selections.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.ai.tools import panel_bom
from app.core.errors import ValidationError
from app.models.schemas.calculations import (
    BomLineKind,
    ConductorMaterial,
    EnclosureConstraints,
    EnclosurePlacement,
    InstallationMethod,
    LoadScheduleItem,
)


def _constraints(**overrides: object) -> EnclosureConstraints:
    fields: dict[str, object] = {
        "width_mm": 600,
        "height_mm": 2000,
        "depth_mm": 500,
        "ingress_rating": "IP54",
        "placement": EnclosurePlacement.SINGLE_FREE_STANDING,
        "cable_installation_method": InstallationMethod.C,
        "ambient_temp_c": Decimal(25),
        "max_internal_temp_c": Decimal(35),
    }
    fields.update(overrides)
    return EnclosureConstraints.model_validate(fields)


def test_rittals_worked_example() -> None:
    """Rittal p. 39: 600 x 2000 x 500 free-standing, 900 W, 25 -> 35 °C.

    A = 1.8 x 2.0 x (0.6 + 0.5) + 1.4 x 0.6 x 0.5 = 4.38 m². The guide takes
    Qs = 5.5 x 4.38 x 10 as 242 W (exactly 240.9) and Qe = 900 - 242 = 658 W;
    unrounded, the heat to remove is 659.1 W.
    """
    area = panel_bom.effective_area_m2(
        width_m=Decimal("0.6"),
        height_m=Decimal("2.0"),
        depth_m=Decimal("0.5"),
        placement=EnclosurePlacement.SINGLE_FREE_STANDING,
    )
    assert area == Decimal("4.38")

    heat, cooling = panel_bom.enclosure_heat_load_w(
        items=[LoadScheduleItem(tag="X", description="all", dissipation_w=Decimal(900))],
        constraints=_constraints(),
    )
    assert heat == Decimal(900)
    assert cooling == Decimal("659.1")


@pytest.mark.parametrize(
    ("placement", "area"),
    [
        # W 0.6, H 2.0, D 0.5, each formula from the guide's table, p. 24.
        (EnclosurePlacement.SINGLE_WALL, "3.9"),  # 1.4·0.6·2.5 + 1.8·2.0·0.5
        (EnclosurePlacement.SUITE_END_FREE_STANDING, "3.98"),  # 1.4·0.5·2.6 + 1.8·0.6·2.0
        (EnclosurePlacement.SUITE_END_WALL, "3.5"),  # 1.4·2.0·1.1 + 1.4·0.6·0.5
        (EnclosurePlacement.SUITE_MIDDLE_FREE_STANDING, "3.58"),  # 2.16 + 0.42 + 1.0
        (EnclosurePlacement.SUITE_MIDDLE_WALL, "3.1"),  # 1.4·0.6·2.5 + 1.0
        (EnclosurePlacement.SUITE_MIDDLE_WALL_COVERED_ROOF, "2.89"),  # 1.68 + 0.21 + 1.0
    ],
)
def test_each_placement_uses_its_formula(placement: EnclosurePlacement, area: str) -> None:
    assert panel_bom.effective_area_m2(
        width_m=Decimal("0.6"), height_m=Decimal("2.0"), depth_m=Decimal("0.5"), placement=placement
    ) == Decimal(area)


def test_a_surface_that_sheds_everything_needs_no_cooling() -> None:
    _, cooling = panel_bom.enclosure_heat_load_w(
        items=[LoadScheduleItem(tag="X", description="PSU", dissipation_w=Decimal(100))],
        constraints=_constraints(),
    )
    assert cooling == 0


def test_no_permitted_rise_is_refused() -> None:
    with pytest.raises(ValidationError, match="above ambient"):
        panel_bom.enclosure_heat_load_w(
            items=[], constraints=_constraints(max_internal_temp_c=Decimal(25))
        )


def test_the_bom_lists_drive_cable_enclosure_and_cooling() -> None:
    result = panel_bom.build_bom(
        loads=[
            LoadScheduleItem(
                tag="M-101",
                description="conveyor",
                current_a=Decimal("40"),
                dissipation_w=Decimal("900"),
                variable_speed=True,
            ),
            LoadScheduleItem(tag="H-1", description="heater", current_a=Decimal("10")),
        ],
        constraints=_constraints(),
    )
    parts = [line.part_reference for line in result.lines]
    # 40 A at 35 °C inside the panel: 045A-3 (I2 45 A, no temperature derate).
    assert parts[0] == "ACS880-01-045A-3"
    # Method C, XLPE, 25 °C (k1 1.04), 2 grouped (k2 0.80): 40 / 0.832 = 48.1 A
    # -> 6 mm² (52 A); 10 A -> 1.5 mm² (22 A).
    assert parts[1] == "Cu XLPE 6 mm²"
    assert parts[2] == "Cu XLPE 1.5 mm²"
    assert parts[3] == "Enclosure 600x2000x500 IP54"
    assert parts[4] == "Cooling 659 W"
    assert result.cooling_required_w == Decimal("659.1")
    assert any("Terminals" in note for note in result.notes)


def test_aluminium_cables_size_from_the_aluminium_columns() -> None:
    result = panel_bom.build_bom(
        loads=[
            LoadScheduleItem(tag="M-101", description="conveyor", current_a=Decimal("40")),
            LoadScheduleItem(tag="H-1", description="heater", current_a=Decimal("10")),
        ],
        constraints=_constraints(cable_material=ConductorMaterial.ALUMINIUM),
    )
    cables = [line for line in result.lines if line.kind is BomLineKind.CABLE]
    # Same 48.1 A and 12.0 A as the copper case; Table 8 Al, method C, XLPE,
    # three loaded: 6 mm² carries 41 A, 10 mm² 57 A; 2.5 mm² (the first Al
    # row) 24 A.
    assert [line.part_reference for line in cables] == ["Al XLPE 10 mm²", "Al XLPE 2.5 mm²"]
    assert cables[0].details["material"] == "aluminium"


@pytest.mark.parametrize(
    ("supply", "drive"),
    [
        # 40 A at 35 °C: -5 at Un = 500 V (040A-5, I2 40 A); -7 at 690 V
        # (042A-7, I2 42 A); -7 at 575 V, UL ILd (035A-7, 41 A).
        ("500", "ACS880-01-040A-5"),
        ("690", "ACS880-01-042A-7"),
        ("575", "ACS880-01-035A-7"),
    ],
)
def test_the_supply_voltage_picks_the_drive_range(supply: str, drive: str) -> None:
    result = panel_bom.build_bom(
        loads=[
            LoadScheduleItem(
                tag="M-101", description="conveyor", current_a=Decimal("40"), variable_speed=True
            )
        ],
        constraints=_constraints(supply_voltage_v=Decimal(supply)),
    )
    assert result.lines[0].part_reference == drive
    assert result.lines[0].source.page in (236, 237, 240)


@pytest.mark.parametrize(
    ("loads", "message"),
    [
        ([], "empty"),
        (
            [
                LoadScheduleItem(tag="A", description="a", current_a=Decimal(1)),
                LoadScheduleItem(tag="A", description="b", current_a=Decimal(1)),
            ],
            "unique",
        ),
        ([LoadScheduleItem(tag="M", description="m", variable_speed=True)], "nameplate"),
        ([LoadScheduleItem(tag="M", description="m", power_kw=Decimal(5))], "nameplate"),
        (
            [
                LoadScheduleItem(
                    tag="M", description="m", current_a=Decimal(900), variable_speed=True
                )
            ],
            "M: no ACS880",
        ),
    ],
)
def test_an_inconsistent_schedule_is_refused(loads: list[LoadScheduleItem], message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        panel_bom.build_bom(loads=loads, constraints=_constraints())


def test_round_numbers_are_written_plainly() -> None:
    # Decimal.normalize() alone writes 10 as "1E+1": found on the live page as
    # "at 1E+1 K rise". A 10 mm² cable would have read "Cu XLPE 1E+1 mm²".
    result = panel_bom.build_bom(
        loads=[
            LoadScheduleItem(
                tag="M", description="m", current_a=Decimal(60), dissipation_w=Decimal(900)
            )
        ],
        constraints=_constraints(),
    )
    text = " ".join(f"{line.part_reference} {line.description}" for line in result.lines)
    assert "E+" not in text
    assert "Cu XLPE 10 mm²" in text
    assert "10 K rise" in text


def test_each_line_says_what_it_is_so_a_page_can_translate_it() -> None:
    from app.models.schemas.calculations import BomLineKind, BomNote

    result = panel_bom.build_bom(
        loads=[
            LoadScheduleItem(
                tag="M-101",
                description="conveyor",
                current_a=Decimal("40"),
                dissipation_w=Decimal("900"),
                variable_speed=True,
            ),
            LoadScheduleItem(tag="H-1", description="heater", current_a=Decimal("10")),
        ],
        constraints=_constraints(),
    )

    assert [line.kind for line in result.lines] == [
        BomLineKind.DRIVE,
        BomLineKind.CABLE,
        BomLineKind.CABLE,
        BomLineKind.ENCLOSURE,
        BomLineKind.COOLING,
    ]
    assert result.lines[0].details == {"tag": "M-101", "load": "conveyor"}
    assert result.lines[1].details == {
        "tag": "M-101",
        "load": "conveyor",
        "method": "C",
        "grouped": "2",
        "material": "copper",
    }
    assert result.lines[3].details == {"placement": "single_free_standing"}
    assert result.lines[4].details == {"cooling_w": "659", "rise_k": "10", "qw": "65.9"}
    # The heater gives no dissipation, so the cooling figure is incomplete.
    assert result.note_keys == [BomNote.NOT_INCLUDED, BomNote.INCOMPLETE_DISSIPATION]


def test_a_motor_started_across_the_line_gets_its_coordinated_starter() -> None:
    from app.models.schemas.calculations import BomLineKind, BomNote, StartType

    result = panel_bom.build_bom(
        loads=[
            LoadScheduleItem(
                tag="P-1",
                description="pump",
                power_kw=Decimal("55"),
                current_a=Decimal("98"),
                start=StartType.DOL_HEAVY,
            ),
            LoadScheduleItem(
                tag="F-1",
                description="fan",
                power_kw=Decimal("200"),
                current_a=Decimal("349"),
                start=StartType.STAR_DELTA,
            ),
        ],
        constraints=_constraints(cable_installation_method=InstallationMethod.F),
    )
    starter = [
        (line.kind, line.part_reference)
        for line in result.lines
        if line.kind in (BomLineKind.BREAKER, BomLineKind.CONTACTOR, BomLineKind.OVERLOAD)
    ]

    # ABB's worked examples, p. 134: the same devices, now in the BOM.
    assert starter == [
        (BomLineKind.BREAKER, "T4S250 PR222MP In160"),
        (BomLineKind.CONTACTOR, "A145"),
        (BomLineKind.BREAKER, "T5S630 PR221-I In630"),
        (BomLineKind.CONTACTOR, "A210"),
        (BomLineKind.CONTACTOR, "A210"),
        (BomLineKind.CONTACTOR, "A185"),
        (BomLineKind.OVERLOAD, "E320DU320"),
    ]
    roles = [
        line.details.get("role") for line in result.lines if line.kind is BomLineKind.CONTACTOR
    ]
    assert roles == ["line", "line", "delta", "star"]
    assert BomNote.FAULT_LEVEL_ASSUMED in result.note_keys


@pytest.mark.parametrize(
    ("load", "message"),
    [
        (
            LoadScheduleItem(
                tag="M",
                description="m",
                power_kw=Decimal(5),
                current_a=Decimal(11),
                variable_speed=True,
                start="dol",
            ),
            "not both",
        ),
        (
            LoadScheduleItem(tag="M", description="m", current_a=Decimal(11), start="dol"),
            "power and nameplate current",
        ),
    ],
)
def test_a_starter_needs_a_motor_it_can_be_selected_for(
    load: LoadScheduleItem, message: str
) -> None:
    with pytest.raises(ValidationError, match=message):
        panel_bom.build_bom(loads=[load], constraints=_constraints())

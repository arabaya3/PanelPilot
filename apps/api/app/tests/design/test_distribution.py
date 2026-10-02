"""Tests for `app/design/distribution.py`.

Mirrors the module 1:1 — if you add a function there, add its test here.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.core.errors import ValidationError
from app.design import designations, distribution, profile, voltage_drop
from app.models.schemas.design import (
    Circuit,
    DeviceKind,
    DistributionBoardRequest,
    LoadInput,
    LoadKind,
    Phase,
    Supply,
)


def _load(kind: LoadKind, kw: str, name: str, **extra: object) -> LoadInput:
    return LoadInput.model_validate(
        {"description": name, "load": kind, "power_kw": Decimal(kw), **extra}
    )


def _hall() -> DistributionBoardRequest:
    """A hall board shaped like the sample project: socket and lighting groups."""
    loads = [_load(LoadKind.SOCKET, "1.5", f"Sockets {i}") for i in range(1, 7)]
    loads += [_load(LoadKind.LIGHTING, "0.6", f"Lighting {i}") for i in range(1, 9)]
    loads += [
        _load(LoadKind.AIR_CONDITIONING, "4", "AC 1", phases=3, power_factor=Decimal("0.85")),
        _load(LoadKind.DATA, "1", "Data rack"),
    ]
    return DistributionBoardRequest(name="DBG-HALL", location="HALL", loads=loads)


def test_every_circuit_gets_a_breaker_and_cable_that_satisfy_the_overload_rule() -> None:
    board = distribution.design_distribution_board(_hall(), profile.default_profile())
    assert len(board.circuits) == 16
    for circuit in board.circuits:
        breaker = board.device(circuit.device_ids[0])
        assert breaker.rated_current_a is not None
        assert circuit.design_current_a <= breaker.rated_current_a
        assert circuit.cable_id is not None
    # Sockets: the company's 16 A on 2.5 mm²; lighting 10 A on 1.5 mm².
    sockets = board.circuits[0]
    assert board.device(sockets.device_ids[0]).rated_current_a == Decimal(16)
    assert board.cable(sockets.cable_id or "").cross_section_mm2 == Decimal("2.5")


def test_rcd_groups_follow_the_profile() -> None:
    board = distribution.design_distribution_board(_hall(), profile.default_profile())
    rcds = [d for d in board.devices if d.kind is DeviceKind.RESIDUAL_CURRENT_DEVICE]
    # 6 sockets, 8 lights and the AC are under 30 mA, six to a device: three groups.
    assert len(rcds) == 3
    assert all(d.residual_current_ma == Decimal(30) and d.poles == 4 for d in rcds)
    per_rcd = {r.id: [c for c in board.circuits if c.upstream_id == r.id] for r in rcds}
    assert all(1 <= len(cs) <= 6 for cs in per_rcd.values())
    # The data circuit has no residual current rule: straight off the busbar.
    data = next(c for c in board.circuits if c.description == "Data rack")
    assert data.upstream_id is None
    assert board.circuits[-1] == data
    for rcd in rcds:
        feeder = board.device(rcd.upstream_id or "")
        assert feeder.kind is DeviceKind.CIRCUIT_BREAKER
        assert rcd.rated_current_a is not None
        assert feeder.rated_current_a is not None
        # The RCCB is never rated below the breaker that protects it.
        assert rcd.rated_current_a >= feeder.rated_current_a
        largest = max(board.device(c.device_ids[0]).rated_current_a or 0 for c in per_rcd[rcd.id])
        assert feeder.rated_current_a >= largest


def test_phases_are_balanced_within_the_profile() -> None:
    board = distribution.design_distribution_board(_hall(), profile.default_profile())
    totals = distribution.phase_currents(board.circuits)
    most, least = max(totals.values()), min(totals.values())
    assert (most - least) / most * 100 <= Decimal(10)
    assert not any("imbalance" in note.text for note in board.notes)


def test_the_board_states_what_it_assumed() -> None:
    board = distribution.design_distribution_board(_hall(), profile.default_profile())
    joined = " ".join(note.text for note in board.notes)
    assert "cos phi 0.9" in joined
    assert "breaking capacity is left to be confirmed" in joined
    assert "not confirmed by the company's engineers" in joined
    assert "Leave 4 spare outgoing ways" in joined


def test_a_company_profile_changes_the_design() -> None:
    company = profile.load_profile(
        {
            "key": "acme",
            "circuit_rules": {"lighting": {"residual_current_ma": "300"}},
            "max_circuits_per_rcd": 10,
            "rules_confirmed_by": "Eng. A",
        }
    )
    board = distribution.design_distribution_board(_hall(), company)
    sensitivities = sorted(
        d.residual_current_ma
        for d in board.devices
        if d.kind is DeviceKind.RESIDUAL_CURRENT_DEVICE and d.residual_current_ma
    )
    assert sensitivities == [Decimal(30), Decimal(300)]
    assert not any("not confirmed" in note.text for note in board.notes)


def test_a_load_above_the_fixed_rating_is_sized_and_noted() -> None:
    request = DistributionBoardRequest(
        name="DB", loads=[_load(LoadKind.SOCKET, "4", "Big socket", power_factor=Decimal(1))]
    )
    board = distribution.design_distribution_board(request, profile.default_profile())
    # 4 kW at 231 V is 17.32 A: above the 16 A rule, so 20 A.
    assert board.device(board.circuits[0].device_ids[0]).rated_current_a == Decimal(20)
    assert any("exceeds the company's 16 A" in note.text for note in board.notes)


def test_single_phase_supply() -> None:
    request = DistributionBoardRequest(
        name="DB",
        supply=Supply(voltage_v=Decimal(230), phases=1),
        loads=[_load(LoadKind.SOCKET, "1", "S1"), _load(LoadKind.LIGHTING, "0.5", "L1")],
    )
    board = distribution.design_distribution_board(request, profile.default_profile())
    assert {c.phase for c in board.circuits} == {Phase.L1}
    assert board.device("incomer").poles == 2
    with pytest.raises(ValidationError, match="three-phase load on a single-phase supply"):
        distribution.design_distribution_board(
            request.model_copy(update={"loads": [_load(LoadKind.MOTOR, "3", "M", phases=3)]}),
            profile.default_profile(),
        )


def test_a_plc_switched_load_gets_a_contactor_after_its_breaker() -> None:
    request = DistributionBoardRequest(
        name="DB",
        loads=[
            _load(LoadKind.LIGHTING, "0.6", "Lights", controlled=True),
            _load(LoadKind.AIR_CONDITIONING, "9", "AC", phases=3, controlled=True),
            _load(LoadKind.SOCKET, "1.5", "Sockets"),
        ],
    )
    board = distribution.design_distribution_board(request, profile.default_profile())
    by_name = {c.description: c for c in board.circuits}
    lights, ac, sockets = by_name["Lights"], by_name["AC"], by_name["Sockets"]
    assert len(sockets.device_ids) == 1
    for circuit, poles in ((lights, 2), (ac, 4)):
        breaker, contactor = (board.device(i) for i in circuit.device_ids)
        assert contactor.kind is DeviceKind.CONTACTOR
        assert contactor.upstream_id == breaker.id
        assert contactor.poles == poles
        assert contactor.rated_current_a >= breaker.rated_current_a  # type: ignore[operator]
        assert contactor.rated_current_a in distribution.CONTACTOR_RATINGS
    assert any("AC-1" in note.text for note in board.notes)


def test_a_load_above_the_miniature_breakers_gets_an_unselected_mccb() -> None:
    request = DistributionBoardRequest(
        name="MDB", loads=[_load(LoadKind.OTHER, "90", "Chiller", phases=3)]
    )
    board = distribution.design_distribution_board(request, profile.default_profile())
    breaker = board.device(board.circuits[0].device_ids[0])
    assert breaker.rated_current_a is None
    assert breaker.curve is None
    cable = board.cable(board.circuits[0].cable_id)  # type: ignore[arg-type]
    # Sized for Ib (about 144 A), not refused.
    assert cable.cross_section_mm2 >= Decimal(35)
    assert any(
        note.code == "mccb_needed" and note.params["load"] == "Chiller" for note in board.notes
    )


def test_an_unprotectable_load_is_refused_by_name() -> None:
    request = DistributionBoardRequest(
        name="DB", loads=[_load(LoadKind.OTHER, "3000", "Chiller", phases=3)]
    )
    with pytest.raises(ValidationError, match="Chiller"):
        distribution.design_distribution_board(request, profile.default_profile())


def test_an_incomer_above_125_a_is_left_unselected() -> None:
    loads = [_load(LoadKind.OTHER, "40", f"Load {i}", phases=3) for i in range(3)]
    board = distribution.design_distribution_board(
        DistributionBoardRequest(name="DB", loads=loads), profile.default_profile()
    )
    assert board.device("incomer").rated_current_a is None
    assert any("moulded-case" in note.text for note in board.notes)


def test_it_designates_cleanly() -> None:
    board = distribution.design_distribution_board(_hall(), profile.default_profile())
    issued = designations.designate_board(board, profile.default_profile())
    names = [str(d.designation) for d in issued.devices]
    assert len(names) == len(set(names))
    assert str(issued.device("incomer").designation) == "=DBG-HALL+HALL-Q1"


def test_balance_phases() -> None:
    phases = distribution.balance_phases(
        [
            (0, Decimal(10), False),
            (1, Decimal(10), False),
            (2, Decimal(10), False),
            (3, Decimal(5), True),
        ]
    )
    assert phases == {0: Phase.L1, 1: Phase.L2, 2: Phase.L3, 3: Phase.THREE_PHASE}
    # Largest first: the 9 A load lands alone, the two 4 A loads share.
    phases = distribution.balance_phases(
        [
            (0, Decimal(4), False),
            (1, Decimal(9), False),
            (2, Decimal(4), False),
            (3, Decimal(1), False),
        ]
    )
    assert phases[1] == Phase.L1
    assert {phases[0], phases[2]} == {Phase.L2, Phase.L3}


def test_phase_currents() -> None:
    def circuit(phase: Phase, amps: str) -> Circuit:
        return Circuit(
            id=amps,
            description="x",
            load=LoadKind.OTHER,
            power_kw=Decimal(1),
            design_current_a=Decimal(amps),
            phase=phase,
        )

    totals = distribution.phase_currents([circuit(Phase.L1, "2"), circuit(Phase.THREE_PHASE, "3")])
    assert totals == {Phase.L1: Decimal(5), Phase.L2: Decimal(3), Phase.L3: Decimal(3)}


def test_a_small_split_unit_still_gets_16_a() -> None:
    request = DistributionBoardRequest(
        name="DB", loads=[_load(LoadKind.AIR_CONDITIONING, "1.2", "Split unit")]
    )
    board = distribution.design_distribution_board(request, profile.default_profile())
    breaker = board.device(board.circuits[0].device_ids[0])
    cable = board.cable(board.circuits[0].cable_id)  # type: ignore[arg-type]
    assert breaker.rated_current_a == 16
    assert cable.cross_section_mm2 == Decimal("2.5")


def test_a_motor_gets_its_starter_and_a_cable_for_the_relay_setting() -> None:
    request = DistributionBoardRequest(
        name="MCC",
        loads=[
            _load(LoadKind.MOTOR, "30", "Fan", phases=3, starter="star_delta"),
            _load(LoadKind.LIGHTING, "1", "Lights"),
        ],
    )
    board = distribution.design_distribution_board(request, profile.default_profile())
    fan = next(c for c in board.circuits if c.description == "Fan")
    assert fan.starter == "star_delta"
    assert fan.design_current_a == Decimal(56)
    assert fan.upstream_id is None  # Motors take no residual current group by default.
    assert [board.device(d).kind for d in fan.device_ids][-1] is DeviceKind.OVERLOAD_RELAY
    assert board.device(fan.device_ids[0]).upstream_id == "incomer"
    cable = board.cable(fan.cable_id)  # type: ignore[arg-type]
    assert cable.cores == 7
    assert any("Fan: Ir 56 A" in note.text for note in board.notes)
    # The incomer carries the motor's line current, not its phase current.
    assert board.device("incomer").rated_current_a >= Decimal(56)  # type: ignore[operator]


def test_a_long_cable_is_enlarged_for_voltage_drop() -> None:
    request = DistributionBoardRequest(
        name="DB",
        loads=[_load(LoadKind.LIGHTING, "2", "Car park lights", length_m=Decimal(60))],
    )
    board = distribution.design_distribution_board(request, profile.default_profile())
    (circuit,) = board.circuits
    cable = board.cable(circuit.cable_id or "")
    assert cable.length_m == 60
    assert cable.cross_section_mm2 > Decimal("1.5")
    assert circuit.voltage_drop_percent is not None
    assert circuit.voltage_drop_percent <= 3
    assert "voltage_drop_upsized" in [n.code for n in board.notes]
    assert "voltage_drop_unchecked" not in [n.code for n in board.notes]


def test_drop_beyond_any_section_is_said() -> None:
    request = DistributionBoardRequest(
        name="DB",
        loads=[_load(LoadKind.OTHER, "20", "Pump house", phases=3, length_m=Decimal(10000))],
    )
    board = distribution.design_distribution_board(request, profile.default_profile())
    (exceeded,) = [n for n in board.notes if n.code == "voltage_drop_exceeded"]
    assert exceeded.params["load"] == "Pump house"
    assert exceeded.params["upstream"] == "0"


def test_upstream_drop_is_taken_from_every_circuit() -> None:
    request = DistributionBoardRequest(
        name="DB",
        loads=[_load(LoadKind.SOCKET, "1", "Sockets", length_m=Decimal(20))],
    )
    company = profile.default_profile()
    alone = distribution.design_distribution_board(request, company)
    fed = distribution.design_distribution_board(
        request, company, voltage_drop.Budget(upstream_percent=Decimal("4.5"))
    )
    assert alone.cables[0].cross_section_mm2 < fed.cables[0].cross_section_mm2
    assert "voltage_drop_upstream" in [n.code for n in fed.notes]


def test_circuits_without_a_length_are_counted() -> None:
    board = distribution.design_distribution_board(_hall(), profile.default_profile())
    (unchecked,) = [n for n in board.notes if n.code == "voltage_drop_unchecked"]
    assert unchecked.params["count"] == "16"


def test_a_star_delta_motor_is_read_as_three_loops() -> None:
    request = DistributionBoardRequest(
        name="DB",
        loads=[
            _load(
                LoadKind.MOTOR, "30", "Pump", phases=3, starter="star_delta", length_m=Decimal(50)
            )
        ],
    )
    board = distribution.design_distribution_board(request, profile.default_profile())
    (circuit,) = board.circuits
    section = board.cable(circuit.cable_id or "").cross_section_mm2
    # Each winding: Ir/√3 out and back, across the 400 V line voltage.
    loop = voltage_drop.Run(
        circuit.design_current_a / Decimal(3).sqrt(), Decimal(50), False, Decimal(400)
    )
    expected = voltage_drop.percent(loop, section, request.conditions)
    assert circuit.voltage_drop_percent == expected.quantize(Decimal("0.01"))

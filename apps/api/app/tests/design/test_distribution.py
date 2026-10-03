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


def test_a_load_above_the_miniature_breakers_gets_a_moulded_case_breaker() -> None:
    request = DistributionBoardRequest(
        name="MDB",
        supply=Supply(fault_level_ka=Decimal(25)),
        loads=[_load(LoadKind.OTHER, "90", "Chiller", phases=3, length_m=Decimal(40))],
    )
    board = distribution.design_distribution_board(request, profile.default_profile())
    breaker = board.device(board.circuits[0].device_ids[0])
    # Ib about 144 A: XT3 TMD 160, I3 fixed at 10 In.
    assert breaker.part_key == "ABB/XT3N 250 TMD 160"
    assert breaker.rated_current_a == 160
    assert breaker.curve is None
    assert breaker.breaking_capacity_ka == 36
    (selected,) = [n for n in board.notes if n.code == "mccb_selected"]
    assert selected.params["trip"] == "1600"
    assert "1SDC210033D0203" in selected.params["source"]
    cable = board.cable(board.circuits[0].cable_id)  # type: ignore[arg-type]
    assert cable.cross_section_mm2 >= Decimal(50)


def test_a_load_beyond_the_moulded_case_breakers_is_left_unselected() -> None:
    request = DistributionBoardRequest(
        name="MDB",
        supply=Supply(voltage_v=Decimal(230), phases=1),
        loads=[_load(LoadKind.OTHER, "30", "Heater")],
    )
    board = distribution.design_distribution_board(request, profile.default_profile())
    breaker = board.device(board.circuits[0].device_ids[0])
    assert breaker.rated_current_a is None
    assert breaker.curve is None
    assert any(
        note.code == "mccb_needed" and note.params["load"] == "Heater" for note in board.notes
    )


def test_an_unprotectable_load_is_refused_by_name() -> None:
    request = DistributionBoardRequest(
        name="DB", loads=[_load(LoadKind.OTHER, "3000", "Chiller", phases=3)]
    )
    with pytest.raises(ValidationError, match="Chiller"):
        distribution.design_distribution_board(request, profile.default_profile())


def test_an_incomer_above_125_a_is_a_moulded_case_breaker() -> None:
    loads = [_load(LoadKind.OTHER, "40", f"Load {i}", phases=3) for i in range(3)]
    board = distribution.design_distribution_board(
        DistributionBoardRequest(name="DB", loads=loads), profile.default_profile()
    )
    incomer = board.device("incomer")
    assert incomer.part_key is not None
    assert incomer.part_key.startswith("ABB/XT3N 250 TMD ")
    assert incomer.poles == 4
    assert any(n.code == "mccb_selected" and n.params["load"] == "Incomer" for n in board.notes)


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


@pytest.mark.parametrize(
    ("fault", "rating"),
    [("4.5", "6"), ("6", "6"), ("6.1", "10"), ("20", "25"), ("50", "50"), ("51", None)],
)
def test_breaking_capacity(fault: str, rating: str | None) -> None:
    expected = Decimal(rating) if rating else None
    assert distribution.breaking_capacity(Decimal(fault)) == expected


def test_every_breaker_clears_the_fault_level() -> None:
    request = _hall().model_copy(update={"supply": Supply(fault_level_ka=Decimal(12))})
    board = distribution.design_distribution_board(request, profile.default_profile())
    breakers = [d for d in board.devices if d.kind is DeviceKind.CIRCUIT_BREAKER]
    assert {d.breaking_capacity_ka for d in breakers} == {Decimal(15)}
    (rated,) = [n for n in board.notes if n.code == "breaking_capacity"]
    assert rated.params == {"rating": "15", "fault": "12"}
    assert "no_fault_level" not in [n.code for n in board.notes]


def test_a_fault_beyond_every_breaker_is_said() -> None:
    request = _hall().model_copy(update={"supply": Supply(fault_level_ka=Decimal(65))})
    board = distribution.design_distribution_board(request, profile.default_profile())
    assert board.device("incomer").breaking_capacity_ka is None
    assert "breaking_capacity_beyond" in [n.code for n in board.notes]


def test_a_starter_keeps_its_coordinated_breaker() -> None:
    request = DistributionBoardRequest(
        name="DB",
        supply=Supply(fault_level_ka=Decimal(20)),
        loads=[_load(LoadKind.MOTOR, "7.5", "Fan", phases=3, starter="dol")],
    )
    board = distribution.design_distribution_board(request, profile.default_profile())
    starter = board.device(board.circuits[0].device_ids[0])
    assert starter.breaking_capacity_ka is None
    assert board.device("incomer").breaking_capacity_ka == 25


def test_no_fault_level_is_said() -> None:
    board = distribution.design_distribution_board(_hall(), profile.default_profile())
    assert "no_fault_level" in [n.code for n in board.notes]
    assert all(d.breaking_capacity_ka is None for d in board.devices)


def test_group_breakers_discriminate_with_the_breakers_after_them() -> None:
    board = distribution.design_distribution_board(_hall(), profile.default_profile())
    for group in [d for d in board.devices if d.id.startswith("g") and d.id.endswith("-breaker")]:
        after = [
            board.device(c.device_ids[0]).rated_current_a or Decimal(0)
            for c in board.circuits
            if c.upstream_id == group.id.replace("-breaker", "-rcd")
        ]
        assert group.rated_current_a is not None
        assert group.rated_current_a >= Decimal("1.6") * max(after)
        # Its RCCB is rated for it.
        rcd = board.device(group.id.replace("-breaker", "-rcd"))
        assert rcd.rated_current_a is not None
        assert rcd.rated_current_a >= group.rated_current_a
    assert "discrimination" in [n.code for n in board.notes]


def test_the_incomer_discriminates_with_what_it_feeds() -> None:
    board = distribution.design_distribution_board(_hall(), profile.default_profile())
    incomer = board.device("incomer").rated_current_a
    fed = [d.rated_current_a for d in board.devices if d.upstream_id == "incomer"]
    assert incomer is not None
    assert all(incomer >= Decimal("1.6") * (rating or 0) for rating in fed)


def test_the_company_ratio_applies() -> None:
    company = profile.default_profile().model_copy(update={"discrimination_ratio": Decimal(1)})
    board = distribution.design_distribution_board(_hall(), company)
    codes = [n.code for n in board.notes]
    assert "discrimination_group_raised" not in codes
    assert next(n for n in board.notes if n.code == "discrimination").params == {"ratio": "1"}


def test_a_group_its_rccb_cannot_follow_is_said() -> None:
    company = profile.default_profile().model_copy(update={"discrimination_ratio": Decimal(10)})
    board = distribution.design_distribution_board(_hall(), company)
    assert "discrimination_group_not_met" in [n.code for n in board.notes]


def test_a_feeder_discriminates_with_the_breakers_after_it() -> None:
    request = DistributionBoardRequest(
        name="MDB",
        loads=[_load(LoadKind.SUB_BOARD, "5", "Feeder to DB-1", phases=3, feeds="DB-1")],
    )
    board = distribution.design_distribution_board(
        request, profile.default_profile(), sub_board_after_a={"DB-1": Decimal(25)}
    )
    breaker = board.device(board.circuits[0].device_ids[0])
    assert breaker.rated_current_a == 40
    (raised,) = [n for n in board.notes if n.code == "discrimination_feeder_raised"]
    assert raised.params == {"board": "DB-1", "rated": "40", "ratio": "1.6", "after": "25"}


@pytest.mark.parametrize(("after", "expected"), [("16", "32"), ("63", "125"), ("80", None)])
def test_group_rating(after: str, expected: str | None) -> None:
    rating = distribution._group_rating(Decimal(20), Decimal(after), Decimal("1.6"))
    assert rating == (Decimal(expected) if expected else None)


def test_a_sub_board_has_a_switch_rated_for_the_breaker_that_feeds_it() -> None:
    request = _hall().model_copy(update={"fed_from": "MDB"})
    board = distribution.design_distribution_board(
        request, profile.default_profile(), supply_breaker_a=Decimal(63)
    )
    incomer = board.device("incomer")
    assert incomer.kind is DeviceKind.SWITCH_DISCONNECTOR
    assert incomer.rated_current_a == 63
    assert incomer.breaking_capacity_ka is None
    assert "incomer_isolator" in [n.code for n in board.notes]
    # It does not trip, so nothing is raised to discriminate with it.
    assert "discrimination_incomer_raised" not in [n.code for n in board.notes]


def test_a_switch_beyond_every_rating_is_said() -> None:
    request = _hall().model_copy(update={"fed_from": "MDB"})
    board = distribution.design_distribution_board(
        request, profile.default_profile(), supply_breaker_a=Decimal(400)
    )
    assert board.device("incomer").rated_current_a is None
    assert "isolator_unselected" in [n.code for n in board.notes]


def test_a_long_motor_cable_is_enlarged_for_starting() -> None:
    # A company that holds starting to 8 %: at 6 x Ir the start, not the
    # running current, then decides the cable.
    company = profile.default_profile().model_copy(
        update={"max_starting_voltage_drop_percent": Decimal(8)}
    )

    def board(starter: str, metres: str) -> list[str]:
        request = DistributionBoardRequest(
            name="DB",
            loads=[
                _load(
                    LoadKind.MOTOR,
                    "22",
                    "Pump",
                    phases=3,
                    starter=starter,
                    length_m=Decimal(metres),
                )
            ],
        )
        return [n.code for n in distribution.design_distribution_board(request, company).notes]

    assert "starting_drop_upsized" in board("dol", "200")
    # A third of the current in star-delta, and a drive starts at Ir.
    assert "starting_drop_upsized" not in board("star_delta", "200")
    assert "starting_drop_upsized" not in board("drive", "200")


def _earthed(ze: str | None, *loads: LoadInput, earthing: str = "TN-S") -> DistributionBoardRequest:
    supply = Supply(earthing=earthing, earth_loop_ohm=Decimal(ze) if ze else None)
    return DistributionBoardRequest(name="DB", supply=supply, loads=list(loads))


def test_a_long_cable_is_enlarged_for_earth_fault_disconnection() -> None:
    request = _earthed("0.8", _load(LoadKind.DATA, "2", "Server room", length_m=Decimal(40)))
    board = distribution.design_distribution_board(request, profile.default_profile())
    (circuit,) = board.circuits
    breaker = board.device(circuit.device_ids[0])
    assert breaker.rated_current_a == 16
    # C16 trips at once up to 1.366 Ω; 2.5 mm² over 40 m with Ze 0.8 is 1.46 Ω.
    assert board.cable(circuit.cable_id or "").cross_section_mm2 == 4
    assert circuit.earth_loop_ohm is not None
    assert circuit.earth_loop_ohm <= Decimal("1.366")
    (upsized,) = [n for n in board.notes if n.code == "earth_fault_upsized"]
    assert upsized.params["sized"] == "2.5"
    assert "earth_fault_basis" in [n.code for n in board.notes]
    # The drop shown is the larger cable's.
    assert circuit.voltage_drop_percent is not None
    assert circuit.voltage_drop_percent < Decimal("2.5")


def test_earth_fault_beyond_any_section_is_said() -> None:
    request = _earthed("1.5", _load(LoadKind.DATA, "2", "Server room", length_m=Decimal(10)))
    board = distribution.design_distribution_board(request, profile.default_profile())
    (exceeded,) = [n for n in board.notes if n.code == "earth_fault_exceeded"]
    assert exceeded.params["load"] == "Server room"
    assert exceeded.params["curve"] == "C"


def test_a_circuit_under_an_rcd_disconnects_by_it() -> None:
    request = _earthed("1.5", _load(LoadKind.SOCKET, "1", "Sockets", length_m=Decimal(10)))
    board = distribution.design_distribution_board(request, profile.default_profile())
    codes = [n.code for n in board.notes]
    assert "earth_fault_exceeded" not in codes
    assert "earth_fault_unchecked" not in codes
    assert board.circuits[0].earth_loop_ohm is None


def test_tt_needs_an_rcd_on_every_circuit() -> None:
    request = _earthed(
        None,
        _load(LoadKind.DATA, "1", "Data rack", length_m=Decimal(10)),
        _load(LoadKind.SOCKET, "1", "Sockets"),
        earthing="TT",
    )
    board = distribution.design_distribution_board(request, profile.default_profile())
    (tt,) = [n for n in board.notes if n.code == "earth_fault_tt_no_rcd"]
    assert tt.params["load"] == "Data rack"
    assert "earth_fault_no_ze" not in [n.code for n in board.notes]


def test_earth_fault_without_ze_or_length_is_said() -> None:
    no_ze = distribution.design_distribution_board(
        _earthed(None, _load(LoadKind.DATA, "1", "Data rack", length_m=Decimal(10))),
        profile.default_profile(),
    )
    assert "earth_fault_no_ze" in [n.code for n in no_ze.notes]
    no_length = distribution.design_distribution_board(
        _earthed("0.35", _load(LoadKind.DATA, "1", "Data rack")), profile.default_profile()
    )
    (unchecked,) = [n for n in no_length.notes if n.code == "earth_fault_unchecked"]
    assert unchecked.params["count"] == "1"


def test_a_starter_breaker_disconnects_at_its_magnetic_threshold() -> None:
    motor = _load(LoadKind.MOTOR, "7.5", "Fan", phases=3, starter="dol", length_m=Decimal(30))
    board = distribution.design_distribution_board(
        _earthed("0.35", motor), profile.default_profile()
    )
    codes = [n.code for n in board.notes]
    assert "earth_fault_motor_basis" in codes
    assert "earth_fault_unchecked" not in codes
    assert board.circuits[0].earth_loop_ohm is not None

    far = distribution.design_distribution_board(_earthed("1.5", motor), profile.default_profile())
    (exceeded,) = [n for n in far.notes if n.code == "earth_fault_magnetic_exceeded"]
    assert exceeded.params["load"] == "Fan"
    assert Decimal(exceeded.params["trip"]) > 0


def test_a_drive_circuit_stays_unchecked_for_earth_fault() -> None:
    motor = _load(LoadKind.MOTOR, "7.5", "Fan", phases=3, starter="drive", length_m=Decimal(30))
    board = distribution.design_distribution_board(
        _earthed("0.35", motor), profile.default_profile()
    )
    (unchecked,) = [n for n in board.notes if n.code == "earth_fault_unchecked"]
    assert unchecked.params["count"] == "1"


def test_a_long_cable_is_enlarged_until_a_far_short_circuit_trips_at_once() -> None:
    # Under an RCD, so only the short circuit check can enlarge it for its breaker.
    request = DistributionBoardRequest(
        name="DB",
        loads=[_load(LoadKind.SOCKET, "0.5", "Garden sockets", length_m=Decimal(70))],
    )
    board = distribution.design_distribution_board(request, profile.default_profile())
    (upsized,) = [n for n in board.notes if n.code == "short_circuit_min_upsized"]
    assert upsized.params["trip"] == "160"
    cable = board.cables[0]
    assert cable.cross_section_mm2 == Decimal(upsized.params["section"])
    assert cable.withstand_ka2s is not None
    assert "short_circuit_withstand" not in [n.code for n in board.notes]


def test_the_board_asks_for_the_breakers_let_through_at_its_fault_level() -> None:
    request = _hall().model_copy(update={"supply": Supply(fault_level_ka=Decimal(10))})
    board = distribution.design_distribution_board(request, profile.default_profile())
    (withstand,) = [n for n in board.notes if n.code == "short_circuit_withstand"]
    assert withstand.params["fault"] == "10"


def test_demand_factors_lower_the_incomer() -> None:
    company = profile.default_profile()
    full = distribution.design_distribution_board(_hall(), company)
    diverse = company.model_copy(
        update={"demand_factors": {LoadKind.SOCKET: Decimal("0.4"), LoadKind.LIGHTING: Decimal(1)}}
    )
    board = distribution.design_distribution_board(_hall(), diverse)
    (made,) = [n for n in board.notes if n.code == "demand_factors"]
    assert Decimal(made.params["demand"]) < Decimal(made.params["connected"])
    assert "demand_factors" not in [n.code for n in full.notes]


def test_demand_currents_never_drop_below_the_largest_circuit() -> None:
    circuits = [
        Circuit(
            id="c1",
            description="Heater",
            load=LoadKind.WATER_HEATER,
            power_kw=Decimal(3),
            design_current_a=Decimal(13),
            phase=Phase.L1,
        ),
        Circuit(
            id="c2",
            description="Heater 2",
            load=LoadKind.WATER_HEATER,
            power_kw=Decimal(1),
            design_current_a=Decimal(4),
            phase=Phase.L1,
        ),
    ]
    company = profile.default_profile().model_copy(
        update={"demand_factors": {LoadKind.WATER_HEATER: Decimal("0.5")}}
    )
    currents = distribution.demand_currents(circuits, company)
    assert currents[Phase.L1] == 13
    assert currents[Phase.L2] == 0


def test_a_load_no_single_cable_carries_runs_in_parallel() -> None:
    from app.models.schemas.design import InstallationConditions

    request = DistributionBoardRequest(
        name="DB",
        conditions=InstallationConditions(installation_method="C"),
        loads=[
            _load(
                LoadKind.OTHER,
                "500",
                "Chiller plant",
                phases=3,
                power_factor=Decimal("0.9"),
                length_m=Decimal(60),
            )
        ],
    )
    board = distribution.design_distribution_board(request, profile.default_profile())
    (cable,) = board.cables
    assert cable.parallel > 1
    assert cable.size.startswith(f"{cable.parallel}x5G")
    (made,) = [n for n in board.notes if n.code == "parallel_cables"]
    assert made.params["runs"] == str(cable.parallel)
    # Each run carries its share: the drop is that of one run at I / n.
    assert board.circuits[0].voltage_drop_percent is not None

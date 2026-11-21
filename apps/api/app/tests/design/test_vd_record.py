"""The voltage-drop calculation record (``pp-vdrec/1``, ADR 0004), from the design that runs.

Expected drops are worked by hand from Schneider EIG 2010 Fig. G28 cells
stated beside each case (drop = cell x I x L/1000, as a share of the voltage
used), never read back from the engine. The record must say what the design
actually did, so its final figures are also checked against the saved board.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest

from app.design import snapshot, vd_record
from app.domain import design as design_domain
from app.models.schemas.design import DesignProject, ProjectDesignRequest
from app.tests.design.model.compat_support import DESIGNED

_SHA = "0" * 64


def _designed(
    boards: list[dict[str, Any]], profile: dict[str, Any] | None = None
) -> tuple[DesignProject, dict[str, Any]]:
    request = ProjectDesignRequest.model_validate(
        {"info": {"name": "SYNTHETIC record"}, "boards": boards, "profile": profile}
    )
    trace = vd_record.ProjectTrace()
    designed, _company = design_domain.build_design(request, trace)
    record = vd_record.build(trace, snapshot_sha256=_SHA, profile_sha256=_SHA, rules_version="r")
    return designed, record


def _circuit(record: dict[str, Any], board: str, description: str) -> dict[str, Any]:
    found = next(b for b in record["boards"] if b["board"] == board)
    return next(c for c in found["circuits"] if c["description"] == description)


def _cells(attempt: dict[str, Any]) -> dict[str, str]:
    return {r["column"]: r["cell_v_per_a_km"] for r in attempt["readings"]}


def _saved(designed: DesignProject, board: str, description: str) -> tuple[Any, Any]:
    found = next(b for b in designed.boards if b.name == board)
    circuit = next(c for c in found.circuits if c.description == description)
    cable = found.cable(circuit.cable_id) if circuit.cable_id else None
    return circuit.voltage_drop_percent, cable.cross_section_mm2 if cable else None


def test_a_motor_enlarged_for_earth_fault_records_the_final_cable() -> None:
    # 4 kW DOL, Ir 8.6 A, 60 m, 400 V, TN-S Ze 0.35 ohm: earth fault 1.5 -> 4 mm².
    designed, record = _designed(
        [
            {
                "name": "MDB",
                "supply": {"earthing": "TN-S", "earth_loop_ohm": "0.35"},
                "loads": [
                    {
                        "description": "Fan",
                        "load": "motor",
                        "power_kw": "4",
                        "phases": 3,
                        "starter": "dol",
                        "length_m": "60",
                    }
                ],
            }
        ]
    )
    fan = _circuit(record, "MDB", "Fan")
    assert fan["current"]["design_current_a"] == "8.6"
    assert fan["voltage"] == {"value_v": "400", "basis": "supply line voltage"}
    assert {"step": "earth_fault", "from_mm2": "1.5", "section_mm2": "4"} in fan["sections"]
    running = fan["running"]
    assert running["status"] == "checked"
    # The running check chose 1.5 mm²; the final reading is on the 4 mm² cable.
    assert running["selected_by_check_mm2"] == "1.5"
    final = running["final"]
    assert final["section_mm2"] == "4"
    # Fig. G28 4 mm² three-phase: lighting 9.5, motor (cos 0.8) 8.
    assert _cells(final) == {"three-phase lighting": "9.5", "three-phase motor cos phi 0.8": "8"}
    # 9.5 x 8.6 x 0.060 = 4.902 V of 400 V = 1.2255 % -> 1.23 %.
    assert Decimal(final["raw_percent"]) == Decimal("1.2255")
    assert final["rounded_percent"] == "1.23"
    assert fan["saved_voltage_drop_percent"] == "1.23"
    assert fan["final_section_mm2"] == "4"
    assert _saved(designed, "MDB", "Fan") == (Decimal("1.23"), Decimal(4))
    assert running["within_limit"] is True
    assert running["limit_source"] == "company profile default_max_voltage_drop_percent"


def test_a_motor_enlarged_for_starting_records_both_drops_on_the_final_cable() -> None:
    # 22 kW DOL, Ir 42 A, 120 m, starting limit 8 %: starting 10 -> 16 mm².
    designed, record = _designed(
        [
            {
                "name": "MCC",
                "loads": [
                    {
                        "description": "Pump",
                        "load": "motor",
                        "power_kw": "22",
                        "phases": 3,
                        "starter": "dol",
                        "length_m": "120",
                    }
                ],
            }
        ],
        profile={"key": "synth", "max_starting_voltage_drop_percent": "8"},
    )
    pump = _circuit(record, "MCC", "Pump")
    assert {"step": "starting_drop", "from_mm2": "10", "section_mm2": "16"} in pump["sections"]
    final = pump["running"]["final"]
    # Fig. G28 16 mm² three-phase: lighting 2.4, motor 2.05 -> 2.4 x 42 x 0.12 = 12.096 V.
    assert _cells(final) == {"three-phase lighting": "2.4", "three-phase motor cos phi 0.8": "2.05"}
    assert Decimal(final["raw_percent"]) == Decimal("3.024")
    assert pump["saved_voltage_drop_percent"] == "3.02"
    assert _saved(designed, "MCC", "Pump") == (Decimal("3.02"), Decimal(16))
    starting = pump["starting"]
    assert starting["limit_percent"] == "8"
    assert starting["limit_source"] == "company profile max_starting_voltage_drop_percent"
    # Start-up: 6 x 42 = 252 A, cos 0.35 column, 16 mm² three-phase 1 V/(A·km):
    # 1 x 252 x 0.12 = 30.24 V of 400 V = 7.56 %.
    assert starting["final"]["section_mm2"] == "16"
    assert _cells(starting["final"]) == {"three-phase motor cos phi 0.35": "1"}
    assert Decimal(starting["final"]["raw_percent"]) == Decimal("7.56")
    assert starting["within_limit"] is True


def test_an_unchanged_circuit_records_its_inputs_and_defaults() -> None:
    # 1 kW single-phase data circuit, 20 m, no power factor entered: Ib =
    # 1000 / (231 x 0.9) = 4.81 A on 2.5 mm² (lighting 18, motor 14.4 -> 18):
    # 18 x 4.81 x 0.020 = 1.7316 V of 231 V = 0.7496 % -> 0.75 %.
    designed, record = _designed(
        [
            {
                "name": "DB",
                "loads": [
                    {"description": "Rack", "load": "data", "power_kw": "1", "length_m": "20"}
                ],
            }
        ]
    )
    rack = _circuit(record, "DB", "Rack")
    assert rack["inputs"]["power_factor"] == {
        "value": "0.9",
        "origin": "software default (DEFAULT_POWER_FACTOR), none entered",
    }
    assert rack["inputs"]["length_m"] == {"value": "20", "origin": "entered"}
    assert rack["current"]["design_current_a"] == "4.81"
    assert rack["voltage"] == {
        "value_v": "231",
        "basis": "phase voltage: supply line voltage / sqrt 3, rounded to whole volts",
    }
    # Sized at 1.5 mm² for its current; the default profile's data rule asks 2.5 mm².
    assert rack["sections"] == [
        {"step": "current_carrying_capacity", "section_mm2": "1.5"},
        {"step": "company_circuit_rule", "from_mm2": "1.5", "section_mm2": "2.5"},
    ]
    final = rack["running"]["final"]
    assert _cells(final) == {
        "single-phase lighting": "18",
        "single-phase motor cos phi 0.8": "14.4",
    }
    assert final["readings"][0]["edition"] == "2010"
    assert final["rounded_percent"] == "0.75"
    assert rack["saved_voltage_drop_percent"] == "0.75"
    assert _saved(designed, "DB", "Rack")[0] == Decimal("0.75")
    assert rack["running"]["total_complete"] is True


def test_a_remaining_exceeded_drop_is_recorded_as_not_within_on_the_final_cable() -> None:
    # 4 kW single-phase data, 300 m, company limit 0.5 %: short circuit 2.5 -> 25 mm².
    designed, record = _designed(
        [
            {
                "name": "MDB",
                "supply": {"earthing": "TN-S", "earth_loop_ohm": "0.35"},
                "loads": [
                    {"description": "Link", "load": "data", "power_kw": "4", "length_m": "300"}
                ],
            }
        ],
        profile={"key": "synth", "default_max_voltage_drop_percent": "0.5"},
    )
    link = _circuit(record, "MDB", "Link")
    running = link["running"]
    # Every tabulated section from 2.5 mm² up was tried, and none was within.
    tried = [a["section_mm2"] for a in running["attempts"]]
    assert tried[0] == "2.5"
    assert "300" in tried
    assert {"step": "short_circuit", "from_mm2": "2.5", "section_mm2": "25"} in link["sections"]
    # 25 mm² single-phase: 1.8 x 19.24 x 0.3 = 10.3896 V of 231 V = 4.4977 % -> 4.50 %.
    assert running["final"]["section_mm2"] == "25"
    assert running["final"]["rounded_percent"] == "4.50"
    assert running["within_limit"] is False
    assert running["limit_percent"] == "0.5"
    assert _saved(designed, "MDB", "Link") == (Decimal("4.50"), Decimal(25))


def test_a_missing_length_is_recorded_as_unchecked() -> None:
    designed, record = _designed(
        [
            {
                "name": "MDB",
                "loads": [
                    {
                        "description": "Fan",
                        "load": "motor",
                        "power_kw": "4",
                        "phases": 3,
                        "starter": "dol",
                    }
                ],
            }
        ]
    )
    fan = _circuit(record, "MDB", "Fan")
    assert fan["inputs"]["length_m"] == {"value": None, "origin": "not given"}
    assert fan["running"] == {"kind": "running", "status": "unchecked", "reason": "no_length"}
    assert fan["starting"] == {"kind": "starting", "status": "unchecked", "reason": "no_length"}
    assert fan["saved_voltage_drop_percent"] is None
    assert _saved(designed, "MDB", "Fan")[0] is None


def test_an_unchecked_feeder_makes_every_total_below_it_incomplete() -> None:
    # The sub-board's feeder has no length: its drop is unknown, and the
    # software counts it as 0. Nothing below it may read as complete.
    _designed_project, record = _designed(
        [
            {
                "name": "MDB",
                "loads": [
                    {"description": "Lamp", "load": "lighting", "power_kw": "1", "length_m": "10"}
                ],
            },
            {
                "name": "SDB",
                "fed_from": "MDB",
                "loads": [
                    {"description": "Rack", "load": "data", "power_kw": "1", "length_m": "20"}
                ],
            },
        ]
    )
    sub = next(b for b in record["boards"] if b["board"] == "SDB")
    assert sub["upstream"]["complete"] is False
    (contribution,) = sub["upstream"]["contributions"]
    assert contribution["board"] == "MDB"
    assert contribution["percent"] is None
    assert contribution["status"] == "unknown_counted_as_0"
    rack = _circuit(record, "SDB", "Rack")
    assert rack["running"]["total_complete"] is False
    assert rack["running"]["upstream_percent_counted"] == "0"
    main = next(b for b in record["boards"] if b["board"] == "MDB")
    assert main["upstream"]["complete"] is True
    feeder = next(c for c in main["circuits"] if c["inputs"]["length_m"]["value"] is None)
    assert feeder["running"]["reason"] == "no_length"


def test_a_measured_feeder_is_carried_to_the_sub_board_as_recorded() -> None:
    _designed_project, record = _designed(
        [
            {
                "name": "MDB",
                "loads": [
                    {"description": "Lamp", "load": "lighting", "power_kw": "1", "length_m": "10"}
                ],
            },
            {
                "name": "SDB",
                "fed_from": "MDB",
                "feeder_length_m": "30",
                "loads": [
                    {"description": "Rack", "load": "data", "power_kw": "1", "length_m": "20"}
                ],
            },
        ]
    )
    main = next(b for b in record["boards"] if b["board"] == "MDB")
    feeder = next(c for c in main["circuits"] if c["inputs"]["length_m"]["value"] == "30")
    assert (
        feeder["inputs"]["power_kw"]["origin"]
        == "derived by the software from the design of board SDB"
    )
    sub = next(b for b in record["boards"] if b["board"] == "SDB")
    (contribution,) = sub["upstream"]["contributions"]
    assert contribution["percent"] == feeder["saved_voltage_drop_percent"]
    assert contribution["status"] == "recorded"
    assert sub["upstream"]["complete"] is True
    rack = _circuit(record, "SDB", "Rack")
    assert rack["running"]["upstream_percent_counted"] == feeder["saved_voltage_drop_percent"]
    assert rack["running"]["limit_source"] == "company profile default_max_voltage_drop_percent"
    assert (
        feeder["running"]["limit_source"]
        == "the strictest limit of the loads at or below board SDB"
    )


@pytest.mark.parametrize("case", sorted(DESIGNED))
def test_the_record_agrees_with_the_saved_design_and_changes_nothing(case: str) -> None:
    """Every committed fixture: recorded or not, the same design; the record's finals are its."""
    request = ProjectDesignRequest.model_validate(DESIGNED[case]["request"])
    as_entered = request.model_copy(
        update={"info": request.info.model_copy(update={"revisions": []})}
    )
    plain, _ = design_domain.build_design(as_entered)
    trace = vd_record.ProjectTrace()
    traced, _ = design_domain.build_design(as_entered, trace)
    assert snapshot.canonical_json(traced.model_dump(mode="json")) == snapshot.canonical_json(
        plain.model_dump(mode="json")
    )
    record = vd_record.build(trace, snapshot_sha256=_SHA, profile_sha256=_SHA, rules_version="r")
    assert [b["board"] for b in record["boards"]] == [b.name for b in traced.boards]
    for board in traced.boards:
        recorded = next(b for b in record["boards"] if b["board"] == board.name)
        assert len(recorded["circuits"]) == len(board.circuits)
        for circuit in board.circuits:
            entry = next(c for c in recorded["circuits"] if c["circuit_id"] == circuit.id)
            saved = circuit.voltage_drop_percent
            assert entry["saved_voltage_drop_percent"] == (None if saved is None else str(saved))
            if circuit.cable_id:
                assert (
                    Decimal(entry["final_section_mm2"])
                    == board.cable(circuit.cable_id).cross_section_mm2
                )
            running = entry["running"]
            if saved is None:
                assert running["status"] != "checked"
            else:
                assert running["final"]["rounded_percent"] == str(saved)
                assert running["final"]["section_mm2"] == entry["final_section_mm2"]


def test_the_record_validates_and_says_it_is_not_engineering_validation() -> None:
    _designed_project, record = _designed(
        [{"name": "DB", "loads": [{"description": "Rack", "load": "data", "power_kw": "1"}]}]
    )
    envelope = vd_record.CalculationRecord.model_validate(record)
    assert envelope.schema_ == "pp-vdrec/1"
    assert "not engineering validation" in record["statement"]
    assert record["not_modelled"]
    # Canonical: every number is text, so the record hashes.
    assert len(snapshot.sha256_of(record)) == 64

"""Tests for `app/design/requirements.py`.

Mirrors the module 1:1 — if you add a function there, add its test here.
"""

from __future__ import annotations

from typing import Any

from app.design import integrity, requirements
from app.domain import design as design_domain
from app.models.schemas.design import ProjectDesignRequest, RequirementsInput

_SUPPLY = {"voltage_v": 400, "phases": 3, "fault_level_ka": 10, "earth_loop_ohm": "0.2"}


def _request(*, missing: bool = False) -> ProjectDesignRequest:
    main: dict[str, Any] = {
        "name": "MDB",
        "supply": {"voltage_v": 400, "phases": 3} if missing else dict(_SUPPLY),
        "loads": [
            {"description": "Lights", "load": "lighting", "power_kw": "1", "length_m": 10},
            {
                "description": "Pump",
                "load": "motor",
                "power_kw": "5.5",
                "phases": 3,
                "starter": "dol",
                "controlled": True,
                "length_m": 10,
            },
        ],
    }
    sub: dict[str, Any] = {
        "name": "DB-1",
        "fed_from": "MDB",
        "supply": {"voltage_v": 400, "phases": 3},
        "loads": [
            {
                "description": "Sockets",
                "load": "socket",
                "power_kw": "2",
                "power_factor": "0.95",
                "length_m": 10,
            }
        ],
    }
    if not missing:
        sub["feeder_length_m"] = 20
    return ProjectDesignRequest.model_validate({"info": {"name": "R"}, "boards": [main, sub]})


def _record(request: ProjectDesignRequest, **kwargs: Any) -> dict[str, Any]:
    designed, _ = design_domain.build_design(request)
    findings = integrity.project_findings(designed)
    return requirements.build(request, designed, findings, **kwargs)


def test_build_records_boards_loads_feeders_and_trace() -> None:
    record = _record(_request(), given=RequirementsInput(intake="guided"))
    assert record["schema"] == "pp-req/1"
    assert record["derived"] is False
    assert [b["ref"] for b in record["boards"]] == ["b1", "b2"]
    assert record["boards"][1]["fed_from_ref"] == "b1"
    assert [load["ref"] for load in record["loads"]] == ["b1.l1", "b1.l2", "b2.l1"]
    assert record["feeders"] == [
        {"ref": "b2.feeder", "board_ref": "b2", "supply_board_ref": "b1", "source": "generated"}
    ]
    trace = {t["requirement_ref"]: t["design_ref"] for t in record["trace"]}
    assert trace["b1.l1"] == "MDB/c1"
    assert trace["b2.l1"] == "DB-1/c1"
    # The feeder is the circuit the designer appended after MDB's loads.
    assert trace["b2.feeder"] == "MDB/c3"
    codes = [item["code"] for item in record["open_items"]]
    assert codes.count("inter_board_wiring_not_modelled") == 1
    # The controlled pump is a control requirement, not designed.
    assert codes.count("control_not_designed") == 1
    assert record["control"][0]["load_ref"] == "b1.l2"


def test_control_needs_come_from_the_guided_input() -> None:
    given = RequirementsInput.model_validate(
        {"control": [{"board_index": 0, "load_index": 1, "needs": ["start_stop"]}]}
    )
    record = _record(_request(), given=given)
    assert record["control"][0]["needs"] == ["start_stop"]


def test_questions_are_derived_from_empty_inputs_and_assumptions() -> None:
    record = _record(_request(missing=True))
    asked = {(q["kind"], q["field"], q["finding_code"]) for q in record["questions"]}
    assert ("missing_value", "fault_level_ka", "no_fault_level") in asked
    assert ("missing_value", "ze_ohm", "earth_fault_no_ze") in asked
    assert ("missing_value", "feeder_length_m", None) in asked
    assert ("assumption", "power_factor", "default_power_factor") in asked
    motor = next(q for q in record["questions"] if q["field"] == "motor_current_a")
    assert motor["kind"] == "assumption"
    assert motor["answerable"] is False
    assert motor["subject"]["load_ref"] == "b1.l2"
    assert all(q["state"] == "open" and q["by_user_id"] is None for q in record["questions"])
    complete = _record(_request())
    assert not [q for q in complete["questions"] if q["kind"] == "missing_value"]


def test_question_ids_are_stable() -> None:
    first = _record(_request(missing=True))
    again = _record(_request(missing=True), prior=first)
    assert [q["id"] for q in first["questions"]] == [q["id"] for q in again["questions"]]
    assert requirements.question_id("a", "f", None, "MDB", "") == requirements.question_id(
        "a", "f", None, "MDB", ""
    )


def test_refs_carry_over_from_a_prior_record() -> None:
    first = _record(_request())
    request = _request()
    reordered = request.model_copy(update={"boards": list(reversed(request.boards))})
    again = _record(reordered, prior=first)
    assert {b["name"]: b["ref"] for b in again["boards"]} == {"MDB": "b1", "DB-1": "b2"}


def test_apply_acknowledgements_and_the_printed_lines() -> None:
    record = _record(_request(missing=True))
    pf = next(q for q in record["questions"] if q["finding_code"] == "default_power_factor")
    record["acknowledgements"] = [
        {"question_id": pf["id"], "by_user_id": "u1", "by_name": "Ann", "at": "2026-11-03T10:00:00"}
    ]
    requirements.apply_acknowledgements(record)
    assert next(q for q in record["questions"] if q["id"] == pf["id"])["state"] == "acknowledged"
    lines = requirements.acknowledged_lines(record)
    assert lines == [
        "[ACKNOWLEDGED] Assumption acknowledged by Ann on 2026-11-03 — not engineer-validated: "
        "default_power_factor — MDB \u203a Lights"
    ]
    # No name recorded: never empty, never invented.
    record["questions"][0]["by_name"] = None
    record["questions"][0]["state"] = "acknowledged"
    assert requirements.UNKNOWN_ACCOUNT in requirements.acknowledged_lines(record)[0]
    # A derived record never counts as acknowledged.
    assert requirements.acknowledged_lines({**record, "derived": True}) == []


def test_an_acknowledgement_carries_over_while_its_question_stands() -> None:
    record = _record(_request(missing=True))
    pf = next(q for q in record["questions"] if q["finding_code"] == "default_power_factor")
    record["acknowledgements"] = [
        {"question_id": pf["id"], "by_user_id": "u1", "by_name": "Ann", "at": "2026-11-03T10:00:00"}
    ]
    again = _record(_request(missing=True), prior=record)
    assert next(q for q in again["questions"] if q["id"] == pf["id"])["state"] == "acknowledged"
    derived = _record(_request(missing=True), prior=record, derived=True)
    assert all(q["state"] == "open" for q in derived["questions"])


def test_an_answered_question_stays_on_later_records() -> None:
    record = _record(_request(missing=True))
    fault = next(q for q in record["questions"] if q["field"] == "fault_level_ka")
    done = {**fault, "answer": "15", "by_user_id": "u1", "by_name": "Ann", "at": "now"}
    answered = _record(_request(), answered=[done])
    kept = next(q for q in answered["questions"] if q["id"] == fault["id"])
    assert kept["state"] == "answered"
    assert kept["answer"] == "15"
    later = _record(_request(), prior=answered)
    assert any(q["id"] == fault["id"] and q["state"] == "answered" for q in later["questions"])


def test_open_item_lines_say_what_is_not_designed() -> None:
    record = _record(_request())
    assert requirements.open_item_lines(record) == [
        "[OPEN] Control circuit not designed: MDB \u203a Pump",
        "[OPEN] Board-to-board wiring not modelled: MDB to DB-1",
    ]
    # A derived record prints them too: a review document never reads complete.
    derived = _record(_request(), derived=True)
    assert requirements.open_item_lines(derived) == requirements.open_item_lines(record)
    assert requirements.open_item_lines(None) == []

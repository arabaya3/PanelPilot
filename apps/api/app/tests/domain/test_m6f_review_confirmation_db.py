"""M6f independent review of d40b551: the per-load confirmation, its binding and races.

Black-box over the design routes on a disposable ``panelpilot_test_*``
database, both ``DESIGN_SNAPSHOT_SCHEMA`` modes. Passing tests pin what holds;
strict xfails name confirmed defects. Synthetic data only.
"""

from __future__ import annotations

import copy
import os
import threading
import time
import uuid
from collections.abc import Callable
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine

from app.design import safety_review
from app.design.snapshot import sha256_of
from app.tests.api.v1.routes.test_export_integrity import (  # noqa: F401  (fixtures)
    PRICING,
    _engine,
    _tenants,
    pytestmark,
    revision_row,
    update_row,
)
from app.tests.domain.test_m6b_ai_adversarial_db import (  # noqa: F401  (fixtures)
    StubProvider,
    _app_fixture,
    _as_fixture,
    _open,
    _provider_fixture,
    _who_fixture,
)
from app.tests.domain.test_m6d_review_journeys_db import (
    approve,
    assert_never_executable,
    board,
    code,
    lighting,
    motor,
    request,
)
from app.tests.domain.test_m6d_safety_resolution_db import _export, _pdf_text

REASON = "Supplied and wired by the fire-protection contractor."


@pytest.fixture(autouse=True, params=[None, "pp-design/2"], ids=["v1", "v2"])
def _schema(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    if request.param is None:
        monkeypatch.delenv("DESIGN_SNAPSHOT_SCHEMA", raising=False)
    else:
        monkeypatch.setenv("DESIGN_SNAPSHOT_SCHEMA", request.param)


def other(description: str, kw: str) -> dict[str, Any]:
    """A load of kind ``other`` (a pump typed as a generic load)."""
    return {
        "description": description,
        "load": "other",
        "power_kw": kw,
        "phases": 3,
        "power_factor": "0.85",
        "length_m": 20,
    }


def boards(mdb: list[dict[str, Any]], db2: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """MDB, and DB-2 fed from MDB when ``db2`` is given (the feeder is set by the design)."""
    out = [{**board(), "loads": mdb}]
    if db2 is not None:
        out.append({**board("DB-2"), "loads": db2, "fed_from": "MDB"})
    return request(*out)


def _new(as_: Any, req: dict[str, Any]) -> str:
    saved = as_().post("/design/projects", json={"name": "M6f", "request": req, "note": "r1"})
    assert saved.status_code == 201, saved.text[:400]
    return str(saved.json()["id"])


def _revise(as_: Any, pid: str, req: dict[str, Any]) -> None:
    revised = as_().post(f"/design/projects/{pid}/revisions", json={"request": req, "note": "r"})
    assert revised.status_code == 201, revised.text[:400]


def _unresolved(as_: Any, pid: str, n: int) -> list[dict[str, Any]]:
    return [c for c in _open(as_("reviewer"), pid, n)["safety_concerns"] if not c["resolved"]]


def _body(c: dict[str, Any], resolution: str = "excluded_from_scope") -> dict[str, Any]:
    key = {"mention": "mention_id", "item": "item_id", "request": "request_id"}[c["kind"]]
    body: dict[str, Any] = {key: c["target_id"], "resolution": resolution, "reason": REASON}
    if c["kind"] != "request" and c.get("carried"):
        body["proposal_id"] = c["proposal_id"]
    if resolution == "excluded_from_scope" and c.get("confirmation_candidates") is not None:
        body["not_this_equipment"] = [
            {"board": x["board"], "load_index": x["load_index"]}
            for x in c["confirmation_candidates"]
        ]
    return body


def _url(pid: str, n: int) -> str:
    return f"/design/projects/{pid}/revisions/{n}/requirements/safety-resolutions"


def _resolve(as_: Any, who: str, pid: str, n: int, c: dict[str, Any], **kw: Any) -> Any:
    return as_(who).post(_url(pid, n), json=_body(c, **kw))


def _later(as_: Any, who: str, url: str, body: dict[str, Any]) -> Callable[[], Any]:
    """A POST prepared now (client built on this thread), sent when called."""
    client = as_(who)
    return lambda: client.post(url, json=body)


def _refs(c: dict[str, Any]) -> set[tuple[str, int]]:
    return {(x["board"], x["load_index"]) for x in c["confirmation_candidates"]}


# --------------------------------------------------------------------------- #
# 1. The candidate rule
# --------------------------------------------------------------------------- #


def test_a_pump_moved_to_a_sub_board_fed_from_mdb_is_a_candidate(as_: Any) -> None:
    pid = _new(as_, boards([lighting(), motor("Fire pump", "15")]))
    _revise(as_, pid, boards([lighting()], [motor("P-7", "15")]))
    (c,) = _unresolved(as_, pid, 2)
    assert ("DB-2", 0) in _refs(c)
    assert code(approve(as_("owner"), pid, 2)) == "approval_blocked_by_safety"


def test_an_other_kind_load_of_at_least_half_the_power_is_a_candidate(as_: Any) -> None:
    pid = _new(as_, boards([lighting(), motor("Fire pump", "15")]))
    _revise(as_, pid, boards([lighting(), other("Spare drive", "7.5")]))
    (c,) = _unresolved(as_, pid, 2)
    assert ("MDB", 1) in _refs(c)
    assert ("MDB", 0) not in _refs(c)  # the 1 kW lighting


@pytest.mark.xfail(
    strict=True,
    reason="defect: safety_review.candidates_for (safety_review.py:153) asks about a non-motor "
    "load only at >= half the flagged power; a 15 kW pump split into two 'other' 5 kW loads on "
    "another board is never a candidate, so it is excluded with no question about it",
)
def test_a_pump_split_into_small_other_kind_loads_is_asked_about(as_: Any) -> None:
    pid = _new(as_, boards([lighting(), motor("Fire pump", "15")]))
    _revise(as_, pid, boards([lighting()], [other("P-7a", "5"), other("P-7b", "5")]))
    (c,) = _unresolved(as_, pid, 2)
    assert {("DB-2", 0), ("DB-2", 1)} <= _refs(c), c["confirmation_candidates"]


@pytest.mark.xfail(
    strict=True,
    reason="defect: safety_review.concerns (safety_review.py:334-335) keeps the EARLIEST "
    "revision's copy of a carried concern (setdefault), so its power and rows are revision 1's; "
    "the pump re-powered to 4 kW 'other' on revision 2 and moved on revision 3 is not a "
    "candidate (floor 15 kW from revision 1's 30 kW)",
)
def test_the_candidate_floor_follows_the_latest_power_of_the_flagged_load(as_: Any) -> None:
    pid = _new(as_, boards([lighting(), motor("Fire pump", "30")]))
    _revise(as_, pid, boards([lighting(), other("Fire pump", "4")]))
    _revise(as_, pid, boards([lighting()], [other("P-9", "4")]))
    (c,) = _unresolved(as_, pid, 3)
    assert ("DB-2", 0) in _refs(c), c["confirmation_candidates"]


@pytest.mark.xfail(
    strict=True,
    reason="defect: the request concern id (safety_review._request_id, safety_review.py:202-205) "
    "omits the board and position, so two 'Fire pump' loads on two boards are ONE concern with "
    "the first's rows and power; the second renamed in place ('FP-2', 5 kW other) is neither "
    "still_in_request nor a candidate, and the exclusion goes through without naming it",
)
def test_two_fire_pumps_on_two_boards_each_hold_their_own_row(as_: Any) -> None:
    pid = _new(as_, boards([lighting(), motor("Fire pump", "30")], [other("Fire pump", "5")]))
    _revise(as_, pid, boards([lighting()], [other("FP-2", "5")]))
    (c,) = _unresolved(as_, pid, 2)
    refused = c["exclusion_refusal"] == "still_in_request"
    assert refused or ("DB-2", 0) in _refs(c), c


def test_the_unknown_power_rule_asks_about_motor_like_and_1kw_loads() -> None:
    """Unit check of the rule for an AI mention with no power (request power is required > 0)."""
    req = boards(
        [lighting("Small", "0.5"), other("Pump P", "0.75"), lighting("Big", "1")],
        [{**motor("Fan F", "0.37"), "load": "fan", "starter": None}],
    )
    got = {(b, i) for b, i, *_ in safety_review.candidates_for("", req)}
    assert got == {("MDB", 2), ("DB-2", 0)}
    # A 0.75 kW 'other' pump is not asked about: the rule is a heuristic (recorded limit).
    assert ("MDB", 1) not in got


# --------------------------------------------------------------------------- #
# 2. Carried concerns of two revisions, false positives, confirmation drift
# --------------------------------------------------------------------------- #


def test_concerns_carried_from_revisions_1_and_2_each_need_their_own_confirmation(
    engine: Engine, as_: Any
) -> None:
    pid = _new(as_, boards([lighting(), motor("Fire pump", "15")]))
    _revise(as_, pid, boards([lighting(), motor("Sprinkler pump", "11")]))
    _revise(as_, pid, boards([lighting()], [motor("P-1", "15"), motor("P-2", "11")]))
    found = _unresolved(as_, pid, 3)
    assert len(found) == 2, found
    assert {c["raised_on"] for c in found} == {1, 2}
    for c in found:
        assert {("DB-2", 0), ("DB-2", 1)} <= _refs(c)
        bare = {k: v for k, v in _body(c).items() if k != "not_this_equipment"}
        assert code(as_("reviewer").post(_url(pid, 3), json=bare)) == (
            "exclusion_confirmation_incomplete"
        )
    # One resolved still holds.
    assert _resolve(as_, "reviewer", pid, 3, found[0]).status_code == 200
    assert code(approve(as_("owner"), pid, 3)) == "approval_blocked_by_safety"
    assert _resolve(as_, "reviewer", pid, 3, found[1]).status_code == 200
    # The (false) confirmation is recorded with who, when and the loads, and printed.
    entries = revision_row(engine, pid, 3)["requirements"]["safety_resolutions"]
    assert len(entries) == 2
    for e in entries:
        assert e["by_user_id"] and e["at"] and e["by_name"]
        assert {x["description"] for x in e["confirmed_loads"]} >= {"P-1", "P-2"}
    assert approve(as_("owner"), pid, 3).status_code == 200
    printed = _pdf_text(_export(as_("owner"), pid, "pdf", 3).content)
    assert printed.count("confirmed these loads are not that equipment:") == 2
    assert "P-1 (15 kW)" in printed and "P-2 (11 kW)" in printed


def test_a_false_positive_takes_no_load_list_and_an_exclusion_needs_one(as_: Any) -> None:
    pid = _new(as_, boards([lighting(), motor("Standby pump", "15")]))
    _revise(as_, pid, boards([lighting()], [motor("P-1", "15")]))
    (c,) = _unresolved(as_, pid, 2)
    assert c["level"] == "possibly"
    # Possibly-level: no per-load confirmation asked; listing loads anyway is 422.
    assert not c.get("confirmation_candidates")
    body = {**_body(c, "false_positive"), "not_this_equipment": [{"board": "DB-2", "load_index": 0}]}
    assert code(as_("reviewer").post(_url(pid, 2), json=body)) == "exclusion_confirmation_extra"
    assert _resolve(as_, "reviewer", pid, 2, c, resolution="false_positive").status_code == 200
    assert approve(as_("owner"), pid, 2).status_code == 200


@pytest.mark.parametrize(
    "change",
    ["rename_candidate", "repower_candidate", "add_motor", "drop_candidate", "lighting_up"],
)
def test_a_confirmation_stops_counting_when_the_candidate_set_changes(
    as_: Any, change: str
) -> None:
    pid = _new(as_, boards([lighting(), motor("Fire pump", "15")]))
    mdb, db2 = [lighting()], [motor("P-1", "15"), motor("P-2", "7.5")]
    _revise(as_, pid, boards(mdb, db2))
    (c,) = _unresolved(as_, pid, 2)
    assert _resolve(as_, "reviewer", pid, 2, c).status_code == 200
    assert _unresolved(as_, pid, 2) == []
    mdb, db2 = copy.deepcopy(mdb), copy.deepcopy(db2)
    if change == "rename_candidate":
        db2[0]["description"] = "P-1 renamed"
    elif change == "repower_candidate":
        db2[1]["power_kw"] = "11"
    elif change == "add_motor":
        db2.append(motor("New", "2.2"))
    elif change == "drop_candidate":
        del db2[1]
    else:
        mdb[0]["power_kw"] = "8"  # the lighting becomes a candidate
    _revise(as_, pid, boards(mdb, db2))
    (again,) = _unresolved(as_, pid, 3)
    assert again["target_id"] == c["target_id"]
    assert code(approve(as_("owner"), pid, 3)) == "approval_blocked_by_safety"
    assert _resolve(as_, "reviewer", pid, 3, again).status_code == 200
    assert approve(as_("owner"), pid, 3).status_code == 200


def test_an_unchanged_request_keeps_the_confirmation_counting(as_: Any) -> None:
    pid = _new(as_, boards([lighting(), motor("Fire pump", "15")]))
    req = boards([lighting()], [motor("P-1", "15")])
    _revise(as_, pid, req)
    (c,) = _unresolved(as_, pid, 2)
    assert _resolve(as_, "reviewer", pid, 2, c).status_code == 200
    _revise(as_, pid, req)
    assert _unresolved(as_, pid, 3) == []


# --------------------------------------------------------------------------- #
# 3. Binding
# --------------------------------------------------------------------------- #


def _approved_with_confirmation(engine: Engine, as_: Any) -> str:
    pid = _new(as_, boards([lighting(), motor("Fire pump", "15")]))
    _revise(as_, pid, boards([lighting()], [motor("P-1", "15"), lighting("Hall", "9")]))
    (c,) = _unresolved(as_, pid, 2)
    assert _refs(c) == {("DB-2", 0), ("DB-2", 1)}
    assert _resolve(as_, "reviewer", pid, 2, c).status_code == 200
    assert approve(as_("owner"), pid, 2).status_code == 200
    return pid


def test_the_approved_exports_list_exactly_the_confirmed_loads(engine: Engine, as_: Any) -> None:
    pid = _approved_with_confirmation(engine, as_)
    (entry,) = revision_row(engine, pid, 2)["requirements"]["safety_resolutions"]
    assert [(x["board"], x["load_index"], x["description"]) for x in entry["confirmed_loads"]] == [
        ("DB-2", 0, "P-1"),
        ("DB-2", 1, "Hall"),
    ]
    printed = _pdf_text(_export(as_("owner"), pid, "pdf", 2).content)
    listed = printed.split("confirmed these loads are not that equipment:", 1)[1]
    listed = listed.split("— not engineer-validated", 1)[0]
    assert "P-1 (15 kW)" in listed and "Hall (9 kW)" in listed
    assert "Office lighting" not in listed
    exported = _export(as_("owner"), pid, "json", 2)
    assert exported.status_code == 200, exported.text[:300]
    doc = exported.json()
    approval = doc["approval"] if "approval" in doc else doc.get("design", {}).get("approval")
    assert approval["engineer_validated"] is False
    joined = " ".join(map(str, approval["excluded_from_scope"]))
    assert "P-1" in joined and "Hall" in joined and "Office lighting" not in joined


@pytest.mark.parametrize("tamper", ["drop_a_load", "rename_a_load", "remove_confirmation"])
def test_a_rehashed_confirmation_makes_the_approval_not_current(
    engine: Engine, as_: Any, tamper: str
) -> None:
    pid = _approved_with_confirmation(engine, as_)
    record = copy.deepcopy(revision_row(engine, pid, 2)["requirements"])
    (entry,) = record["safety_resolutions"]
    if tamper == "drop_a_load":
        entry["confirmed_loads"] = entry["confirmed_loads"][:1]
    elif tamper == "rename_a_load":
        entry["confirmed_loads"][0]["description"] = "Not the pump"
    else:
        del entry["confirmed_loads"]
    update_row(engine, pid, 2, requirements=record, requirements_sha256=sha256_of(record))
    shown = _open(as_("owner"), pid, 2)
    assert shown["approval_current"] is False, shown.get("approval_changed")
    assert shown["export_status"] != "review_approved"
    # The export falls back to an unapproved draft: no approval or confirmation is claimed.
    pdf = _export(as_("owner"), pid, "pdf", 2)
    assert pdf.status_code == 200, pdf.text[:300]
    printed = _pdf_text(pdf.content)
    assert "not approved" in printed and "confirmed this scope" not in printed


# --------------------------------------------------------------------------- #
# 4. Holds and wording
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("mdb", "info", "codes"),
    [
        ([lighting(), motor("Fire pump", "15")], None, {"request_life_safety"}),
        ([lighting(), motor("Standby pump", "15")], None, {"request_possibly_life_safety"}),
        ([lighting()], "Fire pump room", {"request_life_safety"}),
        ([lighting()], "Fire Station Road", {"request_possibly_life_safety"}),
    ],
)
def test_every_request_concern_type_holds_approval(
    as_: Any, mdb: list[dict[str, Any]], info: str | None, codes: set[str]
) -> None:
    req = boards(mdb)
    if info:
        req["info"]["customer"] = info
    pid = _new(as_, req)
    held = approve(as_("owner"), pid)
    assert code(held) == "approval_blocked_by_safety"
    assert set(held.json()["params"]["codes"].split(",")) == codes


#: Phrases no approved export may state (positive claims of validation or readiness).
FORBIDDEN = (
    "engineer-validated.",
    "engineer validated",
    "validated by",
    "approved for construction",
    "issued for construction",
    "ready for execution",
    "ready for construction",
    "ready to build",
    "certified",
    "compliant with",
    "complies with",
    "safe to energi",
)


def _positive(textual: str) -> list[str]:
    low = " ".join(textual.lower().split())
    for neg in ("not engineer-validated", "not engineer validation", "no engineer validation"):
        low = low.replace(neg, "")
    return [p for p in FORBIDDEN if p in low]


#: Formats only a ``pp-design/2`` revision has.
V2_ONLY = ("terminal_schedule_pdf", "wire_schedule_pdf", "connection_drawing_pdf")


@pytest.mark.parametrize(
    "fmt", ["pdf", "calculations_pdf", "quotation_pdf", "json", *V2_ONLY, "dxf"]
)
def test_no_approved_export_implies_validation_or_execution_readiness(
    engine: Engine, as_: Any, fmt: str
) -> None:
    pid = _approved_with_confirmation(engine, as_)
    extra: dict[str, Any] = {"pricing": PRICING} if fmt == "quotation_pdf" else {}
    exported = _export(as_("owner"), pid, fmt, 2, **extra)
    if fmt in V2_ONLY and exported.status_code in (409, 422) and "pp-design/2" not in str(
        os.environ.get("DESIGN_SNAPSHOT_SCHEMA")
    ):
        pytest.skip(f"{fmt} is a pp-design/2 export")
    assert exported.status_code == 200, exported.text[:300]
    body = (
        _pdf_text(exported.content) if exported.content[:4] == b"%PDF" else exported.text
    )
    if fmt != "dxf":
        assert "[SCOPE]" in body or "does_not_cover" in body
    assert _positive(body) == [], fmt
    assert_never_executable(as_("owner"), pid, 2)


# --------------------------------------------------------------------------- #
# 5. Races, forced on Postgres
# --------------------------------------------------------------------------- #


def _hold_project(engine: Engine, pid: str) -> tuple[Any, Any]:
    conn = engine.connect()
    tx = conn.begin()
    conn.execute(
        text("SELECT id FROM design_projects WHERE id = :p FOR UPDATE"), {"p": uuid.UUID(pid)}
    )
    return conn, tx


def _waiting(engine: Engine, holder: Any, count: int) -> None:
    pid = holder.execute(text("SELECT pg_backend_pid()")).scalar()
    deadline = time.monotonic() + 20
    with engine.connect() as probe:
        while time.monotonic() < deadline:
            n = probe.execute(
                text(
                    "SELECT count(*) FROM pg_stat_activity WHERE wait_event_type = 'Lock' "
                    "AND datname = current_database() AND pid <> :h "
                    "AND query LIKE 'SELECT design_projects.%'"
                ),
                {"h": pid},
            ).scalar()
            # pg_stat_activity is a per-transaction snapshot: end it each time.
            probe.commit()
            if n >= count:
                return
            time.sleep(0.05)
        rows = probe.execute(
            text("SELECT pid, state, wait_event_type, left(query, 80) FROM pg_stat_activity "
                 "WHERE datname = current_database()")
        ).all()
    raise AssertionError(f"{rows} holder={pid} "
                         f"fewer than {count} backends waited on the held project lock")


def _start(results: dict[str, Any], name: str, call: Callable[[], Any]) -> threading.Thread:
    def run() -> None:
        results[name] = call()

    t = threading.Thread(target=run, daemon=True)
    t.start()
    return t


def _two_carried(as_: Any) -> tuple[str, list[dict[str, Any]]]:
    pid = _new(as_, boards([lighting(), motor("Fire pump", "15"), motor("Sprinkler pump", "11")]))
    _revise(as_, pid, boards([lighting()], [motor("P-1", "15")]))
    found = _unresolved(as_, pid, 2)
    assert len(found) == 2
    return pid, found


def test_two_members_resolving_two_concerns_at_once_both_survive(
    engine: Engine, as_: Any
) -> None:
    pid, (a, b) = _two_carried(as_)
    results: dict[str, Any] = {}
    conn, tx = _hold_project(engine, pid)
    try:
        ta = _start(results, "a", _later(as_, "reviewer", _url(pid, 2), _body(a)))
        _waiting(engine, conn, 1)
        tb = _start(results, "b", _later(as_, "owner", _url(pid, 2), _body(b)))
        _waiting(engine, conn, 2)
    finally:
        tx.rollback()
        conn.close()
    ta.join(30)
    tb.join(30)
    assert results["a"].status_code == 200, results["a"].text[:300]
    assert results["b"].status_code == 200, results["b"].text[:300]
    row = revision_row(engine, pid, 2)
    entries = row["requirements"]["safety_resolutions"]
    assert {e["target_id"] for e in entries} == {a["target_id"], b["target_id"]}
    assert row["requirements_sha256"] == sha256_of(row["requirements"])
    assert _unresolved(as_, pid, 2) == []


@pytest.mark.parametrize("first", ["resolution", "approval"])
def test_a_resolution_racing_the_approval_leaves_a_consistent_record(
    engine: Engine, as_: Any, first: str
) -> None:
    pid, (a, b) = _two_carried(as_)
    assert _resolve(as_, "reviewer", pid, 2, a).status_code == 200
    results: dict[str, Any] = {}
    calls = {
        "resolution": _later(as_, "reviewer", _url(pid, 2), _body(b)),
        "approval": _later(
            as_, "owner", f"/design/projects/{pid}/revisions/2/approval", {"scope_confirmed": True}
        ),
    }
    second = "approval" if first == "resolution" else "resolution"
    conn, tx = _hold_project(engine, pid)
    try:
        t1 = _start(results, first, calls[first])
        _waiting(engine, conn, 1)
        t2 = _start(results, second, calls[second])
        _waiting(engine, conn, 2)
    finally:
        tx.rollback()
        conn.close()
    t1.join(30)
    t2.join(30)
    assert results["resolution"].status_code == 200, results["resolution"].text[:300]
    row = revision_row(engine, pid, 2)
    assert row["requirements_sha256"] == sha256_of(row["requirements"])
    if first == "resolution":
        assert results["approval"].status_code == 200, results["approval"].text[:300]
        assert row["approved_requirements_sha256"] == row["requirements_sha256"]
        assert _open(as_("owner"), pid, 2)["approval_current"] is True
    else:
        assert code(results["approval"]) == "approval_blocked_by_safety"
        assert row["approved_requirements_sha256"] is None
        assert approve(as_("owner"), pid, 2).status_code == 200

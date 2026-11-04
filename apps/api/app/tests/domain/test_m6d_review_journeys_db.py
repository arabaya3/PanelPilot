"""M6d review: designer journeys from description to review PDF (``panelpilot_test_*`` only).

Written by the independent reviewer from the M6d contract, before the
implementation was read. Each scenario traces description -> proposal ->
decisions -> saved requirements record -> approval eligibility -> review PDF
text, with a deterministic stub in place of the model. Whatever the
scenario, nothing unsupported may silently become an approved or
execution-ready design: the execution export is refused, unsupported text
never reaches the request or the stored design, and approval is refused
while a safety concern is unresolved.

Written against 710da3b with the coming rules as strict xfail; at 5133b60
they pass and the markers are gone. The resolution route is in :func:`resolve`.
Synthetic data only.
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy.engine import Engine

from app.design import snapshot
from app.tests.api.v1.routes.test_export_integrity import (  # noqa: F401  (fixtures)
    BLOCKED_FORMATS,
    _engine,
    _tenants,
    error,
    pdf_pages,
    pytestmark,
    revision_row,
)
from app.tests.domain.test_m6b_ai_adversarial_db import (  # noqa: F401  (fixtures)
    StubProvider,
    _app_fixture,
    _as_fixture,
    _decide,
    _open,
    _payload,
    _propose,
    _provider_fixture,
    _save,
    _who_fixture,
    span,
)

#: What the scope statement must name as not covered (contract wording, loosely).
NOT_COVERED = (
    "control circuit",
    "board-to-board",
    "life-safety",
    "standard",
    "execution",
    "engineer",
)

# --------------------------------------------------------------------------- #
# Helpers shared with the adversarial file
# --------------------------------------------------------------------------- #


def board(name: str = "MDB", fault: bool = True, **extra: Any) -> dict[str, Any]:
    supply: dict[str, Any] = {"voltage_v": 400, "phases": 3, "earth_loop_ohm": "0.1"}
    if fault:
        supply["fault_level_ka"] = 10
    return {"name": name, "supply": supply, "loads": [], **extra}


def lighting(description: str = "Office lighting", kw: str = "1") -> dict[str, Any]:
    return {
        "description": description,
        "load": "lighting",
        "power_kw": kw,
        "power_factor": "0.95",
        "length_m": 15,
    }


def motor(description: str, kw: str) -> dict[str, Any]:
    return {
        "description": description,
        "load": "motor",
        "power_kw": kw,
        "phases": 3,
        "starter": "dol",
        "power_factor": "0.85",
        "length_m": 20,
    }


def request(*boards: dict[str, Any], number: str = "M6D-REV-1") -> dict[str, Any]:
    return {"info": {"name": "M6d review", "number": number}, "boards": list(boards)}


def it(text: str, phrase: str, item_id: str, item_kind: str, /, **value: Any) -> dict[str, Any]:
    return {"id": item_id, "kind": item_kind, "source": span(text, phrase), "value": value}


def propose(as_: Any, provider: StubProvider, text: str, items: list[dict[str, Any]], **kw: Any):
    provider.payload = _payload(items)
    created = _propose(as_(kw.pop("creator", "engineer")), text, **kw)
    assert created.status_code == 201, created.text[:600]
    body: dict[str, Any] = created.json()
    return body


def by_quote(proposal: dict[str, Any], quote: str) -> dict[str, Any]:
    found: dict[str, Any] = next(i for i in proposal["items"] if i["quote"] == quote)
    return found


def decide_all(
    as_: Any, proposal: dict[str, Any], decisions: dict[str, str], who: str = "engineer"
):
    for item_id, decision in decisions.items():
        done = _decide(as_(who), proposal["id"], item_id, {"decision": decision})
        assert done.status_code == 200, done.text[:400]


def save_ai(
    as_: Any,
    proposal: dict[str, Any],
    req: dict[str, Any],
    placements: list[dict[str, Any]],
    who: str = "engineer",
) -> str:
    saved = _save(
        as_(who),
        req,
        {"intake": "ai", "ai": {"proposal_id": proposal["id"], "placements": placements}},
    )
    assert saved.status_code == 201, saved.text[:600]
    return str(saved.json()["id"])


def record(engine: Engine, pid: str, n: int = 1) -> dict[str, Any]:
    row = revision_row(engine, pid, n)
    assert row["requirements_sha256"] == snapshot.sha256_of(row["requirements"])
    found: dict[str, Any] = row["requirements"]
    return found


def open_codes(rec: dict[str, Any]) -> list[str]:
    return sorted(o["code"] for o in rec["open_items"])


def mention_ids(rec: dict[str, Any], code: str | None = None) -> list[str]:
    return [
        o["subject"]["mention_id"]
        for o in rec["open_items"]
        if o["subject"].get("mention_id") and (code is None or o["code"] == code)
    ]


def unsupported_ids(rec: dict[str, Any]) -> list[str]:
    return [o["subject"]["item_id"] for o in rec["open_items"] if o["code"] == "ai_unsupported"]


def approve(c: Any, pid: str, n: int = 1, body: Any = None) -> Any:
    """The contract's approval: ``scope_confirmed`` must be the literal ``true``."""
    return c.post(
        f"/design/projects/{pid}/revisions/{n}/approval",
        json={"scope_confirmed": True} if body is None else body,
    )


def resolve(
    c: Any,
    pid: str,
    n: int,
    subject: dict[str, str],
    resolution: str,
    reason: str = "Fire pump supplied and wired by the fire contractor.",
    **extra: Any,
) -> Any:
    """Assumed shape of the coming resolution route (adapt once the real one is known)."""
    return c.post(
        f"/design/projects/{pid}/revisions/{n}/requirements/safety-resolutions",
        json={**subject, "resolution": resolution, "reason": reason, **extra},
    )


def export(c: Any, pid: str, n: int = 1, fmt: str = "pdf", **extra: Any) -> Any:
    return c.post(f"/design/projects/{pid}/revisions/{n}/export", json={"format": fmt, **extra})


def pdf_text(c: Any, pid: str, n: int = 1) -> str:
    done = export(c, pid, n)
    assert done.status_code == 200, done.text[:400]
    return "\n".join(pdf_pages(done.content))


def code(response: Any) -> str:
    return str(error(response).get("code"))


def assert_never_executable(c: Any, pid: str, n: int = 1) -> None:
    """The execution export and every restricted format stay refused."""
    for fmt in ("pdf", "dxf", "json"):
        refused = export(c, pid, n, fmt, purpose="execution")
        assert refused.status_code == 409, (fmt, refused.text[:300])
    for fmt in BLOCKED_FORMATS:
        refused = export(c, pid, n, fmt)
        assert refused.status_code in (409, 422), (fmt, refused.text[:300])


def assert_absent(engine: Engine, pid: str, n: int, *phrases: str) -> None:
    """None of ``phrases`` reached the request or the server's stored design."""
    row = revision_row(engine, pid, n)
    stored = json.dumps([row["request"], row["design_snapshot"]]).lower()
    for phrase in phrases:
        assert phrase.lower() not in stored, phrase


def assert_scope_statement(text: str) -> None:
    lowered = text.lower()
    assert "not cover" in lowered or "does not include" in lowered, "no scope statement"
    for words in NOT_COVERED:
        assert words in lowered, words


# --------------------------------------------------------------------------- #
# (a) A normal distribution-board request
# --------------------------------------------------------------------------- #

NORMAL = (
    "Main board MDB, 400 V three phase, fault level 10 kA. "
    "Office lighting 1 kW on MDB. Sockets 2 kW on MDB."
)


def _normal(as_: Any, provider: StubProvider) -> str:
    t = NORMAL
    proposal = propose(
        as_,
        provider,
        t,
        [
            it(t, "Main board MDB", "b1", "board", name="MDB"),
            it(
                t,
                "Office lighting 1 kW on MDB",
                "l1",
                "load",
                board="MDB",
                description="Office lighting",
                kind="lighting",
                power_kw=1,
            ),
            it(
                t,
                "Sockets 2 kW on MDB",
                "l2",
                "load",
                board="MDB",
                description="Sockets",
                kind="lighting",
                power_kw=2,
            ),
        ],
    )
    assert proposal["safety_mentions"] == []
    ids = [i["id"] for i in proposal["items"]]
    decide_all(as_, proposal, dict.fromkeys(ids, "accept"))
    mdb = board()
    mdb["loads"] = [lighting(), lighting("Sockets", "2")]
    return save_ai(
        as_,
        proposal,
        request(mdb),
        [
            {"item_id": ids[0], "board_index": 0},
            {"item_id": ids[1], "board_index": 0, "load_index": 0},
            {"item_id": ids[2], "board_index": 0, "load_index": 1},
        ],
    )


def test_a_normal_request_has_no_safety_concern_and_is_never_executable(
    engine: Engine, as_: Any, provider: StubProvider
) -> None:
    pid = _normal(as_, provider)
    rec = record(engine, pid)
    assert not {c for c in open_codes(rec) if c.startswith("ai_")}
    assert_never_executable(as_("reviewer"), pid)
    text = pdf_text(as_("reviewer"), pid)
    assert "NOT FOR EXECUTION" in text


def test_a_normal_request_approves_only_with_scope_confirmed_and_states_its_scope(
    engine: Engine, as_: Any, provider: StubProvider
) -> None:
    pid = _normal(as_, provider)
    for body in ({}, {"scope_confirmed": False}, {"scope_confirmed": "true"}):
        refused = approve(as_("reviewer"), pid, body=body)
        assert refused.status_code == 422, (body, refused.text[:300])
    assert revision_row(engine, pid, 1)["approved_at"] is None
    assert approve(as_("reviewer"), pid).status_code == 200
    text = pdf_text(as_("reviewer"), pid)
    assert "REVIEW APPROVED" in text
    assert_scope_statement(text)
    assert_never_executable(as_("reviewer"), pid)


# --------------------------------------------------------------------------- #
# (b) An explicit fire-pump request
# --------------------------------------------------------------------------- #

FIRE = (
    "Main board MDB, fault level 10 kA. Fire pump FP-1 15 kW on MDB. "
    "Office lighting 1 kW on MDB."
)


def _fire(as_: Any, provider: StubProvider, pump_in_request: bool = False) -> tuple[str, dict]:
    t = FIRE
    proposal = propose(
        as_,
        provider,
        t,
        [
            it(t, "Main board MDB", "b1", "board", name="MDB"),
            it(
                t,
                "Fire pump FP-1 15 kW on MDB",
                "l1",
                "load",
                board="MDB",
                description="FP-1",
                kind="motor",
                power_kw=15,
            ),
            it(
                t,
                "Office lighting 1 kW on MDB",
                "l2",
                "load",
                board="MDB",
                description="Office lighting",
                kind="lighting",
                power_kw=1,
            ),
        ],
    )
    pump = by_quote(proposal, "Fire pump FP-1 15 kW on MDB")
    assert pump["kind"] == "unsupported"
    assert [m["level"] for m in proposal["safety_mentions"]] == ["life_safety"]
    ids = [i["id"] for i in proposal["items"]]
    decide_all(as_, proposal, dict.fromkeys(ids, "accept"))
    mdb = board()
    mdb["loads"] = [lighting()] + ([motor("FP-1", "15")] if pump_in_request else [])
    light = by_quote(proposal, "Office lighting 1 kW on MDB")["id"]
    pid = save_ai(
        as_,
        proposal,
        request(mdb),
        [
            {"item_id": ids[0], "board_index": 0},
            {"item_id": light, "board_index": 0, "load_index": 0},
        ],
    )
    return pid, proposal


def test_a_fire_pump_is_never_placed_and_never_executable(
    engine: Engine, as_: Any, provider: StubProvider
) -> None:
    pid, proposal = _fire(as_, provider)
    pump = by_quote(proposal, "Fire pump FP-1 15 kW on MDB")
    # Placing the unsupported pump is refused outright.
    placed = _save(
        as_(),
        request(board()),
        {
            "intake": "ai",
            "ai": {
                "proposal_id": proposal["id"],
                "placements": [{"item_id": pump["id"], "board_index": 0, "load_index": 0}],
            },
        },
    )
    assert placed.status_code in (409, 422), placed.text[:300]
    rec = record(engine, pid)
    assert "ai_life_safety_mentioned" in open_codes(rec)
    assert pump["id"] in unsupported_ids(rec)
    assert_absent(engine, pid, 1, "FP-1", "fire pump")
    text = pdf_text(as_("reviewer"), pid)
    assert "[OPEN] Not covered by this design (from AI intake): Fire pump FP-1 15 kW" in text
    assert "('fire pump')" in text
    assert_never_executable(as_("reviewer"), pid)


def test_a_fire_pump_blocks_approval_until_excluded_and_prints_the_exclusion(
    engine: Engine, as_: Any, provider: StubProvider
) -> None:
    pid, proposal = _fire(as_, provider)
    refused = approve(as_("reviewer"), pid)
    assert refused.status_code == 409, refused.text[:300]
    assert code(refused) == "approval_blocked_by_safety"
    rec = record(engine, pid)
    (mention,) = mention_ids(rec, "ai_life_safety_mentioned")
    pump = by_quote(proposal, "Fire pump FP-1 15 kW on MDB")["id"]
    # A false positive is refused: an item from the same span is life-safety.
    fp = resolve(as_("reviewer"), pid, 1, {"mention_id": mention}, "false_positive")
    assert fp.status_code == 409, fp.text[:300]
    for subject in ({"item_id": pump}, {"mention_id": mention}):
        done = resolve(as_("reviewer"), pid, 1, subject, "excluded_from_scope")
        assert done.status_code == 200, done.text[:300]
    assert approve(as_("owner"), pid).status_code == 200
    text = pdf_text(as_("reviewer"), pid)
    assert "[EXCLUDED FROM SCOPE]" in text
    assert "[FALSE POSITIVE]" not in text
    assert_scope_statement(text)
    assert_absent(engine, pid, 1, "FP-1")
    assert_never_executable(as_("reviewer"), pid)


def test_a_fire_pump_typed_into_the_request_by_hand_still_blocks_approval(
    engine: Engine, as_: Any, provider: StubProvider
) -> None:
    """The AI flagged it; the designer adds "FP-1" manually. Approval is refused."""
    pid, _ = _fire(as_, provider, pump_in_request=True)
    refused = approve(as_("reviewer"), pid)
    assert refused.status_code == 409, refused.text[:300]
    assert code(refused) == "approval_blocked_by_safety"


# --------------------------------------------------------------------------- #
# (c) Ambiguous wording ("standby pump") and a harmless false positive
# --------------------------------------------------------------------------- #

STANDBY = "Main board MDB, fault level 10 kA. Standby pump 5.5 kW on MDB. Lighting 1 kW on MDB."
ROAD = "Site at 12 Fire Station Road. Main board MDB, fault level 10 kA. Lighting 1 kW on MDB."


def _standby(as_: Any, provider: StubProvider) -> tuple[str, dict]:
    t = STANDBY
    proposal = propose(
        as_,
        provider,
        t,
        [
            it(t, "Main board MDB", "b1", "board", name="MDB"),
            it(
                t,
                "Standby pump 5.5 kW on MDB",
                "l1",
                "load",
                board="MDB",
                description="Standby pump",
                kind="motor",
                power_kw=5.5,
            ),
            it(
                t,
                "Lighting 1 kW on MDB",
                "l2",
                "load",
                board="MDB",
                description="Lighting",
                kind="lighting",
                power_kw=1,
            ),
        ],
    )
    pump = by_quote(proposal, "Standby pump 5.5 kW on MDB")
    assert pump["kind"] == "unsupported", pump
    ids = [i["id"] for i in proposal["items"]]
    decide_all(as_, proposal, dict.fromkeys(ids, "accept"))
    mdb = board()
    mdb["loads"] = [lighting("Lighting")]
    light = by_quote(proposal, "Lighting 1 kW on MDB")["id"]
    return (
        save_ai(
            as_,
            proposal,
            request(mdb),
            [
                {"item_id": ids[0], "board_index": 0},
                {"item_id": light, "board_index": 0, "load_index": 0},
            ],
        ),
        proposal,
    )


def _road(as_: Any, provider: StubProvider) -> tuple[str, dict]:
    t = ROAD
    proposal = propose(
        as_,
        provider,
        t,
        [
            it(t, "Main board MDB", "b1", "board", name="MDB"),
            it(
                t,
                "Lighting 1 kW on MDB",
                "l1",
                "load",
                board="MDB",
                description="Lighting",
                kind="lighting",
                power_kw=1,
            ),
        ],
    )
    ids = [i["id"] for i in proposal["items"]]
    decide_all(as_, proposal, dict.fromkeys(ids, "accept"))
    mdb = board()
    mdb["loads"] = [lighting("Lighting")]
    return (
        save_ai(
            as_,
            proposal,
            request(mdb),
            [
                {"item_id": ids[0], "board_index": 0},
                {"item_id": ids[1], "board_index": 0, "load_index": 0},
            ],
        ),
        proposal,
    )


def test_a_standby_pump_is_not_designed_and_is_open_in_the_pdf(
    engine: Engine, as_: Any, provider: StubProvider
) -> None:
    pid, _ = _standby(as_, provider)
    rec = record(engine, pid)
    assert "ai_possibly_life_safety" in open_codes(rec)
    assert "ai_unsupported" in open_codes(rec)
    assert_absent(engine, pid, 1, "standby")
    text = pdf_text(as_("reviewer"), pid)
    assert "[OPEN] Not covered by this design (from AI intake): Standby pump" in text
    assert_never_executable(as_("reviewer"), pid)


def test_a_standby_pump_blocks_approval_until_resolved(
    engine: Engine, as_: Any, provider: StubProvider
) -> None:
    pid, proposal = _standby(as_, provider)
    refused = approve(as_("reviewer"), pid)
    assert code(refused) == "approval_blocked_by_safety", refused.text[:300]
    rec = record(engine, pid)
    pump = by_quote(proposal, "Standby pump 5.5 kW on MDB")["id"]
    for subject in [{"item_id": pump}] + [{"mention_id": m} for m in mention_ids(rec)]:
        done = resolve(
            as_("reviewer"), pid, 1, subject, "excluded_from_scope", "Duty/standby by others."
        )
        assert done.status_code == 200, done.text[:300]
    assert approve(as_("owner"), pid).status_code == 200
    text = pdf_text(as_("owner"), pid)
    assert "[EXCLUDED FROM SCOPE]" in text
    assert_absent(engine, pid, 1, "standby")
    assert_never_executable(as_("owner"), pid)


def test_fire_station_road_is_raised_not_designed(
    engine: Engine, as_: Any, provider: StubProvider
) -> None:
    """A street name: whatever the net makes of it, the design holds only the lighting."""
    pid, proposal = _road(as_, provider)
    assert all(i["kind"] != "unsupported" for i in proposal["items"])
    assert_never_executable(as_("reviewer"), pid)


def test_fire_station_road_is_a_false_positive_printed_as_not_engineer_validated(
    engine: Engine, as_: Any, provider: StubProvider
) -> None:
    pid, _ = _road(as_, provider)
    mentions = mention_ids(record(engine, pid))
    assert mentions, "the street name raised no mention: nothing to resolve"
    refused = approve(as_("reviewer"), pid)
    assert code(refused) == "approval_blocked_by_safety", refused.text[:300]
    for mid in mentions:
        done = resolve(
            as_("reviewer"), pid, 1, {"mention_id": mid}, "false_positive", "A street address."
        )
        assert done.status_code == 200, done.text[:300]
    assert approve(as_("owner"), pid).status_code == 200
    text = pdf_text(as_("owner"), pid)
    line = next(x for x in text.splitlines() if x.startswith("[FALSE POSITIVE]"))
    assert "not engineer-validated" in line
    assert_scope_statement(text)
    assert_never_executable(as_("owner"), pid)


# --------------------------------------------------------------------------- #
# (d) Conflicting or missing information
# --------------------------------------------------------------------------- #

CONFLICT = (
    "Main board MDB. Chiller CH-1 11 kW on MDB. "
    "Per the mechanical schedule CH-1 is 15 kW. Lighting 1 kW on MDB."
)


def _conflict(as_: Any, provider: StubProvider) -> tuple[str, dict]:
    t = CONFLICT
    proposal = propose(
        as_,
        provider,
        t,
        [
            it(t, "Main board MDB", "b1", "board", name="MDB"),
            it(
                t,
                "Chiller CH-1 11 kW on MDB",
                "l1",
                "load",
                board="MDB",
                description="CH-1",
                kind="motor",
                power_kw=11,
            ),
            it(
                t,
                "CH-1 is 15 kW",
                "q1",
                "question",
                text="Is CH-1 11 kW or 15 kW?",
                about="l1",
                reason="contradictory",
            ),
            it(
                t,
                "Lighting 1 kW on MDB",
                "l2",
                "load",
                board="MDB",
                description="Lighting",
                kind="lighting",
                power_kw=1,
            ),
        ],
    )
    ids = {i["quote"]: i["id"] for i in proposal["items"]}
    decide_all(
        as_,
        proposal,
        {ids["Main board MDB"]: "accept", ids["Lighting 1 kW on MDB"]: "accept"},
    )
    mdb = board(fault=False)
    mdb["loads"] = [lighting("Lighting")]
    pid = save_ai(
        as_,
        proposal,
        request(mdb),
        [
            {"item_id": ids["Main board MDB"], "board_index": 0},
            {"item_id": ids["Lighting 1 kW on MDB"], "board_index": 0, "load_index": 0},
        ],
    )
    return pid, proposal


def test_a_conflict_and_a_missing_fault_level_stay_open_and_print(
    engine: Engine, as_: Any, provider: StubProvider
) -> None:
    pid, _ = _conflict(as_, provider)
    rec = record(engine, pid)
    assert "ai_question" in open_codes(rec)
    fault = [q for q in rec.get("questions", []) if "fault" in json.dumps(q)]
    assert fault, rec.get("questions")
    assert all(q["state"] != "answered" for q in fault)
    # The undecided chiller is not in the design at either power.
    assert_absent(engine, pid, 1, "CH-1")
    text = pdf_text(as_("reviewer"), pid)
    assert "CH-1 11 kW or 15 kW" in text
    assert "fault" in text.lower()
    assert_never_executable(as_("reviewer"), pid)


def test_two_powers_for_one_load_are_refused_or_open(
    engine: Engine, as_: Any, provider: StubProvider
) -> None:
    t = CONFLICT
    proposal = propose(
        as_,
        provider,
        t,
        [
            it(t, "Main board MDB", "b1", "board", name="MDB"),
            it(
                t,
                "Chiller CH-1 11 kW on MDB",
                "l1",
                "load",
                board="MDB",
                description="CH-1",
                kind="motor",
                power_kw=11,
            ),
            it(
                t,
                "CH-1 is 15 kW",
                "l2",
                "load",
                board="MDB",
                description="CH-1",
                kind="motor",
                power_kw=15,
            ),
        ],
    )
    ids = [i["id"] for i in proposal["items"]]
    decide_all(as_, proposal, dict.fromkeys(ids, "accept"))
    mdb = board()
    mdb["loads"] = [motor("CH-1", "11")]
    both = _save(
        as_(),
        request(mdb),
        {
            "intake": "ai",
            "ai": {
                "proposal_id": proposal["id"],
                "placements": [
                    {"item_id": ids[0], "board_index": 0},
                    {"item_id": ids[1], "board_index": 0, "load_index": 0},
                    {"item_id": ids[2], "board_index": 0, "load_index": 0},
                ],
            },
        },
    )
    if both.status_code == 422:
        return
    assert both.status_code == 201, both.text[:300]
    pid = str(both.json()["id"])
    conflicts = [
        o for o in record(engine, pid)["open_items"] if o["code"] == "ai_conflicting_values"
    ]
    assert {o["subject"]["kind"] for o in conflicts} >= {"items_disagree"}
    lines = [x for x in pdf_text(as_("reviewer"), pid).splitlines() if x.startswith("[OPEN]")]
    assert any("11" in x and "15" in x for x in lines), lines


def test_a_conflict_is_listed_as_not_covered_when_approved(
    engine: Engine, as_: Any, provider: StubProvider
) -> None:
    pid, _ = _conflict(as_, provider)
    assert approve(as_("reviewer"), pid).status_code == 200
    text = pdf_text(as_("reviewer"), pid)
    assert_scope_statement(text)
    assert "CH-1 11 kW or 15 kW" in text
    assert_never_executable(as_("reviewer"), pid)


# --------------------------------------------------------------------------- #
# (e) Requirements outside the supported model
# --------------------------------------------------------------------------- #

OUTSIDE = (
    "Main board MDB, fault level 10 kA. Lighting 1 kW on MDB. "
    "Use cable 4x16 mm2 XLPE/SWA for the incomer. "
    "Breaker part ABB S203-C16 for lighting. "
    "Interconnect MDB to DB-9 with board-to-board wiring."
)


def _outside(as_: Any, provider: StubProvider) -> tuple[str, dict]:
    t = OUTSIDE
    proposal = propose(
        as_,
        provider,
        t,
        [
            it(t, "Main board MDB", "b1", "board", name="MDB"),
            it(
                t,
                "Lighting 1 kW on MDB",
                "l1",
                "load",
                board="MDB",
                description="Lighting",
                kind="lighting",
                power_kw=1,
            ),
            it(
                t,
                "Use cable 4x16 mm2 XLPE/SWA for the incomer",
                "u1",
                "unsupported",
                text="cable 4x16 mm2 XLPE/SWA",
                reason="a cable specification",
            ),
            it(
                t,
                "Breaker part ABB S203-C16 for lighting",
                "u2",
                "unsupported",
                text="ABB S203-C16",
                reason="a product number",
            ),
            it(
                t,
                "Interconnect MDB to DB-9 with board-to-board wiring",
                "u3",
                "unsupported",
                text="board-to-board wiring MDB to DB-9",
                reason="board-to-board wiring",
            ),
        ],
    )
    assert [i["kind"] for i in proposal["items"]].count("unsupported") == 3
    ids = [i["id"] for i in proposal["items"]]
    decide_all(as_, proposal, dict.fromkeys(ids, "accept"))
    mdb = board()
    mdb["loads"] = [lighting("Lighting")]
    pid = save_ai(
        as_,
        proposal,
        request(mdb),
        [
            {"item_id": ids[0], "board_index": 0},
            {"item_id": ids[1], "board_index": 0, "load_index": 0},
        ],
    )
    return pid, proposal


def test_outside_the_model_is_open_never_designed(
    engine: Engine, as_: Any, provider: StubProvider
) -> None:
    pid, _ = _outside(as_, provider)
    rec = record(engine, pid)
    assert len(unsupported_ids(rec)) == 3
    assert_absent(engine, pid, 1, "XLPE", "S203", "DB-9")
    text = pdf_text(as_("reviewer"), pid)
    for words in ("4x16 mm2", "ABB S203-C16", "board-to-board"):
        assert any(words in x for x in text.splitlines() if x.startswith("[OPEN]")), words
    assert_never_executable(as_("reviewer"), pid)


def test_outside_the_model_approves_only_as_not_covered(
    engine: Engine, as_: Any, provider: StubProvider
) -> None:
    pid, _ = _outside(as_, provider)
    assert approve(as_("reviewer"), pid, body={}).status_code == 422
    assert approve(as_("reviewer"), pid).status_code == 200
    text = pdf_text(as_("reviewer"), pid)
    assert_scope_statement(text)
    assert "[OPEN] Not covered by this design (from AI intake): ABB S203-C16" in text
    assert_absent(engine, pid, 1, "XLPE", "S203", "DB-9")
    assert_never_executable(as_("reviewer"), pid)

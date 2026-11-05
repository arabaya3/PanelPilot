"""M6f re-check of c0d93ea: every load confirmed, positional request ids, AI concerns, ar/he.

Independent reviewer, black-box over the design routes on a disposable
``panelpilot_test_*`` database, both ``DESIGN_SNAPSHOT_SCHEMA`` modes.
Synthetic data only.
"""

from __future__ import annotations

import copy
import hashlib
import unicodedata
from typing import Any

import pytest
from sqlalchemy.engine import Engine

from app.design.snapshot import sha256_of
from app.tests.api.v1.routes.test_export_integrity import (  # noqa: F401  (fixtures)
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
    _payload,
    _propose,
    _provider_fixture,
    _who_fixture,
)
from app.tests.domain.test_m6d_review_journeys_db import (
    _fire,
    approve,
    board,
    code,
    lighting,
    motor,
    request,
)
from app.tests.domain.test_m6d_safety_resolution_db import _export, _pdf_text
from app.tests.domain.test_m6f_review_confirmation_db import (
    _body,
    _new,
    _refs,
    _resolve,
    _revise,
    _unresolved,
    _url,
    boards,
    other,
)


@pytest.fixture(autouse=True, params=[None, "pp-design/2"], ids=["v1", "v2"])
def _schema(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    if request.param is None:
        monkeypatch.delenv("DESIGN_SNAPSHOT_SCHEMA", raising=False)
    else:
        monkeypatch.setenv("DESIGN_SNAPSHOT_SCHEMA", request.param)


def _all_loads(req: dict[str, Any]) -> set[tuple[str, int]]:
    return {(b["name"], i) for b in req["boards"] for i in range(len(b["loads"]))}


@pytest.mark.parametrize(
    "variant",
    ["split_other_kinds", "rename_move_repower", "spread_three_boards"],
)
def test_every_load_of_every_board_must_be_confirmed(as_: Any, variant: str) -> None:
    pid = _new(as_, boards([lighting(), motor("Fire pump", "15")]))
    req = {
        "split_other_kinds": boards([lighting()], [other("A", "0.5"), other("B", "0.5")]),
        "rename_move_repower": boards([lighting()], [lighting("Heater", "0.2")]),
        "spread_three_boards": request(
            {**board(), "loads": [lighting()]},
            {**board("DB-2"), "loads": [other("A", "3"), other("B", "3")], "fed_from": "MDB"},
            {**board("DB-3"), "loads": [other("C", "3")], "fed_from": "DB-2"},
        ),
    }[variant]
    _revise(as_, pid, req)
    (c,) = _unresolved(as_, pid, 2)
    assert _refs(c) == _all_loads(req)
    body = _body(c)
    body["not_this_equipment"] = body["not_this_equipment"][1:]
    assert code(as_("reviewer").post(_url(pid, 2), json=body)) == (
        "exclusion_confirmation_incomplete"
    )
    assert code(approve(as_("owner"), pid, 2)) == "approval_blocked_by_safety"


def test_duplicate_refs_in_not_this_equipment_store_each_load_once(
    engine: Engine, as_: Any
) -> None:
    pid = _new(as_, boards([lighting(), motor("Fire pump", "15")]))
    _revise(as_, pid, boards([lighting()], [motor("P-1", "15")]))
    (c,) = _unresolved(as_, pid, 2)
    body = _body(c)
    body["not_this_equipment"] = body["not_this_equipment"] * 3
    done = as_("reviewer").post(_url(pid, 2), json=body)
    assert done.status_code in (200, 422), done.text[:300]
    if done.status_code == 200:
        (entry,) = revision_row(engine, pid, 2)["requirements"]["safety_resolutions"]
        keys = [(x["board"], x["load_index"]) for x in entry["confirmed_loads"]]
        assert sorted(keys) == sorted(set(keys)) == sorted(_refs(c))


def test_duplicate_board_names_cannot_merge_two_loads_into_one_confirmation(
    engine: Engine, as_: Any
) -> None:
    pid = _new(as_, boards([lighting(), motor("Fire pump", "15")]))
    dup = request({**board(), "loads": [lighting()]}, {**board(), "loads": [motor("P-1", "15")]})
    revised = as_().post(f"/design/projects/{pid}/revisions", json={"request": dup, "note": "r"})
    if revised.status_code != 201:
        assert revised.status_code in (409, 422), revised.text[:300]
        return  # duplicate board names are refused at save: nothing to merge
    (c,) = _unresolved(as_, pid, 2)
    _resolve(as_, "reviewer", pid, 2, c)
    # Either the exclusion never counts (held), or both loads were recorded.
    # Observed at c0d93ea: the exclusion is refused (still_in_request, both rows are
    # "MDB" index 0) and the approval is refused (revision_not_validated).
    if approve(as_("owner"), pid, 2).status_code == 200:
        (entry,) = revision_row(engine, pid, 2)["requirements"]["safety_resolutions"]
        assert {x["description"] for x in entry["confirmed_loads"]} >= {"Office lighting", "P-1"}


def test_carried_ai_mention_and_item_need_the_every_load_confirmation(
    engine: Engine, as_: Any, provider: StubProvider
) -> None:
    pid, _ = _fire(as_, provider)
    req = boards([lighting()], [motor("P-7", "15")])
    provider.payload = _payload([])
    fresh = _propose(as_(), "Main board MDB. Office lighting 1 kW on MDB.", request=req)
    assert fresh.status_code == 201, fresh.text[:300]
    revised = as_().post(
        f"/design/projects/{pid}/revisions",
        json={
            "request": req,
            "note": "r2",
            "requirements_input": {
                "intake": "ai",
                "control": [],
                "ai": {"proposal_id": fresh.json()["id"], "placements": []},
            },
        },
    )
    assert revised.status_code == 201, revised.text[:300]
    found = _unresolved(as_, pid, 2)
    ai = [c for c in found if c["kind"] in ("mention", "item")]
    assert {c["kind"] for c in ai} == {"mention", "item"}, found
    for c in ai:
        assert c["carried"]
        assert _refs(c) == _all_loads(req)
        bare = {k: v for k, v in _body(c).items() if k != "not_this_equipment"}
        assert code(as_("reviewer").post(_url(pid, 2), json=bare)) == (
            "exclusion_confirmation_incomplete"
        )
    for c in found:
        assert _resolve(as_, "reviewer", pid, 2, c).status_code == 200, c
    assert approve(as_("owner"), pid, 2).status_code == 200
    printed = _pdf_text(_export(as_("owner"), pid, "pdf", 2).content)
    assert printed.count("confirmed these loads are not that equipment:") >= 2
    assert "P-7 (15 kW)" in printed


def _old_id(level: str, place: str, text: str, term: str, start: int) -> str:
    """The pre-c0d93ea request concern id (no board or position)."""
    norm = " ".join(text.casefold().split())
    digest = hashlib.sha256(f"{level}\x00{place}\x00{norm}\x00{term}\x00{start}".encode())
    return "r" + digest.hexdigest()[:16]


def test_a_resolution_under_the_old_id_stops_counting(engine: Engine, as_: Any) -> None:
    pid = _new(as_, boards([lighting(), motor("Fire pump", "15")]))
    req = boards([lighting()], [motor("P-1", "15")])
    _revise(as_, pid, req)
    (c,) = _unresolved(as_, pid, 2)
    assert _resolve(as_, "reviewer", pid, 2, c).status_code == 200
    assert approve(as_("owner"), pid, 2).status_code == 200
    # Rewrite the stored resolution as an upgrade from the old id scheme would leave it.
    record = copy.deepcopy(revision_row(engine, pid, 2)["requirements"])
    (entry,) = record["safety_resolutions"]
    old = _old_id("life_safety", "load", "Fire pump", "fire pump", 0)
    assert old != entry["target_id"]
    entry["target_id"] = old
    update_row(engine, pid, 2, requirements=record, requirements_sha256=sha256_of(record))
    shown = _open(as_("owner"), pid, 2)
    assert shown["approval_current"] is False
    assert any(not x["resolved"] for x in shown["safety_concerns"])
    # Carried to a new revision, it still does not count.
    _revise(as_, pid, req)
    assert code(approve(as_("owner"), pid, 3)) == "approval_blocked_by_safety"


#: Positive claims (validated / approved for or ready for execution or construction).
FORBIDDEN_AR = ("جاهز للتنفيذ", "معتمد للتنفيذ", "للتنفيذ.", "صالح للتنفيذ", "معتمد هندسي")
FORBIDDEN_HE = ("מוכן לביצוע", "מאושר לביצוע", "מאושר לבנייה", "תוקף הנדסי")
NEGATED = {"ar": "ليست للتنفيذ", "he": "לא לביצוע"}


@pytest.mark.parametrize("lang", ["ar", "he"])
def test_rtl_approved_exports_imply_no_validation_or_readiness(
    engine: Engine, as_: Any, lang: str
) -> None:
    profile = {"key": f"synthetic-{lang}", "name": f"Synthetic {lang}", "language": lang}
    first = boards([lighting(), motor("Fire pump", "15")])
    first["profile"] = profile
    pid = _new(as_, first)
    req = boards([lighting()], [motor("P-1", "15")])
    req["profile"] = profile
    _revise(as_, pid, req)
    (c,) = _unresolved(as_, pid, 2)
    assert _resolve(as_, "reviewer", pid, 2, c).status_code == 200
    assert approve(as_("owner"), pid, 2).status_code == 200
    for fmt in ("pdf", "calculations_pdf"):
        got = _export(as_("owner"), pid, fmt, 2)
        assert got.status_code == 200, got.text[:300]
        printed = _pdf_text(got.content)
        forbidden = FORBIDDEN_AR if lang == "ar" else FORBIDDEN_HE
        # pdfplumber returns RTL runs in visual order with presentation forms:
        # normalise (NFKC) and read both directions.
        flat = unicodedata.normalize("NFKC", printed)
        readings = [flat, flat[::-1]]
        assert any(NEGATED[lang] in r for r in readings) or "NOT FOR EXECUTION" in flat
        hits = [p for r in readings for p in forbidden if p in r.replace(NEGATED[lang], "")]
        assert hits == [], (fmt, hits)
        assert "not engineer-validated" in printed or "engineer validation" in printed


def test_own_ai_pump_concerns_with_the_pump_typed_in_by_another_name_need_confirmation(
    engine: Engine, as_: Any, provider: StubProvider
) -> None:
    pid, _ = _fire(as_, provider)
    _revise(as_, pid, boards([lighting()], [motor("P-7", "15")]))
    found = _unresolved(as_, pid, 2)
    ai = [c for c in found if c["kind"] in ("mention", "item")]
    assert ai
    assert all(not c["carried"] for c in ai)
    results = [_resolve(as_, "reviewer", pid, 2, c).status_code for c in found]
    approved = approve(as_("owner"), pid, 2).status_code
    # Expected: the exclusion asks about P-7 (or refuses), so it is not approved silently.
    asked = all(c.get("confirmation_candidates") for c in ai)
    assert asked or approved != 200, (results, approved)


def test_own_ai_pump_concerns_on_revision_1_need_confirmation(
    engine: Engine, as_: Any, provider: StubProvider
) -> None:
    """M6f re-check: the revision's own life-safety concerns ask for every load, on revision 1 too."""
    pid, _ = _fire(as_, provider)
    ai = [c for c in _unresolved(as_, pid, 1) if c["kind"] in ("mention", "item")]
    assert ai
    for c in ai:
        assert c["carried"] is False
        assert c["confirmation_candidates"] is not None, c
        bare = {k: v for k, v in _body(c).items() if k != "not_this_equipment"}
        refused = as_("reviewer").post(_url(pid, 1), json=bare)
        if c["confirmation_candidates"]:
            assert code(refused) == "exclusion_confirmation_incomplete", refused.text[:300]
    for c in ai:
        assert _resolve(as_, "reviewer", pid, 1, c).status_code == 200
    entries = revision_row(engine, pid, 1)["requirements"]["ai"]["resolutions"]
    assert all("confirmed_loads" in e for e in entries)

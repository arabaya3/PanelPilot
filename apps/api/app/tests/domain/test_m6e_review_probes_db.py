"""M6e review probes against 06427a8: paths to approve with detected life-safety text.

Independent reviewer, black-box over the design routes (``panelpilot_test_*``
only). Passing tests pin what holds; strict xfails name the residual paths.
Synthetic data only.
"""

# ruff: noqa: RUF001  (homoglyph probe text)
from __future__ import annotations

import copy
from typing import Any

import pytest
from sqlalchemy.engine import Engine

from app.tests.api.v1.routes.test_export_integrity import (  # noqa: F401  (fixtures)
    _engine,
    _tenants,
    pytestmark,
    revision_row,
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
    _fire,
    approve,
    board,
    code,
    lighting,
    motor,
    request,
)

REASON = "Supplied and wired by the fire-protection contractor."


def _manual(as_: Any, *loads: dict[str, Any], name: str = "Manual") -> str:
    mdb = board()
    mdb["loads"] = list(loads) or [lighting()]
    saved = as_().post(
        "/design/projects", json={"name": name, "request": request(mdb), "note": "r1"}
    )
    assert saved.status_code == 201, saved.text[:300]
    return str(saved.json()["id"])


def _revise(as_: Any, pid: str, req: dict[str, Any], who: str = "engineer", **extra: Any) -> Any:
    return as_(who).post(
        f"/design/projects/{pid}/revisions", json={"request": req, "note": "r", **extra}
    )


def _resolve_all(as_: Any, pid: str, n: int, resolution: str = "excluded_from_scope") -> list[Any]:
    out = []
    for c in _open(as_("reviewer"), pid, n)["safety_concerns"]:
        if c["resolved"]:
            continue
        key = {"mention": "mention_id", "item": "item_id", "request": "request_id"}[c["kind"]]
        body = {key: c["target_id"], "resolution": resolution, "reason": REASON}
        if c.get("proposal_id") and c["kind"] != "request":
            body["proposal_id"] = c["proposal_id"]
        out.append(
            as_("reviewer").post(
                f"/design/projects/{pid}/revisions/{n}/requirements/safety-resolutions", json=body
            )
        )
    return out


def test_the_deprecated_clarification_route_is_gone(
    engine: Engine, as_: Any, provider: StubProvider
) -> None:
    pid, _ = _fire(as_, provider)
    gone = as_("reviewer").post(
        f"/design/projects/{pid}/revisions/1/requirements/safety-clarifications",
        json={
            "mention_id": "m0123456789abcdef",
            "statement": "not_life_safety",
            "reason": "x" * 12,
        },
    )
    assert gone.status_code == 410, gone.text[:300]


def test_a_used_proposal_cannot_be_deleted_so_its_concern_cannot_be_dropped(
    engine: Engine, as_: Any, provider: StubProvider
) -> None:
    pid, proposal = _fire(as_, provider)
    deleted = as_().client.delete(
        f"/design/projects/requirements/ai-proposals/{proposal['id']}",
        headers={"x-test-user": "engineer"},
    )
    assert deleted.status_code == 409, deleted.text[:300]
    assert code(approve(as_("reviewer"), pid)) == "approval_blocked_by_safety"


@pytest.mark.parametrize(
    "text",
    ["Fire pump FP-1", "FIRE-PUMP", "fire​pump", "Fіre pump", "Sprinkler booster"],
)
def test_a_life_safety_load_typed_by_hand_holds_and_cannot_be_excluded(as_: Any, text: str) -> None:
    pid = _manual(as_, lighting(), motor(text, "15"))
    assert code(approve(as_("reviewer"), pid)) == "approval_blocked_by_safety"
    for done in _resolve_all(as_, pid, 1):
        assert done.status_code == 409, done.text[:300]
    assert code(approve(as_("reviewer"), pid)) == "approval_blocked_by_safety"


@pytest.mark.parametrize("field", ["name", "customer", "consultant"])
def test_project_information_is_scanned(as_: Any, field: str) -> None:
    mdb = board()
    mdb["loads"] = [lighting()]
    req = request(mdb)
    req["info"][field] = "Fire pump room upgrade"
    saved = as_().post("/design/projects", json={"name": "Info", "request": req, "note": "r1"})
    pid = str(saved.json()["id"])
    assert code(approve(as_("reviewer"), pid)) == "approval_blocked_by_safety"


def test_removing_the_load_carries_the_concern_until_excluded(as_: Any) -> None:
    pid = _manual(as_, lighting(), motor("Fire pump", "15"))
    revised = _revise(as_, pid, request({**board(), "loads": [lighting()]}))
    assert revised.status_code == 201, revised.text[:300]
    assert code(approve(as_("owner"), pid, 2)) == "approval_blocked_by_safety"
    assert all(r.status_code == 200 for r in _resolve_all(as_, pid, 2))
    assert approve(as_("owner"), pid, 2).status_code == 200


@pytest.mark.xfail(
    strict=True,
    reason="finding at 06427a8: renaming 'Fire pump' to 'FP-1' makes the concern 'carried' "
    "(not present), so it may be excluded from scope while the 15 kW load is still drawn",
)
def test_renaming_the_load_does_not_allow_excluding_what_is_still_drawn(as_: Any) -> None:
    pid = _manual(as_, lighting(), motor("Fire pump", "15"))
    revised = _revise(as_, pid, request({**board(), "loads": [lighting(), motor("FP-1", "15")]}))
    assert revised.status_code == 201, revised.text[:300]
    assert code(approve(as_("owner"), pid, 2)) == "approval_blocked_by_safety"
    resolved = _resolve_all(as_, pid, 2)
    approved = approve(as_("owner"), pid, 2)
    assert any(r.status_code == 409 for r in resolved) or approved.status_code == 409


@pytest.mark.xfail(
    strict=True,
    reason="limitation at 06427a8: a new project copying the request with the load renamed "
    "'FP-1' has no history and no keyword, and is review-approved",
)
def test_a_new_project_copying_a_renamed_request_is_held(as_: Any) -> None:
    _manual(as_, lighting(), motor("Fire pump", "15"), name="Original")
    pid = _manual(as_, lighting(), motor("FP-1", "15"), name="Copy")
    assert code(approve(as_("reviewer"), pid)) == "approval_blocked_by_safety"


@pytest.mark.parametrize(
    "text",
    [
        pytest.param(
            "f i r e pump",
            marks=pytest.mark.xfail(strict=True, reason="limitation: spaced letters not detected"),
        ),
        pytest.param(
            "Pump P1 to NFPA 20",
            marks=pytest.mark.xfail(
                strict=True, reason="limitation: the fire-pump standard reference is not a term"
            ),
        ),
    ],
)
def test_evasive_spellings(as_: Any, text: str) -> None:
    pid = _manual(as_, lighting(), motor(text, "15"))
    assert code(approve(as_("reviewer"), pid)) == "approval_blocked_by_safety"


@pytest.mark.xfail(
    strict=True,
    reason="finding at 06427a8: v2 configuration text (names) is not scanned; a configuration "
    "naming 'Fire pump …' is review-approved",
)
def test_a_configuration_naming_life_safety_equipment(
    engine: Engine, as_: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """v2: free text inside a configuration is outside the request scan; see what happens."""
    from app.tests.design.model.compat_support import DESIGNED
    from app.tests.design.model.v2_support import CONFIGS, config_case

    monkeypatch.setenv("DESIGN_SNAPSHOT_SCHEMA", "pp-design/2")
    case = "pe_n_distribution"
    req = copy.deepcopy(DESIGNED[CONFIGS[case]["fixture"]]["request"])
    saved = as_().post("/design/projects", json={"name": "Cfg", "request": req, "note": "r1"})
    assert saved.status_code == 201, saved.text[:300]
    pid = str(saved.json()["id"])
    configuration = config_case(case)[1]
    replaced = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for k, v in node.items():
                if k in ("name", "label", "title", "description") and isinstance(v, str):
                    node[k] = "Fire pump " + v
                    replaced.append(k)
                else:
                    walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(configuration)
    revised = _revise(as_, pid, req, configuration=configuration)
    if not replaced or revised.status_code != 201:
        pytest.xfail(f"no free text accepted in the configuration ({revised.status_code})")
    approved = approve(as_("reviewer"), pid, 2)
    assert approved.status_code == 409, ("configuration text not scanned", replaced[:5])

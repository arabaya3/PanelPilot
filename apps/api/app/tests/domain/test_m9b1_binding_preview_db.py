"""M9b1 integration: a guided binding, previewed by the read-only validate route (SYNTHETIC data).

Drives the real routes against the disposable Postgres (``TEST_DATABASE_URL``)
with the synthetic two-board example of M7a (MB-1 feeds SB-1). Names and
markings are illustrative only, not engineering values.

Contract ``docs/architecture/m9b1-guided-bindings.md``: the preview is the
existing ``configuration/validate`` composition: it saves nothing, creates no
revision, grants no approval and changes nothing in the project; what it shows
is what a save of the same configuration composes; the server refuses an
inconsistent binding on both, with nothing saved.
"""

from __future__ import annotations

import copy
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.engine import Engine

from app.design.model import ids
from app.tests.api.v1.routes.test_export_integrity import (  # noqa: F401  (fixtures)
    _app,
    _Caller,
    _caller,
    _client,
    _engine,
    _tenants,
    _user,
    approve,
    pytestmark,
    revision_row,
)
from app.tests.design.model import m7a_support
from app.tests.domain import review_support
from app.tests.domain.test_m7b_configuration_db import (  # noqa: F401  (fixtures)
    V3,
    Switch,
    _count,
    _new_project,
    _opened,
    _revise,
    _switch,
)
from app.tests.domain.test_m9a_guided_patterns_db import PATTERN, _configuration


@pytest.fixture(autouse=True)
def _saved_by_another_member(monkeypatch: pytest.MonkeyPatch) -> None:
    review_support.author_revisions_as_another_member(monkeypatch)


def _bound(**change: Any) -> dict[str, Any]:
    """The M9a two-slot pattern with a wire on net n1, bound as the guided editor writes it."""
    config = _configuration(bound=True)
    config["patterns"][0]["nets"][0]["connections"] = [
        {"from": 0, "to": 1, "via": "wire", "core": None, "label": None},
        {"from": 1, "to": 2, "via": "wire", "core": None, "label": None},
    ]
    config["patterns"][0]["nets"][1]["connections"] = [
        {"from": 0, "to": 1, "via": "wire", "core": None, "label": None}
    ]
    config["bindings"][0].update(change)
    return config


def _validate(client: TestClient, project_id: str, configuration: dict[str, Any]) -> Any:
    return client.post(
        f"/design/projects/{project_id}/configuration/validate",
        json={"configuration": configuration},
    )


def _project(client: TestClient, switch: Switch) -> str:
    switch(V3)
    project_id = _new_project(client, V3)
    # Revision 2: the definitions, components, strip and pattern, unbound (as M9a saves them).
    saved = _revise(client, project_id, m7a_support.request(), configuration=_configuration())
    assert saved.status_code == 201, saved.text[:600]
    return project_id


def _rows(model: dict[str, Any]) -> dict[str, Any]:
    """What the preview shows of a model, ids included (they are derived, so they must match)."""
    return {
        "nets": sorted((n["id"], n["signal"], tuple(sorted(n["members"]))) for n in model["nets"]),
        "connections": sorted(
            (c["id"], c["start"], c["end"], c["via"]) for c in model["connections"]
        ),
        "wires": sorted((w["wire_id"], w["start"], w["end"], w["signal"]) for w in model["wires"]),
        "points": sorted(
            (p["id"], p["marking"], p["position"], p["level"])
            for s in model["strips"]
            for p in s["points"]
        ),
    }


def test_the_preview_saves_nothing_and_matches_what_a_save_composes(
    client: TestClient, switch: Switch, engine: Engine
) -> None:
    project_id = _project(client, switch)
    before = revision_row(engine, project_id, 2)
    preview = _validate(client, project_id, _bound())
    assert preview.status_code == 200, preview.text[:600]
    shown = preview.json()["model"]
    # Nothing saved or changed: same revisions, revision 2 byte-identical.
    assert _count(client, project_id) == 2
    after = revision_row(engine, project_id, 2)
    for column in ("requirements", "requirements_sha256", "design_snapshot", "snapshot_sha256"):
        assert after[column] == before[column], column
    # The binding's rows are there, under the ids the browser derives to pick them.
    assert ids.derive("x", "bind-1", "n1", "0") in {c["id"] for c in shown["connections"]}
    assert ids.derive("w", "bind-1", "n1", "0") in {w["wire_id"] for w in shown["wires"]}
    assert ids.derive("p", "strip-xp", "bind-1", "p1") in {
        p["id"] for s in shown["strips"] for p in s["points"]
    }
    # Saving the same configuration composes exactly what the preview showed.
    saved = _revise(client, project_id, m7a_support.request(), configuration=_bound())
    assert saved.status_code == 201, saved.text[:600]
    stored = _opened(client, project_id, 3)["model_v2"]
    assert _rows(stored) == _rows(shown)


def test_the_preview_grants_no_approval_and_keeps_an_existing_one(
    client: TestClient, switch: Switch
) -> None:
    project_id = _project(client, switch)
    assert approve(client, project_id, 2).status_code == 200
    assert _validate(client, project_id, _bound()).status_code == 200
    opened = _opened(client, project_id, 2)
    assert opened["approval_current"] is True
    assert _count(client, project_id) == 2


def test_the_preview_of_an_unapproved_revision_does_not_approve_it(
    client: TestClient, switch: Switch
) -> None:
    project_id = _project(client, switch)
    assert _validate(client, project_id, _bound()).status_code == 200
    assert _opened(client, project_id, 2)["approval_current"] is not True


def _wrong_board_component(config: dict[str, Any]) -> None:
    config["components"][0]["board"] = "SB-1"


def _wrong_template(config: dict[str, Any]) -> None:
    config["bindings"][0]["slots"]["src"] = {"component": "comp-q2"}


def _missing_marking(config: dict[str, Any]) -> None:
    config["bindings"][0]["markings"] = {"p1": "7"}


def _unknown_strip(config: dict[str, Any]) -> None:
    config["bindings"][0]["strips"] = {"out": "strip-gone"}


def _role_unmapped(config: dict[str, Any]) -> None:
    config["bindings"][0]["strips"] = {}


def _unknown_circuit(config: dict[str, Any]) -> None:
    config["bindings"][0]["circuit"] = "c99"


def _unknown_pattern(config: dict[str, Any]) -> None:
    config["bindings"][0]["pattern"] = "tenant/not-held/v1"


REFUSED: dict[str, tuple[Any, str]] = {
    "wrong_board_component": (_wrong_board_component, "board_mismatch"),
    "wrong_template": (_wrong_template, "conflicting_binding"),
    "missing_marking": (_missing_marking, "missing_binding"),
    "unknown_strip": (_unknown_strip, "unknown_reference"),
    "role_unmapped": (_role_unmapped, "missing_binding"),
    "unknown_circuit": (_unknown_circuit, "unknown_reference"),
    "unknown_pattern": (_unknown_pattern, "unknown_pattern"),
}


@pytest.mark.parametrize("case", sorted(REFUSED))
def test_an_inconsistent_binding_is_refused_by_preview_and_save_with_nothing_saved(
    client: TestClient, switch: Switch, case: str
) -> None:
    project_id = _project(client, switch)
    mutate, code = REFUSED[case]
    config = copy.deepcopy(_bound())
    mutate(config)
    for response in (
        _validate(client, project_id, config),
        _revise(client, project_id, m7a_support.request(), configuration=config),
    ):
        assert response.status_code == 422, response.text[:400]
        body = response.json()
        assert body["code"] == "configuration_refused", body
        assert body["params"]["refusal"] == code, body
    assert _count(client, project_id) == 2


def test_another_tenant_cannot_preview_the_project(
    client: TestClient, switch: Switch, caller: _Caller, tenants: tuple[str, str]
) -> None:
    project_id = _project(client, switch)
    owner = caller.user
    caller.user = _user(tenants[1], email="intruder@example.com")
    assert _validate(client, project_id, _bound()).status_code == 404
    caller.user = owner
    assert _count(client, project_id) == 2


def test_a_saved_binding_reopens_exactly_and_an_edit_leaves_the_old_revision(
    client: TestClient, switch: Switch, engine: Engine
) -> None:
    project_id = _project(client, switch)
    assert (
        _revise(client, project_id, m7a_support.request(), configuration=_bound()).status_code
        == 201
    )
    assert _opened(client, project_id, 3)["configuration"]["bindings"] == _bound()["bindings"]
    before = revision_row(engine, project_id, 3)
    edited = _bound(markings={"p1": "17", "p2": "18"})
    assert (
        _revise(client, project_id, m7a_support.request(), configuration=edited).status_code == 201
    )
    after = revision_row(engine, project_id, 3)
    for column in ("requirements", "requirements_sha256", "design_snapshot", "snapshot_sha256"):
        assert after[column] == before[column], column
    assert PATTERN in {
        p["ref"]
        for p in revision_row(engine, project_id, 4)["design_snapshot"]["model"]["patterns"]
    }

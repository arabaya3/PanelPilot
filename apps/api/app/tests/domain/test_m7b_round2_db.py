"""M7b round 2: unbound components, direct strip points, refusal params, link provenance.

SYNTHETIC data only (``m7a_support``: MB-1 feeds SB-1). Markings and
designations are illustrative text, not engineering values. Each test failed
against ``bcf3f13`` when written.
"""

from __future__ import annotations

import itertools
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.engine import Engine

from app.design.model import ids
from app.tests.api.v1.routes.test_export_integrity import (  # noqa: F401  (fixtures)
    _app,
    _caller,
    _client,
    _engine,
    _tenants,
    pytestmark,
    revision_row,
)
from app.tests.design.model import m7a_support
from app.tests.domain.test_m7b_configuration_db import (  # noqa: F401  (fixtures)
    V2,
    V3,
    Switch,
    _new_project,
    _opened,
    _revise,
    _saved_by_another_member,
    _switch,
)

_SWITCH = "global/legacy-switch_disconnector/v1"


def _designation(product: str) -> dict[str, Any]:
    return {"function": None, "location": None, "product": product}


def _browser_configuration(points: bool = False) -> dict[str, Any]:
    """The production-browser case: two strips, two components, no bindings."""
    x1: dict[str, Any] = {
        "id": "X1",
        "board": "MB-1",
        "designation": _designation("X1"),
        "template": None,
    }
    if points:
        x1["points"] = [{"id": "a", "marking": "1"}, {"id": "b", "marking": "2", "kind": "pe"}]
    return {
        "templates": [],
        "patterns": [],
        "strips": [
            x1,
            {"id": "X10", "board": "SB-1", "designation": _designation("X10"), "template": None},
        ],
        "components": [
            {"id": "Q1", "board": "MB-1", "template": _SWITCH, "designation": _designation("Q1")},
            {
                "id": "Q10",
                "board": "SB-1",
                "template": _SWITCH,
                "designation": _designation("Q10"),
            },
        ],
        "bindings": [],
    }


def test_unbound_configuration_components_are_composed(
    client: TestClient, engine: Engine, switch: Switch
) -> None:
    switch(V2)
    project_id = _new_project(client, V2)
    response = _revise(
        client, project_id, m7a_support.request(), configuration=_browser_configuration()
    )
    assert response.status_code == 201, response.text[:500]
    model = revision_row(engine, project_id, 2)["design_snapshot"]["model"]
    by_id = {c["id"]: c for c in model["components"]}
    for name, board in (("Q1", "MB-1"), ("Q10", "SB-1")):
        component = by_id[ids.derive("k", name)]
        assert component["board_id"] == board
        assert component["template_ref"] == _SWITCH
        assert component["designation"]["product"] == name
        assert component["legacy_device_id"] is None
    assert {s["designation"]["product"] for s in model["strips"]} == {"X1", "X10"}
    findings = _opened(client, project_id, 2)["findings"]
    clashes = [f for f in findings if f["code"] == "designation_duplicate"]
    # The configured -Q1 repeats the derived incomer =MB-1-Q1: a finding, not a refusal.
    (clash,) = [f for f in clashes if f["board_id"] == "MB-1"]
    assert clash["values"]["designation"] == "-Q1 / =MB-1-Q1"


def test_an_unbound_component_on_an_unknown_template_is_refused_naming_it(
    client: TestClient, switch: Switch
) -> None:
    switch(V2)
    project_id = _new_project(client, V2)
    configuration = _browser_configuration()
    configuration["components"][1]["template"] = "tenant/not-here/v1"
    response = _revise(client, project_id, m7a_support.request(), configuration=configuration)
    assert response.status_code == 422, response.text[:500]
    body = response.json()
    assert body["code"] == "configuration_refused"
    assert body["params"]["refusal"] == "unknown_template"
    assert body["params"]["kind"] == "template"
    assert body["params"]["ref"] == "tenant/not-here/v1"
    assert body["params"]["component"] == "Q10"


def test_a_sent_configuration_refusal_names_ref_kind_and_entry(
    client: TestClient, switch: Switch
) -> None:
    switch(V2)
    project_id = _new_project(client, V2)
    configuration = _browser_configuration()
    configuration["strips"][1]["board"] = "SB-9"
    response = _revise(client, project_id, m7a_support.request(), configuration=configuration)
    assert response.status_code == 422
    params = response.json()["params"]
    assert params["refusal"] == "unknown_reference"
    assert (params["ref"], params["kind"], params["strip"]) == ("SB-9", "board", "X10")


def test_direct_strip_points_are_terminals_and_link_endpoints(
    client: TestClient, engine: Engine, switch: Switch
) -> None:
    switch(V3)
    project_id = _new_project(client, V3)
    configuration = _browser_configuration(points=True)
    response = _revise(client, project_id, m7a_support.request(), configuration=configuration)
    assert response.status_code == 201, response.text[:500]
    strip = next(
        s
        for s in revision_row(engine, project_id, 2)["design_snapshot"]["model"]["strips"]
        if s["designation"]["product"] == "X1"
    )
    assert [(p["marking"], p["position"], p["level"], p["kind"]) for p in strip["points"]] == [
        ("1", 1, 1, "unspecified"),
        ("2", 2, 1, "pe"),
    ]
    assert all(p["pattern_ref"] is None for p in strip["points"])
    terminals = _opened(client, project_id, 2)["model_v2"]["terminals"]
    point_id = strip["points"][0]["id"]
    assert any(row.get("terminal_id") == point_id for row in terminals), terminals[:3]
    link = {
        "fed_board": "SB-1",
        "from_end": {"terminal": {"point": point_id, "side": "inner"}},
    }
    linked = _revise(client, project_id, m7a_support.request([link]))
    assert linked.status_code == 201, linked.text[:500]
    (stored,) = revision_row(engine, project_id, 3)["design_snapshot"]["model"]["links"]
    assert stored["ends"][0]["terminal"] == {"point": point_id, "side": "inner"}
    assert stored["ends"][0]["resolution"] == "resolved"


def test_a_binding_point_colliding_with_a_direct_point_is_refused(
    client: TestClient, switch: Switch
) -> None:
    switch(V2)
    project_id = _new_project(client, V2)
    configuration = m7a_support.configuration()
    configuration["strips"][0]["points"] = [{"id": "x", "marking": "F1"}]
    response = _revise(client, project_id, m7a_support.request(), configuration=configuration)
    assert response.status_code == 422, response.text[:500]
    params = response.json()["params"]
    assert params["refusal"] == "strip_point_conflict"
    assert params["strip"] == "XF"
    assert params["binding"] == "mb1-feeder"


def test_link_provenance_is_kept_while_its_user_content_is_unchanged(
    client: TestClient, engine: Engine, switch: Switch, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.domain import design_projects

    clock = (f"2026-11-1{n}T00:00:00+00:00" for n in itertools.count(1))
    monkeypatch.setattr(design_projects, "_now_iso", lambda: next(clock))
    switch(V3)
    link = {"fed_board": "SB-1", "from_end": {"label": "Way 7"}, "attributes": {"cores": 4}}
    project_id = _new_project(client, V3)
    for request in (
        m7a_support.request([link]),  # 2: entered
        m7a_support.request([link]),  # 3: unchanged
        m7a_support.request([{**link, "attributes": {"cores": 5}}]),  # 4: changed
    ):
        assert _revise(client, project_id, request).status_code == 201

    def stamp(number: int) -> tuple[Any, Any]:
        (stored,) = revision_row(engine, project_id, number)["design_snapshot"]["model"]["links"]
        provenance = stored["provenance"]
        return provenance["entered_by_user_id"], provenance["entered_at"]

    assert stamp(2)[1] is not None
    assert stamp(3) == stamp(2)
    assert stamp(4)[1] != stamp(2)[1]

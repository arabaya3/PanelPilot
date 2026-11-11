"""M7b review round 2: failing tests for defects in the round-2 composer.

SYNTHETIC data only (``m7a_support``). Markings are illustrative text.
"""

from __future__ import annotations

import itertools
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.engine import Engine

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
    _revise,
    _switch,
)


def test_a_direct_point_and_a_binding_point_never_share_an_id(
    client: TestClient, engine: Engine, switch: Switch
) -> None:
    """A binding named ``direct`` derives the same point id as a direct point.

    Both ids are ``ids.derive("p", strip, <"direct" | binding id>, <point id>)``,
    so binding ``direct`` allocating pattern point ``l`` and a direct point
    ``l`` (different markings) give one id to two strip points.
    """
    switch(V2)
    project_id = _new_project(client, V2)
    configuration = m7a_support.configuration()
    configuration["bindings"][0]["id"] = "direct"
    strip = next(s for s in configuration["strips"] if s["id"] == "XF")
    strip["points"] = [{"id": "l", "marking": "Z9"}]
    response = _revise(client, project_id, m7a_support.request(), configuration=configuration)
    if response.status_code == 201:
        model = revision_row(engine, project_id, 2)["design_snapshot"]["model"]
        point_ids = [p["id"] for s in model["strips"] for p in s["points"]]
        assert len(point_ids) == len(set(point_ids)), point_ids
        return
    # Today the duplicate reaches the v2 codec: composition_invalid, ref "",
    # naming no strip or binding, so the editor cannot mark the entry.
    params = response.json()["params"]
    assert params["refusal"] != "composition_invalid", params
    assert params.get("strip") == "XF", params


def test_link_provenance_is_restamped_when_its_terminal_point_changes_marking(
    client: TestClient, engine: Engine, switch: Switch, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The web editor reuses a removed point's id (``p2``) for the next point.

    Points ``p1`` "1", ``p2`` "2" and a link from ``p2``; the user removes
    ``p2`` and adds marking "7" (the editor names it ``p2`` again). The link's
    stored end is byte-equal, so links.derive keeps the old who/when although
    the terminal it lands on now carries another marking.
    """
    from app.domain import design_projects

    clock = (f"2026-11-1{n}T00:00:00+00:00" for n in itertools.count(1))
    monkeypatch.setattr(design_projects, "_now_iso", lambda: next(clock))
    switch(V3)
    project_id = _new_project(client, V3)

    def configuration(second: str) -> dict[str, Any]:
        return {
            "templates": [],
            "patterns": [],
            "strips": [
                {
                    "id": "strip-1",
                    "board": "MB-1",
                    "designation": {"function": None, "location": None, "product": "X1"},
                    "template": None,
                    "points": [{"id": "p1", "marking": "1"}, {"id": "p2", "marking": second}],
                }
            ],
            "components": [],
            "bindings": [],
        }

    first = _revise(client, project_id, m7a_support.request(), configuration=configuration("2"))
    assert first.status_code == 201, first.text[:500]
    strip = revision_row(engine, project_id, 2)["design_snapshot"]["model"]["strips"][0]
    point = next(p["id"] for p in strip["points"] if p["marking"] == "2")
    link = {"fed_board": "SB-1", "from_end": {"terminal": {"point": point, "side": "inner"}}}
    request = m7a_support.request([link])
    assert _revise(client, project_id, request, configuration=configuration("2")).status_code == 201
    assert _revise(client, project_id, request, configuration=configuration("7")).status_code == 201

    def stamp(number: int) -> Any:
        (stored,) = revision_row(engine, project_id, number)["design_snapshot"]["model"]["links"]
        return stored["provenance"]["entered_at"]

    assert stamp(3) is not None
    assert stamp(4) != stamp(3), "provenance carried onto a terminal with another marking"

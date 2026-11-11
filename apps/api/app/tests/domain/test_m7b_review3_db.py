"""M7b review round 3: link provenance on component terminals.

SYNTHETIC data only (``m7a_support`` boards, ``v2_support`` template shape).
"""

from __future__ import annotations

import copy
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
from app.tests.design.model.v2_support import CONFIGS, config_case
from app.tests.domain.test_m7b_configuration_db import (  # noqa: F401  (fixtures)
    V3,
    Switch,
    _new_project,
    _revise,
    _switch,
)


def _template(ref: str, marking: str) -> dict[str, Any]:
    case = config_case(next(iter(CONFIGS)))
    base = next(x for x in case if isinstance(x, dict) and "templates" in x)["templates"][0]
    template: dict[str, Any] = copy.deepcopy(base)
    template.pop("symbol", None)
    template["ref"] = ref
    template["terminals"] = [
        {"id": "t1", "marking": marking, "kind": "coil", "required": True, "group": None}
    ]
    return template


def test_link_provenance_is_restamped_when_a_component_terminal_reads_differently(
    client: TestClient, engine: Engine, switch: Switch, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``links._said`` compares a component end by designation, terminal id and board.

    The terminal id ``t1`` is the same under both templates, but it is marked
    "A1" under one and "Z9" under the other; the drawn terminal changed, yet
    the old who/when is kept.
    """
    from app.domain import design_projects

    clock = (f"2026-11-1{n}T00:00:00+00:00" for n in itertools.count(1))
    monkeypatch.setattr(design_projects, "_now_iso", lambda: next(clock))
    switch(V3)
    project_id = _new_project(client, V3)
    one, two = "tenant/synth-r3-a/v1", "tenant/synth-r3-b/v1"

    def configuration(chosen: str) -> dict[str, Any]:
        return {
            "templates": [_template(one, "A1"), _template(two, "Z9")],
            "patterns": [],
            "strips": [],
            "components": [
                {
                    "id": "Q1",
                    "board": "MB-1",
                    "template": chosen,
                    "designation": {"function": None, "location": None, "product": "K7"},
                }
            ],
            "bindings": [],
        }

    first = _revise(client, project_id, m7a_support.request(), configuration=configuration(one))
    assert first.status_code == 201, first.text[:500]
    model = revision_row(engine, project_id, 2)["design_snapshot"]["model"]
    component = next(c["id"] for c in model["components"] if c["template_ref"] == one)
    link = {
        "fed_board": "SB-1",
        "from_end": {"terminal": {"component": component, "terminal": "t1"}},
    }
    request = m7a_support.request([link])
    saved = _revise(client, project_id, request, configuration=configuration(one))
    assert saved.status_code == 201, saved.text[:500]
    changed = _revise(client, project_id, request, configuration=configuration(two))
    assert changed.status_code == 201, changed.text[:500]

    def stamp(number: int) -> Any:
        (stored,) = revision_row(engine, project_id, number)["design_snapshot"]["model"]["links"]
        return stored["provenance"]["entered_at"]

    assert stamp(3) is not None
    assert stamp(4) != stamp(3), "provenance kept on a terminal now marked Z9, not A1"

"""M9d integration: a binding reused on another board (SYNTHETIC data).

Drives the real routes against the disposable Postgres (``TEST_DATABASE_URL``)
with the synthetic two-board example of M7a (MB-1 feeds SB-1). Names and
markings are illustrative only, not engineering values.

Contract ``docs/architecture/m9d-binding-reuse.md``: "Use on another board"
adds a binding of the same pattern with every board-specific choice unset (the
guided editor writes exactly the shape tested here); the pattern is shared, not
copied; the server composes or refuses it under the existing contract; nothing
of the source binding (its endpoints, cable or wire details) reaches the new
one; earlier revisions are unchanged.
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
    pdf_pages,
    pytestmark,
    revision_export,
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
from app.tests.domain.test_m9a_guided_patterns_db import LOAD, PATTERN, SOURCE
from app.tests.domain.test_m9b1_binding_preview_db import _project, _validate
from app.tests.domain.test_m9c_guided_wire_details_db import DETAILS, _configured


@pytest.fixture(autouse=True)
def _saved_by_another_member(monkeypatch: pytest.MonkeyPatch) -> None:
    review_support.author_revisions_as_another_member(monkeypatch)


def _designation(product: str) -> dict[str, Any]:
    return {"function": None, "location": None, "product": product}


def _two_boards(wires: dict[str, Any]) -> dict[str, Any]:
    """bind-1 on MB-1 (with wire details); SB-1's own strip and components, as entered."""
    config = _configured(wires)
    config["strips"].append(
        {"id": "strip-ys", "board": "SB-1", "template": None, "designation": _designation("YS")}
    )
    config["components"] += [
        {"id": "comp-s1", "board": "SB-1", "template": SOURCE, "designation": _designation("KC")},
        {"id": "comp-s2", "board": "SB-1", "template": LOAD, "designation": _designation("KD")},
    ]
    return config


def _reused(config: dict[str, Any]) -> dict[str, Any]:
    """The draft "Use on another board" adds: the pattern only, everything else unset."""
    return {
        "id": "bind-1-SB-1",
        "pattern": PATTERN,
        "board": "SB-1",
        "circuit": None,
        "slots": {},
        "strips": {},
        "markings": {},
        "cable": None,
        "wires": {},
    }


def _completed(binding: dict[str, Any]) -> dict[str, Any]:
    """The draft completed by hand for SB-1."""
    done = copy.deepcopy(binding)
    done["slots"] = {"src": {"component": "comp-s1"}, "dst": {"component": "comp-s2"}}
    done["strips"] = {"out": "strip-ys"}
    done["markings"] = {"p1": "21", "p2": "22"}
    return done


def _wires_of(model: dict[str, Any], binding: str) -> list[dict[str, Any]]:
    keys = ("n1/0", "n1/2", "n2/0")
    wanted = {ids.derive("w", binding, *key.split("/")): key for key in keys}
    return [w for w in model["wires"] if w["wire_id"] in wanted]


def test_a_reused_binding_shares_the_pattern_and_nothing_board_specific(
    client: TestClient, switch: Switch, engine: Engine
) -> None:
    project_id = _project(client, switch)
    first = _revise(client, project_id, m7a_support.request(), configuration=_two_boards(DETAILS))
    assert first.status_code == 201, first.text[:600]
    before = revision_row(engine, project_id, 3)
    config = _two_boards(DETAILS)
    source = copy.deepcopy(config["bindings"][0])
    config["bindings"].append(_completed(_reused(config)))
    preview = _validate(client, project_id, config)
    assert preview.status_code == 200, preview.text[:600]
    saved = _revise(client, project_id, m7a_support.request(), configuration=config)
    assert saved.status_code == 201, saved.text[:600]
    opened = _opened(client, project_id, 4)
    stored = opened["configuration"]
    # One pattern, shared; the source binding exactly as it was.
    assert [p["ref"] for p in stored["patterns"]] == [PATTERN]
    assert stored["bindings"][0] == source
    assert stored["bindings"][1]["wires"] == {}
    assert stored["bindings"][1]["cable"] is None
    model = opened["model_v2"]
    # The source keeps its wire details; the new binding's wires hold none, and its ends are SB-1's.
    assert any(w["number"]["value"] == "W7" for w in _wires_of(model, "bind-1"))
    new_wires = _wires_of(model, "bind-1-SB-1")
    assert new_wires
    for wire in new_wires:
        for field in ("number", "cross_section_mm2", "colour"):
            assert wire[field]["value"] is None, (wire["wire_id"], field)
        ends = (wire["start"], wire["end"])
        assert not any(end.startswith(("KA:", "KB:", "XP:")) for end in ends), ends
        assert wire["board_id"] == "SB-1"
    # The earlier revision is untouched.
    after = revision_row(engine, project_id, 3)
    for column in ("requirements", "requirements_sha256", "design_snapshot", "snapshot_sha256"):
        assert after[column] == before[column], column
    # The wire schedule prints the new wires as not set, the source's as entered.
    text = " ".join(
        " ".join(page.split())
        for page in pdf_pages(revision_export(client, project_id, 4, "wire_schedule_pdf").content)
    )
    assert "W7 [user]" in text
    assert "KC:A1" in text


def test_an_uncompleted_draft_is_refused_with_nothing_saved(
    client: TestClient, switch: Switch
) -> None:
    project_id = _project(client, switch)
    config = _two_boards({})
    config["bindings"].append(_reused(config))
    for response in (
        _validate(client, project_id, config),
        _revise(client, project_id, m7a_support.request(), configuration=config),
    ):
        assert response.status_code == 422, response.text[:400]
        body = response.json()
        assert body["code"] == "configuration_refused", body
        assert body["params"]["binding"] == "bind-1-SB-1", body
    assert _count(client, project_id) == 2


def test_a_source_board_component_in_the_draft_is_refused(
    client: TestClient, switch: Switch
) -> None:
    project_id = _project(client, switch)
    config = _two_boards({})
    leaked = _completed(_reused(config))
    leaked["slots"]["src"] = {"component": "comp-q1"}  # MB-1's part, not SB-1's
    config["bindings"].append(leaked)
    response = _revise(client, project_id, m7a_support.request(), configuration=config)
    assert response.status_code == 422, response.text[:400]
    assert response.json()["params"]["refusal"] == "board_mismatch"
    assert _count(client, project_id) == 2


def test_the_reused_revision_is_approved_by_another_member_and_stays_in_its_tenant(
    client: TestClient, switch: Switch, caller: _Caller, tenants: tuple[str, str]
) -> None:
    project_id = _project(client, switch)
    config = _two_boards({})
    config["bindings"].append(_completed(_reused(config)))
    assert (
        _revise(client, project_id, m7a_support.request(), configuration=config).status_code == 201
    )
    assert approve(client, project_id, 3).status_code == 200
    assert _opened(client, project_id, 3)["approval_current"] is True
    owner = caller.user
    caller.user = _user(tenants[1], email="intruder-m9d@example.com")
    assert client.get(f"/design/projects/{project_id}", params={"revision": 3}).status_code == 404
    caller.user = owner

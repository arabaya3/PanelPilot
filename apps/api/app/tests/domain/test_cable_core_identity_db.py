"""Cable-core identity is (board, cable, index) (SYNTHETIC data).

v1 numbers cables per board, so ``c1-cable`` on MB-1 and ``c1-cable`` on SB-1
are two cables; the v2 model already resolves a core's ``cable_id`` within its
connection's board. Before this change the composer and the stored-model reader
keyed "one connection per core" by ``(cable_id, index)`` only, so reusing a
cable-core pattern on both boards (M9d) was refused as ``core_reused``.

Contract: ``docs/architecture/cable-core-identity.md``. Drives the real routes
against the disposable Postgres (``TEST_DATABASE_URL``).
"""

from __future__ import annotations

import copy
import re
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.engine import Engine

from app.design.model import read, versions
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
from app.tests.domain.test_m9b1_binding_preview_db import _project, _validate
from app.tests.domain.test_m9d_binding_reuse_db import _completed, _reused, _two_boards


@pytest.fixture(autouse=True)
def _saved_by_another_member(monkeypatch: pytest.MonkeyPatch) -> None:
    review_support.author_revisions_as_another_member(monkeypatch)


def _cabled(cable_mb: str | None, cable_sb: str | None) -> dict[str, Any]:
    """bind-1 on MB-1 and its reuse on SB-1; net n2 joined by core 1 of each binding's cable."""
    config = _two_boards({})
    config["patterns"][0]["nets"][1]["connections"] = [
        {"from": 0, "to": 1, "via": "cable_core", "core": 1, "label": None}
    ]
    config["bindings"][0]["cable"] = cable_mb
    reused = _completed(_reused(config))
    reused["cable"] = cable_sb
    config["bindings"].append(reused)
    return config


def _cores(model: dict[str, Any]) -> list[tuple[str, str, int]]:
    """Each cable core as the API shows it (``cable#index``), with the board of its net."""
    board_of = {n["id"]: n["board_id"] for n in model["nets"]}
    out = []
    for c in model["connections"]:
        if c["cable_core"] is None:
            continue
        cable, index = c["cable_core"].rsplit("#", 1)
        out.append((board_of[c["net_id"]], cable, int(index)))
    return sorted(out)


def test_core_1_of_c1_cable_on_two_boards_is_two_cores(
    client: TestClient, switch: Switch, engine: Engine
) -> None:
    project_id = _project(client, switch)
    config = _cabled("c1-cable", "c1-cable")
    preview = _validate(client, project_id, config)
    assert preview.status_code == 200, preview.text[:600]
    saved = _revise(client, project_id, m7a_support.request(), configuration=config)
    assert saved.status_code == 201, saved.text[:600]
    expected = [("MB-1", "c1-cable", 1), ("SB-1", "c1-cable", 1)]
    assert _cores(preview.json()["model"]) == expected
    assert _cores(_opened(client, project_id, 3)["model_v2"]) == expected
    # The stored snapshot reads back through the v2 reader with both cores, board by net.
    row = revision_row(engine, project_id, 3)
    model = read.read_snapshot(row["design_snapshot"]).v2
    assert model is not None
    boards = {n.id: n.board_id for n in model.nets}
    assert (
        sorted(
            (boards[x.net_id], x.core.cable_id, x.core.index) for x in model.connections if x.core
        )
        == expected
    )
    # The drawing prints each connection's core as held, with each board's terminals.
    text = " ".join(
        " ".join(page.split())
        for page in pdf_pages(
            revision_export(client, project_id, 3, "connection_drawing_pdf").content
        )
    )
    # Each board's core is printed beside that board's own terminal (KA on MB-1, KC on SB-1).
    for terminal in ("MB-1/KA", "SB-1/KC"):
        assert re.search(rf"{terminal}\S*.{{0,240}}?cable c1-cable core 1", text), terminal


def test_core_1_twice_on_one_boards_cable_is_still_refused(
    client: TestClient, switch: Switch
) -> None:
    project_id = _project(client, switch)
    config = _cabled("c1-cable", "c1-cable")
    # Two cable-core connections in the shared pattern on core 1: twice on each board's cable.
    config["patterns"][0]["nets"][0]["connections"].append(
        {"from": 0, "to": 1, "via": "cable_core", "core": 1, "label": None}
    )
    for response in (
        _validate(client, project_id, config),
        _revise(client, project_id, m7a_support.request(), configuration=config),
    ):
        assert response.status_code == 422, response.text[:400]
        params = response.json()["params"]
        assert params["refusal"] == "core_reused", params
        assert params["cable"] == "c1-cable", params
    assert _count(client, project_id) == 2


def test_a_cable_the_bindings_board_does_not_have_is_refused(
    client: TestClient, switch: Switch
) -> None:
    """MB-1's c2-cable exists, but not on SB-1: a reference resolves on the binding's board only."""
    project_id = _project(client, switch)
    config = _cabled("c1-cable", "c2-cable")
    response = _revise(client, project_id, m7a_support.request(), configuration=config)
    assert response.status_code == 422, response.text[:400]
    params = response.json()["params"]
    assert params["refusal"] == "unknown_reference", params
    assert params["binding"] == "bind-1-SB-1", params
    assert _count(client, project_id) == 2


def test_the_stored_reader_refuses_a_core_twice_on_one_boards_cable(
    client: TestClient, switch: Switch, engine: Engine
) -> None:
    """The reader's own rule: a stored model repeating a core on the same board's cable."""
    project_id = _project(client, switch)
    config = _cabled("c1-cable", "c1-cable")
    # Cores 1 and 2 of each binding's cable (both valid), so MB-1 holds two cable-core connections.
    config["patterns"][0]["nets"][0]["connections"].append(
        {"from": 0, "to": 1, "via": "cable_core", "core": 2, "label": None}
    )
    saved = _revise(client, project_id, m7a_support.request(), configuration=config)
    assert saved.status_code == 201, saved.text[:600]
    stored = copy.deepcopy(revision_row(engine, project_id, 3)["design_snapshot"])
    assert read.read_snapshot(stored).v2 is not None
    board_of = {n["id"]: n["board_id"] for n in stored["model"]["nets"]}
    mb = [
        c
        for c in stored["model"]["connections"]
        if c["core"] is not None and board_of[c["net_id"]] == "MB-1"
    ]
    assert sorted(c["core"]["index"] for c in mb) == [1, 2]
    next(c for c in mb if c["core"]["index"] == 2)["core"]["index"] = 1
    with pytest.raises(versions.SnapshotUnreadableError, match="one board's cable"):
        read.read_snapshot(stored)


def test_the_stored_reader_refuses_a_core_on_a_cable_its_board_lacks(
    client: TestClient, switch: Switch, engine: Engine
) -> None:
    """A stored SB-1 core pointed at MB-1's c2-cable (which SB-1 has none of) is refused."""
    project_id = _project(client, switch)
    saved = _revise(
        client, project_id, m7a_support.request(), configuration=_cabled("c1-cable", "c1-cable")
    )
    assert saved.status_code == 201, saved.text[:600]
    stored = copy.deepcopy(revision_row(engine, project_id, 3)["design_snapshot"])
    board_of = {n["id"]: n["board_id"] for n in stored["model"]["nets"]}
    sb = next(
        c
        for c in stored["model"]["connections"]
        if c["core"] is not None and board_of[c["net_id"]] == "SB-1"
    )
    sb["core"]["cable_id"] = "c2-cable"
    with pytest.raises(versions.SnapshotUnreadableError, match="no cable of the net's board"):
        read.read_snapshot(stored)


def test_reuse_with_the_same_cable_id_is_approved_by_another_member_in_its_tenant(
    client: TestClient, switch: Switch, caller: _Caller, tenants: tuple[str, str]
) -> None:
    project_id = _project(client, switch)
    saved = _revise(
        client, project_id, m7a_support.request(), configuration=_cabled("c1-cable", "c1-cable")
    )
    assert saved.status_code == 201, saved.text[:600]
    assert approve(client, project_id, 3).status_code == 200
    assert _opened(client, project_id, 3)["approval_current"] is True
    owner = caller.user
    caller.user = _user(tenants[1], email="intruder-cores@example.com")
    assert client.get(f"/design/projects/{project_id}", params={"revision": 3}).status_code == 404
    assert revision_export(client, project_id, 3, "connection_drawing_pdf").status_code == 404
    caller.user = owner

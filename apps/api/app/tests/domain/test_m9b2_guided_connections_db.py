"""M9b2 integration: connections edited in the guided editor (SYNTHETIC data).

Drives the real routes against the disposable Postgres (``TEST_DATABASE_URL``)
with the synthetic two-board example of M7a. Names, markings and core numbers
are illustrative only, not engineering values; the cable and its core count are
the synthetic design's own.

Contract ``docs/architecture/m9b2-guided-connections.md``: connections are the
composer's existing shape, checked by the server exactly as before; a refused
configuration saves nothing; wire details are keyed by a connection's position,
which is why the editor refuses a change that would move or drop them.
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
from app.tests.domain.test_m9a_guided_patterns_db import _configuration
from app.tests.domain.test_m9b1_binding_preview_db import _project, _rows, _validate


@pytest.fixture(autouse=True)
def _saved_by_another_member(monkeypatch: pytest.MonkeyPatch) -> None:
    review_support.author_revisions_as_another_member(monkeypatch)


def _c(
    start: int, end: int, via: str, core: int | None = None, label: str | None = None
) -> dict[str, Any]:
    return {"from": start, "to": end, "via": via, "core": core, "label": label}


def _edited(n1: list[dict[str, Any]], **binding: Any) -> dict[str, Any]:
    """The M9a pattern, bound, with net n1's connections as the guided editor writes them."""
    config = _configuration(bound=True)
    net = config["patterns"][0]["nets"][0]
    # A point's other side, so a point-sides (internal) connection has two ends of one point.
    net["members"].append({"point": "p1", "side": "outer"})
    net["connections"] = n1
    config["bindings"][0].update(binding)
    return config


def _cable(client: TestClient) -> tuple[str, int]:
    """A cable of MB-1 and its core count, as the design route states them (what the editor offers)."""
    response = client.post("/design/project", json=m7a_support.request())
    assert response.status_code == 200, response.text[:400]
    for board in response.json()["project"]["boards"]:
        if board["id"] == "MB-1" and board.get("cables"):
            cable = board["cables"][0]
            return cable["id"], int(cable["cores"])
    raise AssertionError("the synthetic design has no cable on MB-1")


ALL_KINDS = [
    _c(0, 1, "wire"),
    _c(1, 3, "internal"),
    _c(1, 2, "jumper"),
    _c(0, 2, "busbar"),
]


def test_each_connection_type_saves_reopens_and_matches_the_preview(
    client: TestClient, switch: Switch, engine: Engine
) -> None:
    project_id = _project(client, switch)
    cable, _cores = _cable(client)
    n1 = [*ALL_KINDS, _c(0, 2, "cable_core", 1, "BN")]
    config = _edited(n1, cable=cable)
    preview = _validate(client, project_id, config)
    assert preview.status_code == 200, preview.text[:600]
    saved = _revise(client, project_id, m7a_support.request(), configuration=config)
    assert saved.status_code == 201, saved.text[:600]
    opened = _opened(client, project_id, 3)
    # The saved configuration reopens exactly as written.
    assert opened["configuration"]["patterns"][0]["nets"][0]["connections"] == n1
    # Preview and saved model agree, and each connection keeps its ends and type.
    stored = opened["model_v2"]
    assert _rows(stored) == _rows(preview.json()["model"])
    by_id = {c["id"]: c for c in stored["connections"]}
    for index, connection in enumerate(n1):
        assert by_id[ids.derive("x", "bind-1", "n1", str(index))]["via"] == connection["via"]


def _same_end(config: dict[str, Any]) -> None:
    config["patterns"][0]["nets"][0]["connections"] = [_c(1, 1, "wire")]


def _end_outside(config: dict[str, Any]) -> None:
    config["patterns"][0]["nets"][0]["connections"] = [_c(0, 9, "wire")]


def _internal_two_points(config: dict[str, Any]) -> None:
    config["patterns"][0]["nets"][0]["connections"] = [_c(0, 1, "internal")]


def _core_on_wire(config: dict[str, Any]) -> None:
    config["patterns"][0]["nets"][0]["connections"] = [_c(0, 1, "wire", 2)]


def _label_on_jumper(config: dict[str, Any]) -> None:
    config["patterns"][0]["nets"][0]["connections"] = [_c(0, 1, "jumper", None, "L")]


def _unknown_via(config: dict[str, Any]) -> None:
    config["patterns"][0]["nets"][0]["connections"] = [_c(0, 1, "cable")]


def _no_core(config: dict[str, Any]) -> None:
    config["patterns"][0]["nets"][0]["connections"] = [_c(0, 1, "cable_core")]


def _no_cable(config: dict[str, Any]) -> None:
    config["patterns"][0]["nets"][0]["connections"] = [_c(0, 1, "cable_core", 1)]
    config["bindings"][0]["cable"] = None


def _unknown_cable(config: dict[str, Any]) -> None:
    config["patterns"][0]["nets"][0]["connections"] = [_c(0, 1, "cable_core", 1)]
    config["bindings"][0]["cable"] = "cable-not-on-board"


#: Each case and the refusal the server gives (the editor says the same beside the field first).
PATTERN_REFUSED: dict[str, tuple[Any, str]] = {
    "same_end": (_same_end, "pattern_invalid"),
    "end_outside": (_end_outside, "pattern_invalid"),
    "internal_two_points": (_internal_two_points, "pattern_invalid"),
    "core_on_wire": (_core_on_wire, "pattern_invalid"),
    "label_on_jumper": (_label_on_jumper, "pattern_invalid"),
    "unknown_via": (_unknown_via, "pattern_invalid"),
    "no_core": (_no_core, "pattern_invalid"),
    "no_cable": (_no_cable, "missing_binding"),
    "unknown_cable": (_unknown_cable, "unknown_reference"),
}


def _refused(client: TestClient, project_id: str, config: dict[str, Any]) -> list[dict[str, Any]]:
    bodies = []
    for response in (
        _validate(client, project_id, config),
        _revise(client, project_id, m7a_support.request(), configuration=config),
    ):
        assert response.status_code == 422, response.text[:400]
        bodies.append(response.json())
    assert _count(client, project_id) == 2
    return bodies


@pytest.mark.parametrize("case", sorted(PATTERN_REFUSED))
def test_an_invalid_connection_is_refused_by_preview_and_save_with_nothing_saved(
    client: TestClient, switch: Switch, case: str
) -> None:
    project_id = _project(client, switch)
    config = copy.deepcopy(_edited([_c(0, 1, "wire")]))
    mutate, code = PATTERN_REFUSED[case]
    mutate(config)
    for body in _refused(client, project_id, config):
        assert body["code"] == "configuration_refused", body
        assert body["params"]["refusal"] == code, body


def test_a_core_the_cable_lacks_or_a_reused_core_is_refused(
    client: TestClient, switch: Switch, engine: Engine
) -> None:
    project_id = _project(client, switch)
    cable, cores = _cable(client)
    beyond = _edited([_c(0, 1, "cable_core", cores + 1)], cable=cable)
    for body in _refused(client, project_id, beyond):
        assert body["params"]["refusal"] == "core_out_of_range", body
    reused = _edited([_c(0, 1, "cable_core", 1), _c(1, 2, "cable_core", 1)], cable=cable)
    for body in _refused(client, project_id, reused):
        assert body["params"]["refusal"] == "core_reused", body


W7 = {"number": {"value": "W7", "source": "user"}}


def _wire(model: dict[str, Any], key_index: int) -> dict[str, Any]:
    wid = ids.derive("w", "bind-1", "n1", str(key_index))
    return next(w for w in model["wires"] if w["wire_id"] == wid)


def test_wire_details_stay_with_their_connection_through_editor_changes(
    client: TestClient, switch: Switch
) -> None:
    project_id = _project(client, switch)
    first = _edited([_c(0, 1, "wire"), _c(1, 2, "wire")], wires={"n1/0": W7})
    assert (
        _revise(client, project_id, m7a_support.request(), configuration=first).status_code == 201
    )
    before = _wire(_opened(client, project_id, 3)["model_v2"], 0)
    # What the editor allows: a member added at the end, a connection added at the end,
    # and a later connection (with no details) removed.
    second = copy.deepcopy(first)
    net = second["patterns"][0]["nets"][0]
    net["members"].append({"point": "p2", "side": "outer"})
    second["patterns"][0]["nets"][0]["connections"] = [_c(0, 1, "wire"), _c(0, 2, "jumper")]
    assert (
        _revise(client, project_id, m7a_support.request(), configuration=second).status_code == 201
    )
    after = _opened(client, project_id, 4)
    assert after["configuration"]["bindings"][0]["wires"] == {"n1/0": W7}
    wire = _wire(after["model_v2"], 0)
    assert (wire["start"], wire["end"]) == (before["start"], before["end"])


def test_why_removal_is_refused_the_server_keys_details_by_position(
    client: TestClient, switch: Switch
) -> None:
    """Removing connection 0 while keeping key n1/0 would attach W7 to the former connection 1."""
    project_id = _project(client, switch)
    config = _edited([_c(0, 1, "wire"), _c(1, 2, "wire")], wires={"n1/0": W7})
    shown = _validate(client, project_id, config).json()["model"]
    original = _wire(shown, 0)
    shifted = copy.deepcopy(config)
    shifted["patterns"][0]["nets"][0]["connections"] = [_c(1, 2, "wire")]
    moved = _wire(_validate(client, project_id, shifted).json()["model"], 0)
    assert (moved["start"], moved["end"]) != (original["start"], original["end"])
    assert moved["number"]["value"] == "W7"


def test_changed_pattern_content_does_not_inherit_an_approval(
    client: TestClient, switch: Switch
) -> None:
    project_id = _project(client, switch)
    # Every member joined, so the revision has no blocking finding and can be approved.
    whole = [_c(0, 1, "wire"), _c(1, 3, "internal"), _c(1, 2, "wire")]
    config = _edited(whole)
    config["patterns"][0]["nets"][1]["connections"] = [_c(0, 1, "wire")]
    assert (
        _revise(client, project_id, m7a_support.request(), configuration=config).status_code == 201
    )
    assert approve(client, project_id, 3).status_code == 200
    changed = copy.deepcopy(config)
    changed["patterns"][0]["nets"][0]["connections"][2] = _c(1, 2, "jumper")
    assert (
        _revise(client, project_id, m7a_support.request(), configuration=changed).status_code == 201
    )
    # Revision 4 holds no approval: the approval stays with revision 3's snapshot.
    assert _opened(client, project_id, 4)["approval_current"] is not True
    assert _opened(client, project_id, 3)["approval_current"] is True

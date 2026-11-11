"""M7b integration: the configuration is stored with its revision and carried forward.

Drives the real routes against the disposable Postgres (``TEST_DATABASE_URL``)
with the SYNTHETIC two-board example of M7a (MB-1 feeds SB-1,
``app.tests.design.model.m7a_support``): its names and markings are
illustrative only, not engineering values. Each test sets
``DESIGN_SNAPSHOT_SCHEMA`` itself (unset means truly unset, never ``""``).

The invariants (contract ``docs/architecture/m7b-guided-configuration.md``):

* a configuration sent with a save is stored verbatim in that revision's
  requirements record (``configuration``), so its hash, and the approval
  binding, cover it; with none the record has no such key, as before;
* a plain save, an answer and a re-save carry the latest (re-save: that
  revision's) stored configuration, re-composed against the new request:
  the composed strips, components, connections and feeder-link terminals are
  exactly those of the configured revision;
* a carried reference that no longer resolves refuses the save
  (409 ``configuration_conflict``, specific params) and nothing is saved;
* ``configuration_removed`` is the one way to drop it;
* a revision configured before M7b (no stored configuration) cannot be
  carried: 409 ``configuration_not_recoverable``; re-save keeps
  ``configuration_not_carried``;
* another tenant sees and carries nothing.
"""

from __future__ import annotations

import copy
from collections.abc import Callable, Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.engine import Engine

from app.core.config import get_settings
from app.design import snapshot
from app.design.model.read import read_snapshot
from app.design.model.write import snapshot_v2_from_v1
from app.domain.design import build_design
from app.models.schemas.design import ProjectDesignRequest
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
    update_row,
)
from app.tests.design.model import m7a_support
from app.tests.domain import review_support

Switch = Callable[[str | None], None]

V1, V2, V3 = None, "pp-design/2", "pp-design/3"


@pytest.fixture(name="switch")
def _switch(monkeypatch: pytest.MonkeyPatch) -> Iterator[Switch]:
    """Set ``DESIGN_SNAPSHOT_SCHEMA`` as a deployment would; ``None`` unsets it entirely."""

    def set_to(schema: str | None) -> None:
        if schema is None:
            monkeypatch.delenv("DESIGN_SNAPSHOT_SCHEMA", raising=False)
        else:
            monkeypatch.setenv("DESIGN_SNAPSHOT_SCHEMA", schema)
        get_settings.cache_clear()

    yield set_to
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def _saved_by_another_member(monkeypatch: pytest.MonkeyPatch) -> None:
    """Revisions are saved by another member, so the caller may approve them (M6a)."""
    review_support.author_revisions_as_another_member(monkeypatch)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _points() -> dict[str, str]:
    """The strip point id the synthetic configuration gives each board (deterministic)."""
    designed, _ = build_design(ProjectDesignRequest.model_validate(m7a_support.request()))
    model = read_snapshot(
        snapshot_v2_from_v1(designed.model_dump(mode="json"), m7a_support.configuration())
    ).v2
    assert model is not None
    return {strip.board_id: strip.points[0].id for strip in model.strips}


def _links() -> list[dict[str, Any]]:
    points = _points()
    return [
        {
            "fed_board": "SB-1",
            "from_end": {"terminal": {"point": points["MB-1"], "side": "inner"}},
            "to_end": {"terminal": {"point": points["SB-1"], "side": "inner"}},
        }
    ]


def _request(schema: str | None, links: bool = True) -> dict[str, Any]:
    return m7a_support.request(_links() if schema == V3 and links else None)


def _new_project(client: TestClient, schema: str | None) -> str:
    response = client.post(
        "/design/projects",
        json={"name": "M7b synthetic", "request": m7a_support.request(), "note": "r1"},
    )
    assert response.status_code == 201, response.text[:500]
    project_id: str = response.json()["id"]
    return project_id


def _revise(client: TestClient, project_id: str, request: dict[str, Any], **extra: Any) -> Any:
    return client.post(
        f"/design/projects/{project_id}/revisions",
        json={"request": request, "note": "next", **extra},
    )


def _configured(client: TestClient, schema: str | None) -> str:
    """Revision 1 plain; revision 2 with the configuration (and v3 feeder-link terminals)."""
    project_id = _new_project(client, schema)
    response = _revise(
        client, project_id, _request(schema), configuration=m7a_support.configuration()
    )
    assert response.status_code == 201, response.text[:500]
    return project_id


def _opened(client: TestClient, project_id: str, number: int | None = None) -> dict[str, Any]:
    params = {"revision": number} if number is not None else {}
    response = client.get(f"/design/projects/{project_id}", params=params)
    assert response.status_code == 200, response.text[:500]
    body: dict[str, Any] = response.json()
    return body


def _composed(row: dict[str, Any]) -> dict[str, Any]:
    """What a configuration composed to: strips, components, connections, link terminals."""
    model = row["design_snapshot"]["model"]
    return {
        "strips": model["strips"],
        "components": model["components"],
        "connections": model["connections"],
        "nets": model["nets"],
        "patterns": model["patterns"],
        "link_terminals": [
            [(end["role"], end["terminal"]) for end in link["ends"]]
            for link in model.get("links", [])
        ],
    }


def _answer_fault_level(client: TestClient, project_id: str, number: int) -> Any:
    record = _opened(client, project_id, number)["requirements"]
    question = next(q for q in record["questions"] if q["field"] == "fault_level_ka")
    return client.post(
        f"/design/projects/{project_id}/revisions/{number}/questions/{question['id']}",
        json={"action": "answer", "value": "10"},
    )


def _count(client: TestClient, project_id: str) -> int:
    return len(_opened(client, project_id)["revisions"])


def _strip_configuration(engine: Engine, project_id: str, number: int) -> None:
    """Make revision ``number`` look saved before M7b: no stored configuration, rehashed."""
    record = dict(revision_row(engine, project_id, number)["requirements"])
    record.pop("configuration")
    update_row(
        engine,
        project_id,
        number,
        requirements=record,
        requirements_sha256=snapshot.sha256_of(record),
    )


# --------------------------------------------------------------------------- #
# Storage and carry-forward
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("schema", [V2, V3])
def test_the_configuration_is_stored_verbatim_and_carried_through_every_save_path(
    client: TestClient, engine: Engine, switch: Switch, schema: str | None
) -> None:
    switch(schema)
    project_id = _configured(client, schema)
    configured = revision_row(engine, project_id, 2)
    sent = m7a_support.configuration()
    assert configured["requirements"]["configuration"] == sent
    assert snapshot.sha256_of(configured["requirements"]) == configured["requirements_sha256"]
    expected = _composed(configured)
    assert expected["strips"]
    assert expected["connections"]
    if schema == V3:
        assert all(t is not None for ends in expected["link_terminals"] for _, t in ends)

    # 3: plain save, no configuration sent.
    plain = _revise(client, project_id, _request(schema))
    assert plain.status_code == 201, plain.text[:500]
    # 4: an answer.
    answered = _answer_fault_level(client, project_id, 3)
    assert answered.status_code == 201, answered.text[:500]
    # 5: a re-save of revision 4.
    resaved = client.post(f"/design/projects/{project_id}/revisions/4/resave")
    assert resaved.status_code == 201, resaved.text[:500]

    for number in (3, 4, 5):
        row = revision_row(engine, project_id, number)
        assert row["requirements"]["configuration"] == sent, number
        assert snapshot.sha256_of(row["requirements"]) == row["requirements_sha256"]
        assert _composed(row) == expected, number
        opened = _opened(client, project_id, number)
        assert opened["configuration"] == sent
        assert opened["configuration_status"] == "stored"
    assert revision_row(engine, project_id, 4)["request"]["boards"][0]["supply"][
        "fault_level_ka"
    ] in (
        "10",
        "10.0",
    )
    assert revision_row(engine, project_id, 5)["requirements"]["resaved_from"] == 4

    # Switching revisions opens each one's own configuration.
    first = _opened(client, project_id, 1)
    assert first["configuration"] is None
    assert first["configuration_status"] == "none"


def test_a_plain_save_carries_the_latest_revisions_configuration_not_an_older_one(
    client: TestClient, engine: Engine, switch: Switch
) -> None:
    switch(V2)
    project_id = _configured(client, V2)
    changed = m7a_support.configuration()
    changed["bindings"][0]["markings"] = {"l": "F9"}
    assert _revise(client, project_id, _request(V2), configuration=changed).status_code == 201
    assert _revise(client, project_id, _request(V2)).status_code == 201
    assert revision_row(engine, project_id, 4)["requirements"]["configuration"] == changed
    assert _composed(revision_row(engine, project_id, 4)) == _composed(
        revision_row(engine, project_id, 3)
    )
    # Revision 2 keeps its own, unchanged.
    assert _opened(client, project_id, 2)["configuration"] == m7a_support.configuration()


def test_explicit_removal_saves_without_a_configuration_and_stops_carrying_it(
    client: TestClient, engine: Engine, switch: Switch
) -> None:
    switch(V2)
    project_id = _configured(client, V2)
    refused = _revise(
        client,
        project_id,
        _request(V2),
        configuration=m7a_support.configuration(),
        configuration_removed=True,
    )
    assert refused.status_code == 422
    removed = _revise(
        client, project_id, _request(V2), configuration=None, configuration_removed=True
    )
    assert removed.status_code == 201, removed.text[:500]
    row = revision_row(engine, project_id, 3)
    assert "configuration" not in row["requirements"]
    assert row["design_snapshot"]["model"]["strips"] == []
    assert _opened(client, project_id, 3)["configuration_status"] == "none"
    assert _revise(client, project_id, _request(V2)).status_code == 201
    assert "configuration" not in revision_row(engine, project_id, 4)["requirements"]


# --------------------------------------------------------------------------- #
# Conflicts: nothing saved
# --------------------------------------------------------------------------- #


def test_a_carried_reference_to_a_board_gone_is_a_conflict_and_nothing_is_saved(
    client: TestClient, engine: Engine, switch: Switch
) -> None:
    switch(V2)
    project_id = _configured(client, V2)
    before = revision_row(engine, project_id, 2)
    one_board = m7a_support.request()
    one_board["boards"] = one_board["boards"][:1]
    conflict = _revise(client, project_id, one_board)
    assert conflict.status_code == 409, conflict.text[:500]
    body = conflict.json()
    assert body["code"] == "configuration_conflict"
    assert body["params"] == {
        "ref": "SB-1",
        "kind": "board",
        "strip": "XS",
        "reason": "unknown_reference",
    }
    # The same request with the configuration sent is the composer's 422, not a conflict.
    sent = _revise(client, project_id, one_board, configuration=m7a_support.configuration())
    assert sent.status_code == 422
    assert sent.json()["code"] == "configuration_refused"
    assert _count(client, project_id) == 2
    assert revision_row(engine, project_id, 2) == before


def test_a_carried_circuit_gone_names_the_binding(
    client: TestClient, engine: Engine, switch: Switch
) -> None:
    switch(V2)
    project_id = _configured(client, V2)
    # Every board keeps at least one load here, so a circuit cannot vanish
    # from the request; the stored configuration names one the design lacks.
    record = copy.deepcopy(revision_row(engine, project_id, 2)["requirements"])
    record["configuration"]["bindings"][1]["circuit"] = "c9"
    update_row(
        engine, project_id, 2, requirements=record, requirements_sha256=snapshot.sha256_of(record)
    )
    conflict = _revise(client, project_id, _request(V2))
    assert conflict.status_code == 409, conflict.text[:500]
    assert conflict.json()["code"] == "configuration_conflict"
    params = conflict.json()["params"]
    assert params["kind"] == "circuit"
    assert params["binding"] == "sb1-out"
    assert params["ref"] == "c9"
    assert _count(client, project_id) == 2


def test_an_answer_whose_carried_configuration_conflicts_saves_nothing(
    client: TestClient, engine: Engine, switch: Switch
) -> None:
    switch(V2)
    project_id = _configured(client, V2)
    # Make the configuration name a strip on a board the request lacks, then rehash
    # (a stored configuration is data; the composer must still refuse it on carry).
    row = revision_row(engine, project_id, 2)
    record = copy.deepcopy(row["requirements"])
    record["configuration"]["strips"][1]["board"] = "SB-9"
    update_row(
        engine, project_id, 2, requirements=record, requirements_sha256=snapshot.sha256_of(record)
    )
    response = _answer_fault_level(client, project_id, 2)
    assert response.status_code == 409, response.text[:500]
    assert response.json()["code"] == "configuration_conflict"
    assert response.json()["params"]["ref"] == "SB-9"
    assert _count(client, project_id) == 2


def test_a_carried_feeder_link_terminal_gone_is_a_conflict(
    client: TestClient, engine: Engine, switch: Switch
) -> None:
    switch(V3)
    project_id = _configured(client, V3)
    # The stored configuration loses MB-1's strip point (its binding), rehashed:
    # the request's feeder link still names that terminal.
    row = revision_row(engine, project_id, 2)
    record = copy.deepcopy(row["requirements"])
    record["configuration"]["bindings"] = record["configuration"]["bindings"][1:]
    update_row(
        engine, project_id, 2, requirements=record, requirements_sha256=snapshot.sha256_of(record)
    )
    response = _revise(client, project_id, _request(V3))
    assert response.status_code == 409, response.text[:500]
    assert response.json()["code"] == "configuration_conflict"
    assert response.json()["params"] == {
        "ref": "from end of SB-1",
        "kind": "terminal",
        "link": "SB-1",
        "reason": "link_terminal_unknown",
    }
    assert _count(client, project_id) == 2


# --------------------------------------------------------------------------- #
# Configured before M7b
# --------------------------------------------------------------------------- #


def test_a_pre_m7b_configured_revision_is_not_recoverable(
    client: TestClient, engine: Engine, switch: Switch
) -> None:
    switch(V2)
    project_id = _configured(client, V2)
    _strip_configuration(engine, project_id, 2)
    opened = _opened(client, project_id, 2)
    assert opened["configuration"] is None
    assert opened["configuration_status"] == "not_recoverable"

    plain = _revise(client, project_id, _request(V2))
    assert plain.status_code == 409, plain.text[:500]
    assert plain.json()["code"] == "configuration_not_recoverable"
    assert plain.json()["params"] == {"revision": "2"}
    answered = _answer_fault_level(client, project_id, 2)
    assert answered.status_code == 409
    assert answered.json()["code"] == "configuration_not_recoverable"
    resaved = client.post(f"/design/projects/{project_id}/revisions/2/resave")
    assert resaved.status_code == 409
    assert resaved.json()["params"]["reason"] == "configuration_not_carried"
    assert _count(client, project_id) == 2

    # Re-entered, or removed explicitly, it saves.
    again = _revise(client, project_id, _request(V2), configuration=m7a_support.configuration())
    assert again.status_code == 201
    assert revision_row(engine, project_id, 3)["requirements"]["configuration"] is not None


# --------------------------------------------------------------------------- #
# Approval binding, isolation, schema modes
# --------------------------------------------------------------------------- #


def test_the_requirements_hash_and_approval_cover_the_configuration(
    client: TestClient, engine: Engine, switch: Switch
) -> None:
    switch(V2)
    project_id = _configured(client, V2)
    removed = _revise(client, project_id, _request(V2), configuration_removed=True)
    assert removed.status_code == 201
    with_config = revision_row(engine, project_id, 2)
    without = revision_row(engine, project_id, 3)
    assert with_config["requirements_sha256"] != without["requirements_sha256"]
    changed = m7a_support.configuration()
    changed["bindings"][0]["markings"] = {"l": "F9"}
    assert _revise(client, project_id, _request(V2), configuration=changed).status_code == 201
    assert revision_row(engine, project_id, 4)["requirements_sha256"] not in {
        with_config["requirements_sha256"],
        without["requirements_sha256"],
    }

    assert approve(client, project_id, 2).status_code == 200
    assert _opened(client, project_id, 2)["approval_current"] is True
    # Altering the stored configuration outside PanelPilot breaks the binding.
    record = copy.deepcopy(with_config["requirements"])
    record["configuration"]["bindings"][0]["markings"] = {"l": "F7"}
    update_row(engine, project_id, 2, requirements=record)
    tampered = _opened(client, project_id, 2)
    assert tampered["approval_current"] is not True
    assert tampered["configuration"] is None


def test_another_tenant_sees_no_configuration_and_carries_none(
    client: TestClient, engine: Engine, caller: _Caller, tenants: tuple[str, str], switch: Switch
) -> None:
    switch(V2)
    project_id = _configured(client, V2)
    owner = caller.user
    caller.user = _user(tenants[1], email="intruder@example.com")
    assert client.get(f"/design/projects/{project_id}").status_code == 404
    assert _revise(client, project_id, _request(V2)).status_code == 404
    assert client.post(f"/design/projects/{project_id}/revisions/2/resave").status_code == 404
    own = _new_project(client, V2)
    assert _revise(client, own, _request(V2)).status_code == 201
    assert "configuration" not in revision_row(engine, own, 2)["requirements"]
    caller.user = owner
    assert _count(client, project_id) == 2


@pytest.mark.parametrize("schema", [V1, V2, V3])
def test_without_a_configuration_records_store_as_before(
    client: TestClient, engine: Engine, switch: Switch, schema: str | None
) -> None:
    switch(schema)
    project_id = _new_project(client, schema)
    # No configuration models no terminal, so no link names one.
    assert _revise(client, project_id, _request(schema, links=False)).status_code == 201
    answered = _answer_fault_level(client, project_id, 2)
    assert answered.status_code == 201, answered.text[:500]
    for number in (1, 2, 3):
        row = revision_row(engine, project_id, number)
        assert "configuration" not in row["requirements"]
        assert snapshot.sha256_of(row["requirements"]) == row["requirements_sha256"]
        opened = _opened(client, project_id, number)
        assert opened["configuration"] is None
        assert opened["configuration_status"] == "none"
        expected = {V1: "pp-design/1", V2: "pp-design/2", V3: "pp-design/3"}[schema]
        assert row["design_snapshot"].get("schema", "pp-design/1") == expected


def test_a_carried_configuration_under_a_v1_deployment_is_refused_not_dropped(
    client: TestClient, engine: Engine, switch: Switch
) -> None:
    switch(V2)
    project_id = _configured(client, V2)
    switch(V1)
    response = _revise(client, project_id, m7a_support.request())
    assert response.status_code == 409
    assert response.json()["code"] == "configuration_requires_v2"
    assert _count(client, project_id) == 2
    removed = _revise(client, project_id, m7a_support.request(), configuration_removed=True)
    assert removed.status_code == 201


def test_a_v2_configuration_is_carried_into_a_v3_deployment(
    client: TestClient, engine: Engine, switch: Switch
) -> None:
    switch(V2)
    project_id = _configured(client, V2)
    expected = _composed(revision_row(engine, project_id, 2))
    switch(V3)
    response = _revise(client, project_id, _request(V3))
    assert response.status_code == 201, response.text[:500]
    row = revision_row(engine, project_id, 3)
    assert row["design_snapshot"]["schema"] == "pp-design/3"
    got = _composed(row)
    assert {k: got[k] for k in ("strips", "components", "connections", "nets")} == {
        k: expected[k] for k in ("strips", "components", "connections", "nets")
    }
    assert all(t is not None for ends in got["link_terminals"] for _, t in ends)

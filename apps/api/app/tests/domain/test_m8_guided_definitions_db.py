"""M8: a component definition entered by the user, used as a feeder-link endpoint.

What the guided editor sends when the user defines a component and its
terminals without JSON: a ``tenant/<id>/v1`` template in
``configuration.templates`` (function and terminal kinds ``unspecified`` unless
chosen from data, requiredness not stated, no source, review ``unreviewed``), a
component on it, and a feeder link whose end names that component's terminal by
the ids the server derives. These tests pin that the existing composer, link
validation, renderers and immutability rules carry it end to end. SYNTHETIC data.
"""

from __future__ import annotations

import copy
import io
from typing import Any

import pdfplumber
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
    pytestmark,
    revision_row,
)
from app.tests.design.model import m7a_support
from app.tests.domain import review_support
from app.tests.domain.test_m7b_configuration_db import (  # noqa: F401  (fixtures)
    V3,
    Switch,
    _configured,
    _count,
    _new_project,
    _opened,
    _request,
    _revise,
    _switch,
)

REF = "tenant/synth-isolator/v1"
COMPONENT = "component-m8q9"


@pytest.fixture(autouse=True)
def _saved_by_another_member(monkeypatch: pytest.MonkeyPatch) -> None:
    review_support.author_revisions_as_another_member(monkeypatch)


def _definition(markings: tuple[str, ...] = ("L1", "L2")) -> dict[str, Any]:
    """A definition exactly as the editor writes it: nothing inferred."""
    return {
        "ref": REF,
        "function": "unspecified",
        "terminals": [
            {
                "id": f"t-{index:03d}",
                "marking": marking,
                "kind": "unspecified",
                "required": None,
                "group": None,
            }
            for index, marking in enumerate(markings, start=1)
        ],
        "contact_groups": [],
        "parameters": [],
        "provenance": {"source": None, "author": "user-entered", "created": "2026-11-15"},
        "review": {
            "status": "unreviewed",
            "reviewer": None,
            "reviewed_at": None,
            "standards": [],
            "scope_note": None,
        },
        "restricted_scope": [],
    }


def _configuration(definition: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "templates": [definition if definition is not None else _definition()],
        "patterns": [],
        "strips": [],
        "components": [
            {
                "id": COMPONENT,
                "board": "MB-1",
                "template": REF,
                "designation": {"function": None, "location": None, "product": "Q9"},
            }
        ],
        "bindings": [],
    }


def _link(terminal: str = "t-001") -> list[dict[str, Any]]:
    return [
        {
            "fed_board": "SB-1",
            "from_end": {
                "terminal": {"component": ids.derive("k", COMPONENT), "terminal": terminal}
            },
        }
    ]


def _export(client: TestClient, project_id: str, number: int, fmt: str) -> Any:
    return client.post(
        f"/design/projects/{project_id}/revisions/{number}/export",
        json={"format": fmt, "purpose": "review"},
    )


def _text(pdf: bytes) -> str:
    with pdfplumber.open(io.BytesIO(pdf)) as document:
        return "\n".join(page.extract_text() or "" for page in document.pages)


def _defined(client: TestClient, switch: Switch) -> str:
    switch(V3)
    project_id = _new_project(client, V3)
    saved = _revise(
        client, project_id, m7a_support.request(_link()), configuration=_configuration()
    )
    assert saved.status_code == 201, saved.text[:600]
    return project_id


def test_a_defined_component_terminal_is_a_feeder_link_end_in_one_save(
    client: TestClient, switch: Switch
) -> None:
    project_id = _defined(client, switch)
    opened = _opened(client, project_id, 2)
    # The definition is stored verbatim, flagged as it was entered.
    assert opened["configuration"]["templates"] == [_definition()]
    terminals = [
        row
        for row in opened["model_v2"]["terminals"]
        if row["owner_kind"] == "component" and row["owner_id"] == ids.derive("k", COMPONENT)
    ]
    assert [(row["marking"], row["board_id"]) for row in terminals] == [
        ("L1", "MB-1"),
        ("L2", "MB-1"),
    ]
    link = opened["links"][0]
    from_end = next(end for end in link["ends"] if end["role"] == "from")
    # The API also carries the strip half as nulls (as since M7a).
    assert {key: from_end["terminal"][key] for key in ("component", "terminal")} == {
        "component": ids.derive("k", COMPONENT),
        "terminal": "t-001",
    }
    assert from_end["resolution"] == "resolved"
    # The unreviewed definition is flagged, never treated as reviewed.
    codes = {finding["code"] for finding in opened["findings"]}
    assert "template_unreviewed" in codes
    # No connection, net or wire was made for it: the link is not board-local wiring.
    assert not any(
        member.get("component") == ids.derive("k", COMPONENT)
        for net in opened["model_v2"].get("nets", [])
        for member in net.get("members", [])
    )


def test_the_endpoint_reads_the_same_in_the_drawing_the_wire_schedule_and_the_json(
    client: TestClient, switch: Switch
) -> None:
    project_id = _defined(client, switch)
    drawing = _export(client, project_id, 2, "connection_drawing_pdf")
    assert drawing.status_code == 200, drawing.text[:400]
    text = _text(drawing.content)
    assert "terminal_link_end: MB-1/Q9:L1" in text
    assert "LINK END: no board-local wire (in no net); feeder link to SB-1" in text
    # Requiredness not stated: kept and said separately, never satisfied by the link.
    assert "UNCONNECTED: requiredness unresolved" in text
    schedule = _export(client, project_id, 2, "wire_schedule_pdf")
    assert schedule.status_code == 200, schedule.text[:400]
    assert "Q9:L1" in _text(schedule.content)
    exported = _export(client, project_id, 2, "json")
    assert exported.status_code == 200
    body = exported.json()
    ends = body["links"][0]["ends"]
    assert ("from", ids.derive("k", COMPONENT), "t-001") in [
        (
            end["role"],
            (end["terminal"] or {}).get("component"),
            (end["terminal"] or {}).get("terminal"),
        )
        for end in ends
    ]
    # Within its stated scope: the configuration itself is not in the JSON export.
    assert body["export_scope"]["restores_revision"] is False
    assert "configuration" not in body


def test_editing_the_definition_is_a_new_revision_and_the_old_one_is_unchanged(
    client: TestClient, switch: Switch, engine: Engine
) -> None:
    project_id = _defined(client, switch)
    before = revision_row(engine, project_id, 2)
    edited = _configuration(_definition(("L1A", "L2")))
    saved = _revise(client, project_id, m7a_support.request(_link()), configuration=edited)
    assert saved.status_code == 201, saved.text[:600]
    after = revision_row(engine, project_id, 2)
    for column in (
        "requirements",
        "requirements_sha256",
        "design_snapshot",
        "snapshot_sha256",
    ):
        assert after[column] == before[column], column
    assert (
        _opened(client, project_id, 3)["configuration"]["templates"][0]["terminals"][0]["marking"]
        == "L1A"
    )
    assert (
        _opened(client, project_id, 2)["configuration"]["templates"][0]["terminals"][0]["marking"]
        == "L1"
    )


def test_a_definition_removed_while_a_component_uses_it_saves_nothing(
    client: TestClient, switch: Switch
) -> None:
    project_id = _defined(client, switch)
    broken = copy.deepcopy(_configuration())
    broken["templates"] = []
    refused = _revise(client, project_id, m7a_support.request(_link()), configuration=broken)
    assert refused.status_code == 422, refused.text[:400]
    body = refused.json()
    assert body["code"] == "configuration_refused"
    assert body["params"]["component"] == COMPONENT
    assert body["params"]["ref"] == REF
    assert _count(client, project_id) == 2


def test_a_terminal_removed_while_a_link_uses_it_saves_nothing(
    client: TestClient, switch: Switch
) -> None:
    project_id = _defined(client, switch)
    # The editor keeps terminal ids, so removing L1 leaves only t-002 (L2).
    one = _configuration()
    one["templates"][0]["terminals"] = [_definition()["terminals"][1]]
    refused = _revise(client, project_id, m7a_support.request(_link("t-001")), configuration=one)
    assert refused.status_code in (409, 422), refused.text[:400]
    assert _count(client, project_id) == 2


def test_a_binding_removed_while_a_link_names_its_point_saves_nothing(
    client: TestClient, switch: Switch
) -> None:
    # The browser no longer offers a removed binding's points, and keeps a chosen
    # one visible as gone; the server still refuses it if it is sent.
    switch(V3)
    project_id = _configured(client, V3)
    without = copy.deepcopy(m7a_support.configuration())
    without["bindings"] = without["bindings"][1:]  # MB-1's binding (its strip point) removed
    refused = _revise(client, project_id, _request(V3), configuration=without)
    assert refused.status_code in (409, 422), refused.text[:400]
    assert "link_terminal_unknown" in refused.text
    assert _count(client, project_id) == 2


def test_another_tenant_cannot_open_or_carry_the_definition(
    client: TestClient, switch: Switch, caller: _Caller, tenants: tuple[str, str]
) -> None:
    project_id = _defined(client, switch)
    owner = caller.user
    caller.user = _user(tenants[1], email="intruder@example.com")
    assert client.get(f"/design/projects/{project_id}").status_code == 404
    assert _revise(client, project_id, m7a_support.request(_link())).status_code == 404
    caller.user = owner
    assert _count(client, project_id) == 2

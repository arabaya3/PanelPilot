"""M4 consistency through the HTTP export: one revision, one snapshot, three documents.

The connection drawing and both review schedules of a configured
``pp-design/2`` revision carry the same snapshot hash, the revision row's,
and nothing else; whatever refuses one of them refuses all three (an altered
row, a stale approval, purpose execution, a ``pp-design/1`` revision).

Synthetic data only: the configuration is committed test data. The snapshot
schema switch is set inside each test.
"""

from __future__ import annotations

import copy
import re
from collections.abc import Callable
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.engine import Engine

from app import design
from app.design import snapshot
from app.design.model import versions
from app.tests.api.v1.routes.test_export_integrity import (  # noqa: F401  (fixtures)
    _account_row,
    _app,
    _Caller,
    _caller,
    _client,
    _engine,
    _tenants,
    _user,
    approve,
    error,
    pdf_pages,
    pytestmark,
    revision_export,
    revision_row,
    update_row,
)
from app.tests.design.model.compat_support import DESIGNED
from app.tests.design.model.v2_support import CONFIGS, config_case

CASE = "pe_n_distribution"
FORMATS = ("connection_drawing_pdf", "terminal_schedule_pdf", "wire_schedule_pdf")
_HASH = re.compile(r"\b[0-9a-f]{64}\b")

Switch = Callable[[str], None]


@pytest.fixture(name="switch")
def _switch(monkeypatch: pytest.MonkeyPatch) -> Switch:
    def set_to(schema: str) -> None:
        monkeypatch.setenv("DESIGN_SNAPSHOT_SCHEMA", schema)

    return set_to


def _request() -> dict[str, Any]:
    request: dict[str, Any] = copy.deepcopy(DESIGNED[CONFIGS[CASE]["fixture"]]["request"])
    return request


def _project(client: TestClient) -> str:
    response = client.post(
        "/design/projects", json={"name": f"M4 {CASE}", "request": _request(), "note": "r1"}
    )
    assert response.status_code == 201, response.text[:500]
    project_id: str = response.json()["id"]
    return project_id


def _configured(client: TestClient, switch: Switch) -> str:
    """A project whose revision 2 is the configured ``pp-design/2`` one."""
    switch("pp-design/2")
    project_id = _project(client)
    revised = client.post(
        f"/design/projects/{project_id}/revisions",
        json={"request": _request(), "note": "configured", "configuration": config_case(CASE)[1]},
    )
    assert revised.status_code == 201, revised.text[:500]
    return project_id


def _texts(content: bytes) -> list[str]:
    return [" ".join(page.split()) for page in pdf_pages(content)]


@pytest.mark.parametrize("approved", [False, True])
def test_the_drawing_and_both_schedules_carry_the_revisions_snapshot_hash(
    client: TestClient, engine: Engine, switch: Switch, approved: bool
) -> None:
    project_id = _configured(client, switch)
    if approved:
        assert approve(client, project_id, 2).status_code == 200
    row = revision_row(engine, project_id, 2)
    digest = row["snapshot_sha256"]
    assert digest == snapshot.sha256_of(row["design_snapshot"])
    for fmt in FORMATS:
        exported = revision_export(client, project_id, 2, fmt)
        assert exported.status_code == 200, (fmt, exported.text[:300])
        assert exported.headers["content-type"] == "application/pdf"
        pages = _texts(exported.content)
        text = " ".join(pages)
        # The one hash, and no other, in each document.
        assert set(_HASH.findall(text)) == {digest}, fmt
        assert design.renderer_version(versions.V2) in text
        if fmt == "connection_drawing_pdf":
            for number, page in enumerate(pages, start=1):
                assert f"Snapshot SHA-256: {digest}" in page
                assert f"Sheet {number} of {len(pages)}" in page
                assert "NOT FOR EXECUTION" in page
        else:
            assert f"Design hash (SHA-256): {digest}" in text
            for page in pages:
                assert "NOT FOR EXECUTION" in page


def _wire_value(stored: dict[str, Any]) -> None:
    stored["model"]["wires"][0]["colour"] = {"value": "X", "source": "user", "assumption": None}


def _net_member_dropped(stored: dict[str, Any]) -> None:
    net = next(n for n in stored["model"]["nets"] if len(n["members"]) > 2)
    net["members"] = net["members"][:-1]


def _refusals(client: TestClient, project_id: str, **extra: Any) -> set[tuple[int, str]]:
    found: set[tuple[int, str]] = set()
    for fmt in FORMATS:
        response = revision_export(client, project_id, 2, fmt, **extra)
        found.add((response.status_code, error(response)["code"]))
    return found


@pytest.mark.parametrize("alter", [_wire_value, _net_member_dropped])
@pytest.mark.parametrize("rehash", [True, False])
def test_a_tampered_row_refuses_all_three(
    client: TestClient,
    engine: Engine,
    switch: Switch,
    alter: Callable[[dict[str, Any]], None],
    rehash: bool,
) -> None:
    project_id = _configured(client, switch)
    assert approve(client, project_id, 2).status_code == 200
    stored = copy.deepcopy(revision_row(engine, project_id, 2)["design_snapshot"])
    alter(stored)
    values: dict[str, Any] = {"design_snapshot": stored}
    if rehash:
        values["snapshot_sha256"] = snapshot.sha256_of(stored)
    update_row(engine, project_id, 2, **values)
    refused = _refusals(client, project_id)
    assert len(refused) == 1, refused
    assert next(iter(refused))[0] == 409


def test_a_stale_revision_refuses_all_three(
    client: TestClient, engine: Engine, switch: Switch
) -> None:
    project_id = _configured(client, switch)
    assert approve(client, project_id, 2).status_code == 200
    # As an approval recorded under another renderer version would read.
    update_row(engine, project_id, 2, approved_renderer_version=design.RENDERER_VERSION)
    assert _refusals(client, project_id) == {(409, "revision_stale")}


def test_purpose_execution_refuses_all_three(
    client: TestClient, engine: Engine, switch: Switch
) -> None:
    project_id = _configured(client, switch)
    assert _refusals(client, project_id, purpose="execution") == {
        (409, "execution_package_incomplete")
    }
    assert approve(client, project_id, 2).status_code == 200
    assert _refusals(client, project_id, purpose="execution") == {
        (409, "execution_package_incomplete")
    }


def test_a_pp_design_1_revision_refuses_all_three(
    client: TestClient, engine: Engine, switch: Switch
) -> None:
    switch("pp-design/1")
    project_id = _project(client)
    plain = client.post(
        f"/design/projects/{project_id}/revisions", json={"request": _request(), "note": "v1"}
    )
    assert plain.status_code == 201
    assert "schema" not in revision_row(engine, project_id, 2)["design_snapshot"]
    assert _refusals(client, project_id) == {(409, "schedule_requires_v2")}

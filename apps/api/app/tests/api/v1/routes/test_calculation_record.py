"""The voltage-drop calculation record through the real API and database (ADR 0004).

Saved atomically with its revision, bound to its snapshot, checked on every
read, bound by an approval, invisible to another tenant, and absent (not
derived) for a revision saved before records existed, whose approval still
counts. Synthetic data only.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.engine import Engine

from app.design import snapshot
from app.tests.api.v1.routes.test_export_integrity import (  # noqa: F401 - fixtures
    _app,
    _Caller,
    _caller,
    _client,
    _engine,
    _tenants,
    _user,
    approve,
    revision_export,
    revision_row,
    save,
    update_row,
)
from app.tests.domain import review_support

pytestmark = pytest.mark.skipif(
    not __import__("os").environ.get("TEST_DATABASE_URL", "").startswith("postgresql"),
    reason="needs Postgres: records, approval and tenancy are the database's",
)

#: A motor the earth-fault check enlarges (4 kW DOL, 60 m, Ze 0.35 ohm:
#: 1.5 -> 4 mm²), and a data circuit nothing enlarges.
REQUEST: dict[str, Any] = {
    "info": {"name": "SYNTHETIC record", "number": "REC-1"},
    "boards": [
        {
            "name": "MDB",
            "supply": {"earthing": "TN-S", "earth_loop_ohm": "0.35"},
            "loads": [
                {
                    "description": "Fan",
                    "load": "motor",
                    "power_kw": "4",
                    "phases": 3,
                    "starter": "dol",
                    "length_m": "60",
                },
                {"description": "Rack", "load": "data", "power_kw": "1", "length_m": "20"},
            ],
        }
    ],
}


@pytest.fixture(autouse=True)
def _another_author(monkeypatch: pytest.MonkeyPatch) -> None:
    # The caller approves; revisions are saved as another member of the tenant.
    review_support.author_revisions_as_another_member(monkeypatch)


def _record(client: TestClient, project_id: str, number: int = 1) -> Any:
    return client.get(f"/design/projects/{project_id}/revisions/{number}/calculation-record")


def _opened(client: TestClient, project_id: str, number: int = 1) -> dict[str, Any]:
    response = client.get(f"/design/projects/{project_id}", params={"revision": number})
    assert response.status_code == 200, response.text
    opened: dict[str, Any] = response.json()
    return opened


def test_a_saved_revision_stores_a_record_bound_to_it(client: TestClient, engine: Engine) -> None:
    saved = save(client, REQUEST)
    row = revision_row(engine, saved["id"], 1)
    record = row["calculation_record"]
    assert record["schema"] == "pp-vdrec/1"
    assert row["calculation_record_sha256"] == snapshot.sha256_of(record)
    assert record["snapshot_sha256"] == row["snapshot_sha256"]
    assert record["profile_sha256"] == row["profile_sha256"]
    assert record["rules_version"] == row["rules_version"]
    # The record's final drops are the saved design's.
    circuits = {c["description"]: c for c in record["boards"][0]["circuits"]}
    snapshot_circuits = _snapshot_circuits(row["design_snapshot"])
    assert circuits["Fan"]["saved_voltage_drop_percent"] == "1.23"
    assert circuits["Fan"]["running"]["final"]["section_mm2"] == "4"
    assert Decimal(snapshot_circuits["Fan"]) == Decimal("1.23")
    assert circuits["Rack"]["saved_voltage_drop_percent"] == "0.75"
    assert Decimal(snapshot_circuits["Rack"]) == Decimal("0.75")

    first = _record(client, saved["id"])
    assert first.status_code == 200, first.text
    body = first.json()
    assert body["status"] == "recorded"
    assert body["sha256"] == row["calculation_record_sha256"]
    assert body["record"] == record
    # Reopening reads the same record and design, and changes nothing stored.
    opened = _opened(client, saved["id"])
    assert opened["snapshot_sha256"] == row["snapshot_sha256"]
    assert _record(client, saved["id"]).json() == body
    after = revision_row(engine, saved["id"], 1)
    assert after["calculation_record"] == record
    assert after["calculation_record_sha256"] == row["calculation_record_sha256"]
    assert after["design_snapshot"] == row["design_snapshot"]


def _snapshot_circuits(stored: dict[str, Any]) -> dict[str, str]:
    """Each circuit's stored drop, by description, from a v1, v2 or v3 snapshot."""
    from app.design.model.read import read_snapshot

    project = read_snapshot(stored).project
    return {
        c.description: str(c.voltage_drop_percent)
        for b in project.boards
        for c in b.circuits
        if c.voltage_drop_percent is not None
    }


def test_a_failed_save_stores_neither_revision_nor_record(
    client: TestClient, engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.domain import design_projects

    def broken(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("synthetic failure while building the record")

    monkeypatch.setattr(design_projects.vd_record, "build", broken)
    response = client.post(
        "/design/projects", json={"name": "ATOMIC", "request": REQUEST, "note": "rev 1"}
    )
    assert response.status_code == 500
    with engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM design_projects")).scalar_one() == 0
        assert conn.execute(text("SELECT count(*) FROM design_project_revisions")).scalar_one() == 0


def test_an_approval_binds_the_record_it_was_granted_with(
    client: TestClient, engine: Engine
) -> None:
    saved = save(client, REQUEST)
    assert approve(client, saved["id"], 1).status_code == 200
    row = revision_row(engine, saved["id"], 1)
    assert row["approved_calculation_record_sha256"] == row["calculation_record_sha256"]
    opened = _opened(client, saved["id"])
    assert opened["approval_current"] is True
    assert "calculation_record" not in opened["approval_changed"]


def test_an_edited_record_is_refused_as_altered(client: TestClient, engine: Engine) -> None:
    saved = save(client, REQUEST)
    row = revision_row(engine, saved["id"], 1)
    edited = dict(row["calculation_record"])
    edited["statement"] = "verified by an engineer"
    update_row(engine, saved["id"], 1, calculation_record=edited)
    refused = _record(client, saved["id"])
    assert refused.status_code == 409
    assert refused.json()["params"]["reason"] == "calculation_record_mismatch"
    approval = approve(client, saved["id"], 1)
    assert approval.status_code == 409
    assert approval.json()["code"] == "snapshot_integrity_failed"
    exported = revision_export(client, saved["id"], 1, "calculations_pdf")
    assert exported.status_code == 409
    assert exported.json()["params"]["reason"] == "calculation_record_mismatch"


def test_a_record_rehashed_from_another_revision_is_refused(
    client: TestClient, engine: Engine
) -> None:
    first = save(client, REQUEST)
    other_request = {**REQUEST, "info": {"name": "SYNTHETIC other", "number": "REC-2"}}
    other_request["boards"] = [
        {**REQUEST["boards"][0], "loads": [REQUEST["boards"][0]["loads"][1]]}
    ]
    second = save(client, other_request)
    moved = revision_row(engine, second["id"], 1)["calculation_record"]
    update_row(
        engine,
        first["id"],
        1,
        calculation_record=moved,
        calculation_record_sha256=snapshot.sha256_of(moved),
    )
    refused = _record(client, first["id"])
    assert refused.status_code == 409
    assert refused.json()["params"]["reason"] == "calculation_record_mismatch"


def test_a_record_changed_after_approval_stops_the_approval_counting(
    client: TestClient, engine: Engine
) -> None:
    saved = save(client, REQUEST)
    assert approve(client, saved["id"], 1).status_code == 200
    row = revision_row(engine, saved["id"], 1)
    # An edit rehashed to match: intact on its own, but not what was approved.
    edited = dict(row["calculation_record"])
    edited["not_modelled"] = []
    update_row(
        engine,
        saved["id"],
        1,
        calculation_record=edited,
        calculation_record_sha256=snapshot.sha256_of(edited),
    )
    opened = _opened(client, saved["id"])
    assert opened["approval_current"] is False
    assert "calculation_record" in opened["approval_changed"]


def test_a_revision_saved_before_records_reads_not_recorded_and_keeps_its_approval(
    client: TestClient, engine: Engine
) -> None:
    saved = save(client, REQUEST)
    assert approve(client, saved["id"], 1).status_code == 200
    # As a revision saved and approved before this change: no record, no binding.
    update_row(
        engine,
        saved["id"],
        1,
        calculation_record=None,
        calculation_record_sha256=None,
        approved_calculation_record_sha256=None,
    )
    before = revision_row(engine, saved["id"], 1)
    response = _record(client, saved["id"])
    assert response.status_code == 200, response.text
    assert response.json() == {
        "revision": 1,
        "status": "not_recorded",
        "sha256": None,
        "record": None,
    }
    opened = _opened(client, saved["id"])
    assert opened["approval_current"] is True
    assert opened["approval_changed"] == []
    exported = revision_export(client, saved["id"], 1, "calculations_pdf")
    assert exported.status_code == 200, exported.text
    # Nothing was derived or written back.
    after = revision_row(engine, saved["id"], 1)
    for column in (
        "calculation_record",
        "calculation_record_sha256",
        "approved_calculation_record_sha256",
        "design_snapshot",
        "snapshot_sha256",
        "findings",
        "approved_at",
    ):
        assert after[column] == before[column], column


def test_an_unbound_approval_on_a_revision_with_a_record_does_not_count(
    client: TestClient, engine: Engine
) -> None:
    saved = save(client, REQUEST)
    assert approve(client, saved["id"], 1).status_code == 200
    update_row(engine, saved["id"], 1, approved_calculation_record_sha256=None)
    opened = _opened(client, saved["id"])
    assert opened["approval_current"] is False
    assert "calculation_record" in opened["approval_changed"]


def test_a_refused_design_has_no_record(client: TestClient, engine: Engine) -> None:
    refused_request = {
        "info": {"name": "SYNTHETIC refused"},
        "boards": [
            {
                "name": "DB",
                "supply": {"phases": 1, "voltage_v": 230},
                "loads": [{"description": "Big", "load": "motor", "power_kw": "4", "phases": 3}],
            }
        ],
    }
    saved = save(client, refused_request)
    row = revision_row(engine, saved["id"], 1)
    assert row["design_snapshot"] is None
    assert row["calculation_record"] is None
    assert _record(client, saved["id"]).json()["status"] == "not_recorded"


def test_another_tenant_cannot_read_the_record(
    client: TestClient, caller: _Caller, tenants: tuple[str, str]
) -> None:
    saved = save(client, REQUEST)
    caller.user = _user(tenants[1], email="eng-b@example.com")
    response = _record(client, saved["id"])
    assert response.status_code == 404
    assert response.json()["code"] == "revision_not_found"

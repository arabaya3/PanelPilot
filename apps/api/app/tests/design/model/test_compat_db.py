"""M1 compatibility through the API and the database: legacy rows, unknown schemas, extra fields.

Drives the real routes through FastAPI's TestClient against a disposable
Postgres (``TEST_DATABASE_URL``, a ``panelpilot_phase1_*`` database locally).
Skipped where no database is configured, with the repository's "needs
Postgres" reason, which CI's integration job refuses to skip.

* A v1 row saved by the Phase 1 code (no schema marker), and each committed
  fixture written into a row as stored, still opens, approves and exports.
* A snapshot with an unknown schema marker and a valid hash is a 409
  ``snapshot_schema_unknown`` naming the schema on export and approval, and
  opens as not validated with that finding: never read as v1.
* A snapshot holding a field the schema does not define, rehashed so the
  hash passes, is a structured 409 refusal, never accepted and never a 500.
"""

from __future__ import annotations

import copy
import json
import os
import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.api import deps
from app.api.v1.routes import design as design_route
from app.core.db import get_session
from app.core.errors import install_exception_handlers
from app.design import CATALOGUE_VERSION, RULES_VERSION, snapshot
from app.models.schemas.auth import CurrentUser, Role
from app.models.tables.tenant import TenantRow
from app.tests.design.model.compat_support import DESIGNED

DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL.startswith("postgresql"),
    reason="needs Postgres: stored snapshots, approval and export are the database's",
)

TENANT_SLUG = "m1-compat"
APPROVER_NAME = "Eng. Synthetic Approver"
UNKNOWN = "pp-design/999"

#: The committed fixtures written straight into a revision row.
ROW_CASES = ("residential", "multi_board", "motors", "plc", "arabic", "hebrew", "decimal_edges")


@pytest.fixture(scope="module", name="engine")
def _engine() -> Iterator[Engine]:
    in_ci = os.environ.get("GITHUB_ACTIONS") == "true"
    if not in_ci and not DATABASE_URL.rsplit("/", 1)[-1].startswith("panelpilot_phase1_"):
        pytest.fail(f"refusing to run against a non-disposable database: {DATABASE_URL}")
    engine = create_engine(DATABASE_URL)
    yield engine
    engine.dispose()


@pytest.fixture(name="tenant")
def _tenant(engine: Engine) -> str:
    with engine.begin() as conn:
        found = conn.execute(
            text("SELECT id FROM tenants WHERE slug = :s"), {"s": TENANT_SLUG}
        ).scalar_one_or_none()
        if found is None:
            with Session(bind=conn) as orm:
                row = TenantRow(slug=TENANT_SLUG, name=TENANT_SLUG)
                orm.add(row)
                orm.flush()
                found = row.id
        # Only this suite's tenant's rows: other suites' data is left alone.
        conn.execute(text("DELETE FROM design_projects WHERE tenant_id = :t"), {"t": found})
        conn.execute(text("DELETE FROM company_settings WHERE tenant_id = :t"), {"t": found})
    return str(found)


@pytest.fixture(name="client")
def _client(engine: Engine, tenant: str) -> Iterator[TestClient]:
    factory = sessionmaker(bind=engine)

    def session() -> Iterator[Session]:
        opened = factory()
        try:
            yield opened
        finally:
            opened.close()

    user = CurrentUser(
        id=str(uuid.uuid4()),
        email="m1-compat@example.com",
        tenant_id=tenant,
        roles=frozenset({Role.ENGINEER}),
    )
    # Approval is the owner's or a reviewer's: give the caller a users row, as a
    # login would, and make it the tenant's owner.
    with engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE tenants SET owner_user_id = NULL WHERE owner_user_id IN "
                "(SELECT id FROM users WHERE email = :e)"
            ),
            {"e": user.email},
        )
        conn.execute(text("DELETE FROM users WHERE email = :e"), {"e": user.email})
        conn.execute(
            text(
                "INSERT INTO users (id, tenant_id, email, full_name, is_active, "
                "created_at, updated_at) VALUES (:i, :t, :e, :n, true, now(), now())"
            ),
            {
                "i": uuid.UUID(user.id),
                "t": uuid.UUID(tenant),
                "e": user.email,
                "n": APPROVER_NAME,
            },
        )
        conn.execute(
            text("UPDATE tenants SET owner_user_id = :u WHERE id = :t"),
            {"u": uuid.UUID(user.id), "t": uuid.UUID(tenant)},
        )
    app = FastAPI()
    app.include_router(design_route.router, prefix="/design")
    app.dependency_overrides[deps.get_current_user] = lambda: user
    app.dependency_overrides[get_session] = session
    app.dependency_overrides[deps.enforce_design_rate_limit] = lambda: None
    install_exception_handlers(app)
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _save(client: TestClient, request: dict[str, Any]) -> str:
    response = client.post(
        "/design/projects", json={"name": "M1 compat", "request": request, "note": "rev 1"}
    )
    assert response.status_code == 201, response.text
    project_id: str = response.json()["id"]
    return project_id


def _row(engine: Engine, project_id: str) -> dict[str, Any]:
    with engine.connect() as conn:
        found = (
            conn.execute(
                text("SELECT * FROM design_project_revisions WHERE project_id = :p AND number = 1"),
                {"p": uuid.UUID(project_id)},
            )
            .mappings()
            .one()
        )
    return dict(found)


def _write_snapshot(engine: Engine, project_id: str, stored: Any, sha256: str) -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE design_project_revisions SET design_snapshot = CAST(:s AS JSONB), "
                "snapshot_sha256 = :h WHERE project_id = :p AND number = 1"
            ),
            {"s": json.dumps(stored), "h": sha256, "p": uuid.UUID(project_id)},
        )


def _open(client: TestClient, project_id: str) -> Any:
    return client.get(f"/design/projects/{project_id}", params={"revision": 1})


def _approve(client: TestClient, project_id: str) -> Any:
    # The approver is the signed-in account; the body names no one.
    return client.post(f"/design/projects/{project_id}/revisions/1/approval", json={})


def _export(client: TestClient, project_id: str, fmt: str = "pdf") -> Any:
    return client.post(f"/design/projects/{project_id}/revisions/1/export", json={"format": fmt})


def _refusal(response: Any, status: int, code: str) -> dict[str, Any]:
    assert response.status_code == status, response.text[:500]
    assert response.headers["content-type"].startswith("application/json")
    body: dict[str, Any] = response.json()
    assert body["code"] == code, body
    return body


def _summary(opened: Any) -> dict[str, Any]:
    assert opened.status_code == 200, opened.text[:500]
    body: dict[str, Any] = opened.json()
    return body


# --------------------------------------------------------------------------- #
# Legacy v1 rows
# --------------------------------------------------------------------------- #


def test_a_row_saved_by_phase1_code_opens_approves_and_exports(
    client: TestClient, engine: Engine
) -> None:
    project_id = _save(client, DESIGNED["residential"]["request"])
    row = _row(engine, project_id)
    assert "schema" not in row["design_snapshot"], "M1 writes v1 bare, as Phase 1 did"
    assert snapshot.sha256_of(row["design_snapshot"]) == row["snapshot_sha256"]
    opened = _summary(_open(client, project_id))
    assert opened["validated"] is True
    assert opened["export_status"] == "draft"
    draft = _export(client, project_id, "json")
    assert draft.status_code == 200, draft.text[:500]
    assert _approve(client, project_id).status_code == 200
    approved = _export(client, project_id, "pdf")
    assert approved.status_code == 200, approved.text[:500]
    assert approved.content.startswith(b"%PDF")
    # Reading never rewrote the row.
    assert _row(engine, project_id)["design_snapshot"] == row["design_snapshot"]


@pytest.mark.parametrize("case", ROW_CASES)
def test_a_committed_fixture_row_exports_and_approves(
    client: TestClient, engine: Engine, case: str
) -> None:
    """The stored bytes of each fixture, as a row, pass every gate the read path guards."""
    body = DESIGNED[case]
    stored = body["stored"]
    if (stored["rules_version"], stored["catalogue_version"]) != (
        RULES_VERSION,
        CATALOGUE_VERSION,
    ):
        pytest.skip("fixture predates the current rules: regenerate fixtures/generate.py")
    project_id = _save(client, body["request"])
    _write_snapshot(
        engine, project_id, copy.deepcopy(stored["design_snapshot"]), stored["snapshot_sha256"]
    )
    exported = _export(client, project_id, "json")
    assert exported.status_code == 200, exported.text[:500]
    approved = _approve(client, project_id)
    # A fixture augmented beyond what the pipeline emits re-runs to other
    # findings, so it may be stale or blocked; it is never unreadable.
    if body["source"] == "pipeline":
        assert approved.status_code == 200, approved.text[:500]
    else:
        assert approved.status_code in (200, 409), approved.text[:500]
        assert approved.json().get("code") not in (
            "snapshot_integrity_failed",
            "snapshot_schema_unknown",
        )


# --------------------------------------------------------------------------- #
# Unknown schema marker
# --------------------------------------------------------------------------- #


@pytest.fixture(name="unknown_schema")
def _unknown_schema(client: TestClient, engine: Engine) -> str:
    project_id = _save(client, DESIGNED["residential"]["request"])
    row = _row(engine, project_id)
    enveloped = {"schema": UNKNOWN, "model": row["design_snapshot"]}
    # Rehashed, so the refusal is the schema's and not the integrity check's.
    _write_snapshot(engine, project_id, enveloped, snapshot.sha256_of(enveloped))
    return project_id


@pytest.mark.parametrize("fmt", ["pdf", "json", "dxf"])
def test_unknown_schema_export_is_409_naming_the_schema(
    client: TestClient, unknown_schema: str, fmt: str
) -> None:
    body = _refusal(_export(client, unknown_schema, fmt), 409, "snapshot_schema_unknown")
    assert body["params"]["schema"] == UNKNOWN


def test_unknown_schema_approval_is_409_naming_the_schema(
    client: TestClient, engine: Engine, unknown_schema: str
) -> None:
    body = _refusal(_approve(client, unknown_schema), 409, "snapshot_schema_unknown")
    assert body["params"]["schema"] == UNKNOWN
    assert _row(engine, unknown_schema)["approved_at"] is None


def test_unknown_schema_opens_as_not_validated_with_the_finding(
    client: TestClient, unknown_schema: str
) -> None:
    """Opening shows the entered work, never as a valid design read as v1."""
    opened = _summary(_open(client, unknown_schema))
    # ``validated`` says only that a snapshot was stored on save (Phase 1's
    # meaning, as for an altered one); the export status is what gates.
    assert opened["export_status"] == "not_validated"
    found = [f for f in opened["findings"] if f["code"] == "snapshot_schema_unknown"]
    assert found, opened["findings"]
    assert found[0]["severity"] == "blocking"
    assert found[0]["values"]["schema"] == UNKNOWN


def test_an_approved_revision_whose_schema_changes_is_refused(
    client: TestClient, engine: Engine
) -> None:
    project_id = _save(client, DESIGNED["residential"]["request"])
    assert _approve(client, project_id).status_code == 200
    row = _row(engine, project_id)
    enveloped = {"schema": UNKNOWN, "model": row["design_snapshot"]}
    _write_snapshot(engine, project_id, enveloped, snapshot.sha256_of(enveloped))
    _refusal(_export(client, project_id, "pdf"), 409, "snapshot_schema_unknown")


def test_an_approval_of_a_snapshot_rewritten_to_an_unknown_schema_does_not_count(
    client: TestClient, engine: Engine
) -> None:
    """An approved snapshot rewritten to an unknown schema, rehashed, is approved no longer.

    The hash passes, so it is the read that fails: the approval is of no
    design this version reads, so it is not current and execution has none.
    """
    project_id = _save(client, DESIGNED["residential"]["request"])
    assert _approve(client, project_id).status_code == 200
    row = _row(engine, project_id)
    enveloped = {"schema": UNKNOWN, "model": row["design_snapshot"]}
    _write_snapshot(engine, project_id, enveloped, snapshot.sha256_of(enveloped))

    opened = _summary(_open(client, project_id))
    assert opened["approval_current"] is False
    assert "snapshot" in opened["approval_changed"]
    assert opened["export_status"] == "not_validated"
    assert "approval" in opened["execution_blockers"]
    execution = client.post(
        f"/design/projects/{project_id}/revisions/1/export",
        json={"format": "json", "purpose": "execution"},
    )
    body = _refusal(execution, 409, "execution_package_incomplete")
    assert body["params"]["approval"] == "missing"
    assert "approval" in body["params"]["blockers"].split(",")


def test_v1_in_an_envelope_is_refused_through_the_api(client: TestClient, engine: Engine) -> None:
    project_id = _save(client, DESIGNED["residential"]["request"])
    row = _row(engine, project_id)
    enveloped = {"schema": "pp-design/1", "model": row["design_snapshot"]}
    _write_snapshot(engine, project_id, enveloped, snapshot.sha256_of(enveloped))
    body = _refusal(_export(client, project_id, "json"), 409, "snapshot_schema_unknown")
    assert body["params"]["schema"] == "pp-design/1"


# --------------------------------------------------------------------------- #
# Unknown fields are refused, not dropped
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("where", ["root", "board", "device"])
def test_an_unknown_field_is_a_structured_refusal(
    client: TestClient, engine: Engine, where: str
) -> None:
    project_id = _save(client, DESIGNED["residential"]["request"])
    stored = _row(engine, project_id)["design_snapshot"]
    target = {
        "root": stored,
        "board": stored["boards"][0],
        "device": stored["boards"][0]["devices"][0],
    }[where]
    target["unexpected_field"] = "would be dropped"
    _write_snapshot(engine, project_id, stored, snapshot.sha256_of(stored))
    for response in (_export(client, project_id, "json"), _approve(client, project_id)):
        _refusal(response, 409, "snapshot_integrity_failed")
    opened = _open(client, project_id)
    assert opened.status_code == 200, opened.text[:500]
    assert opened.json()["export_status"] != "review_approved"
    assert _row(engine, project_id)["approved_at"] is None

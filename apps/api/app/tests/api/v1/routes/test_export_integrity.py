"""Adversarial tests of Phase 1: trustworthy export and approval.

Written against ``docs/design-readiness/phase-1/contract.md`` by the
adversarial agent, independently of the implementation. They drive the real
routes through FastAPI's TestClient against the disposable Postgres
``panelpilot_phase1_test``; nothing here may run against the dev database.

The database-backed tests must run. A missing ``TEST_DATABASE_URL`` is a
failure, never a skip, so a green run cannot hide an untested gate.
"""

from __future__ import annotations

import copy
import hashlib
import io
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
from app.models.schemas.auth import CurrentUser, Role
from app.models.tables.tenant import TenantRow

DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "")

FORGED_CHECKER = "FORGED CHECKER ZQX"
FORGED_APPROVER = "FORGED APPROVER ZQX"
DRAFT_WORDS = "NOT FOR EXECUTION"
APPROVER = "Eng. Real Approver"

#: Every format the export enum offers. A format added later without a test
#: here fails ``test_every_export_format_is_covered``.
PDF_FORMATS = ("pdf", "calculations_pdf", "quotation_pdf")
CSV_FORMATS = (
    "devices_csv",
    "parts_csv",
    "cables_csv",
    "circuits_csv",
    "terminals_csv",
    "eplan_devices_csv",
    "ace_components_csv",
    "ace_terminals_csv",
    "quotation_csv",
    "plc_io_csv",
)
OTHER_FORMATS = ("dxf", "qet", "aml", "json", "plc_st")
ALL_FORMATS = PDF_FORMATS + CSV_FORMATS + OTHER_FORMATS
#: The formats whose draft mark would only be a comment a normal import strips:
#: refused on every export path (``export_format_unprotected``) until a mark
#: that survives import is shown.
BLOCKED_FORMATS = (*CSV_FORMATS, "qet", "aml", "plc_st")
#: The formats exported, each carrying a visible mark.
ALLOWED_FORMATS = tuple(f for f in ALL_FORMATS if f not in BLOCKED_FORMATS)

#: A project with a lighting circuit (1.5 mm2 cable) and a PLC-switched motor,
#: so every format, the control program included, has something to export.
REQUEST: dict[str, Any] = {
    "info": {"name": "ADV Integrity", "number": "ADV-001"},
    "boards": [
        {
            "name": "DB1",
            "supply": {"voltage_v": 400, "phases": 3, "fault_level_ka": 10},
            "loads": [
                {
                    "description": "Lighting adv",
                    "load": "lighting",
                    "power_kw": "0.2",
                    "phases": 1,
                    "length_m": 10,
                },
                {
                    "description": "Pump adv",
                    "load": "motor",
                    "power_kw": "5.5",
                    "phases": 3,
                    "starter": "dol",
                    "controlled": True,
                    "length_m": 20,
                },
            ],
        }
    ],
}

PRICING: dict[str, Any] = {"currency": "JOD", "labour_per_circuit": "10"}


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #


@pytest.fixture(scope="module", name="engine")
def _engine() -> Iterator[Engine]:
    if not DATABASE_URL.startswith("postgresql"):
        pytest.fail(
            "TEST_DATABASE_URL must point at the disposable Postgres "
            "(panelpilot_phase1_test); these gates may not be skipped"
        )
    if not DATABASE_URL.rsplit("/", 1)[-1].startswith("panelpilot_phase1_"):
        pytest.fail(
            f"refusing to run against a database other than the disposable one: {DATABASE_URL}"
        )
    engine = create_engine(DATABASE_URL)
    yield engine
    engine.dispose()


@pytest.fixture(name="tenants")
def _tenants(engine: Engine) -> tuple[str, str]:
    with engine.begin() as conn:
        conn.execute(text("TRUNCATE design_project_revisions, design_projects CASCADE"))
        ids: list[str] = []
        for slug in ("adv-export-a", "adv-export-b"):
            found = conn.execute(
                text("SELECT id FROM tenants WHERE slug = :s"), {"s": slug}
            ).scalar_one_or_none()
            if found is None:
                with Session(bind=conn) as orm:
                    row = TenantRow(slug=slug, name=slug)
                    orm.add(row)
                    orm.flush()
                    found = row.id
            ids.append(str(found))
        conn.execute(
            text("DELETE FROM company_settings WHERE tenant_id = ANY(:t)"),
            {"t": [uuid.UUID(i) for i in ids]},
        )
    return ids[0], ids[1]


class _Caller:
    """Who the next request is from; switched by a test to attack across tenants."""

    def __init__(self, tenant: str) -> None:
        self.user = _user(tenant)


def _user(tenant: str, email: str = "eng-a@example.com") -> CurrentUser:
    return CurrentUser(
        id=str(uuid.uuid4()), email=email, tenant_id=tenant, roles=frozenset({Role.ENGINEER})
    )


@pytest.fixture(name="caller")
def _caller(tenants: tuple[str, str]) -> _Caller:
    return _Caller(tenants[0])


@pytest.fixture(name="app")
def _app(engine: Engine, caller: _Caller) -> FastAPI:
    factory = sessionmaker(bind=engine)

    def session() -> Iterator[Session]:
        opened = factory()
        try:
            yield opened
        finally:
            opened.close()

    app = FastAPI()
    app.include_router(design_route.router, prefix="/design")
    app.dependency_overrides[deps.get_current_user] = lambda: caller.user
    app.dependency_overrides[get_session] = session
    app.dependency_overrides[deps.enforce_design_rate_limit] = lambda: None
    install_exception_handlers(app)
    return app


@pytest.fixture(name="client")
def _client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def canonical_sha256(value: Any) -> str:
    """The contract's canonical hash: sorted keys, no whitespace, UTF-8."""
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def pdf_pages(content: bytes) -> list[str]:
    import pdfplumber

    assert content.startswith(b"%PDF"), content[:200]
    with pdfplumber.open(io.BytesIO(content)) as pdf:
        return [page.extract_text() or "" for page in pdf.pages]


def text_of(fmt: str, content: bytes) -> str:
    if fmt in PDF_FORMATS:
        return "\n".join(pdf_pages(content))
    return content.decode("utf-8", errors="replace")


def designed_project(client: TestClient, request: dict[str, Any] | None = None) -> dict[str, Any]:
    response = client.post("/design/project", json=request or REQUEST)
    assert response.status_code == 200, response.text
    project: dict[str, Any] = response.json()["project"]
    return project


def forged(project: dict[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(project)
    out["info"]["revisions"] = [
        {
            "index": "A",
            "date": "2026-10-23",
            "description": "client issue",
            "drawn_by": "DRAWER",
            "checked_by": FORGED_CHECKER,
            "approved_by": FORGED_APPROVER,
        }
    ]
    return out


def lighting_ids(project: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """The lighting circuit, its breaker and its cable."""
    board = project["boards"][0]
    circuit = next(c for c in board["circuits"] if c["load"] == "lighting")
    devices = {d["id"]: d for d in board["devices"]}
    breaker = next(
        devices[i] for i in circuit["device_ids"] if devices[i]["kind"] == "circuit_breaker"
    )
    cable = next(c for c in board["cables"] if c["id"] == circuit["cable_id"])
    return circuit, breaker, cable


def tampered(project: dict[str, Any], amps: str = "200") -> dict[str, Any]:
    out = copy.deepcopy(project)
    _, breaker, cable = lighting_ids(out)
    breaker["rated_current_a"] = amps
    cable["cross_section_mm2"] = "1.5"
    return out


def draft_export(client: TestClient, project: dict[str, Any], fmt: str) -> Any:
    body: dict[str, Any] = {"project": project, "format": fmt}
    if fmt.startswith("quotation"):
        body["pricing"] = PRICING
    return client.post("/design/export", json=body)


def save(client: TestClient, request: dict[str, Any] | None = None) -> dict[str, Any]:
    response = client.post(
        "/design/projects", json={"name": "ADV", "request": request or REQUEST, "note": "rev 1"}
    )
    assert response.status_code == 201, response.text
    saved: dict[str, Any] = response.json()
    return saved


def revise(client: TestClient, project_id: str, request: dict[str, Any] | None = None) -> Any:
    return client.post(
        f"/design/projects/{project_id}/revisions",
        json={"request": request or REQUEST, "note": "next"},
    )


def approve(client: TestClient, project_id: str, number: int, approver: str = APPROVER) -> Any:
    return client.post(
        f"/design/projects/{project_id}/revisions/{number}/approval", json={"approver": approver}
    )


def revision_export(
    client: TestClient, project_id: str, number: int, fmt: str, **extra: Any
) -> Any:
    body: dict[str, Any] = {"format": fmt, **extra}
    if fmt.startswith("quotation") and "pricing" not in body:
        body["pricing"] = PRICING
    return client.post(f"/design/projects/{project_id}/revisions/{number}/export", json=body)


def revision_row(engine: Engine, project_id: str, number: int) -> dict[str, Any]:
    with engine.connect() as conn:
        row = (
            conn.execute(
                text(
                    "SELECT * FROM design_project_revisions WHERE project_id = :p AND number = :n"
                ),
                {"p": uuid.UUID(project_id), "n": number},
            )
            .mappings()
            .one()
        )
    return dict(row)


def update_row(engine: Engine, project_id: str, number: int, **values: Any) -> None:
    sets = ", ".join(f"{k} = :{k}" for k in values)
    params: dict[str, Any] = {"p": uuid.UUID(project_id), "n": number}
    for key, value in values.items():
        params[key] = json.dumps(value) if isinstance(value, (dict, list)) else value
    casts = {k: f"CAST(:{k} AS JSONB)" for k, v in values.items() if isinstance(v, (dict, list))}
    if casts:
        sets = ", ".join(f"{k} = {casts.get(k, ':' + k)}" for k in values)
    with engine.begin() as conn:
        conn.execute(
            text(
                f"UPDATE design_project_revisions SET {sets} WHERE project_id = :p AND number = :n"
            ),
            params,
        )


def error(response: Any) -> dict[str, Any]:
    assert not response.headers.get("content-type", "").startswith(
        ("application/pdf", "text/csv", "application/octet-stream")
    ), f"expected an error envelope, got a file ({response.headers.get('content-type')})"
    body: dict[str, Any] = response.json()
    return body


def revision_of(saved: dict[str, Any], number: int) -> dict[str, Any]:
    found: dict[str, Any] = next(r for r in saved["revisions"] if r["number"] == number)
    return found


# --------------------------------------------------------------------------- #
# Coverage of the format enum
# --------------------------------------------------------------------------- #


def test_every_export_format_is_covered() -> None:
    from app.models.schemas.design import ExportFormat

    assert {f.value for f in ExportFormat} == set(ALL_FORMATS)


# --------------------------------------------------------------------------- #
# Draft export: forged approval, DRAFT mark, findings
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("fmt", ALLOWED_FORMATS)
def test_draft_export_never_prints_forged_approval_and_is_marked(
    client: TestClient, fmt: str
) -> None:
    project = forged(designed_project(client))
    response = draft_export(client, project, fmt)
    assert response.status_code == 200, response.text[:500]
    body = text_of(fmt, response.content)
    assert FORGED_CHECKER not in body
    assert FORGED_APPROVER not in body
    assert "REVIEW APPROVED" not in body
    assert DRAFT_WORDS in body, f"{fmt}: no DRAFT mark"
    if fmt in PDF_FORMATS:
        for number, page in enumerate(pdf_pages(response.content), start=1):
            assert DRAFT_WORDS in page, f"{fmt} page {number} unmarked"


def assert_unprotected(response: Any, fmt: str) -> None:
    """The refusal of a format whose draft mark would not survive import."""
    assert response.status_code == 409, f"{fmt}: {response.status_code} {response.text[:300]}"
    assert "content-disposition" not in response.headers, f"{fmt}: a file was sent"
    body = error(response)
    assert body["code"] == "export_format_unprotected"
    assert body["params"]["format"] == fmt
    assert body["params"]["reason"] == "draft_mark_not_persistent"
    assert set(body["params"]["allowed"].split(",")) == set(ALLOWED_FORMATS)


@pytest.mark.parametrize("fmt", BLOCKED_FORMATS)
def test_draft_export_of_an_unprotected_format_is_refused(client: TestClient, fmt: str) -> None:
    assert_unprotected(draft_export(client, forged(designed_project(client)), fmt), fmt)


def test_draft_json_blanks_checker_and_approver(client: TestClient) -> None:
    response = draft_export(client, forged(designed_project(client)), "json")
    assert response.status_code == 200
    exported = response.json()
    project = exported.get("project", exported)
    for row in project["info"]["revisions"]:
        assert row["checked_by"] == ""
        assert row["approved_by"] == ""
    # Drawing metadata the client may set survives.
    assert any(r.get("drawn_by") == "DRAWER" for r in project["info"]["revisions"])


def test_draft_with_tampered_rating_lists_a_blocking_finding(client: TestClient) -> None:
    project = tampered(designed_project(client))
    _circuit, _breaker, _ = lighting_ids(project)
    response = draft_export(client, project, "pdf")
    # The draft may be produced, but never as anything but a draft with findings.
    assert response.status_code == 200, response.text[:500]
    body = "\n".join(pdf_pages(response.content))
    assert DRAFT_WORDS in body
    assert "REVIEW APPROVED" not in body
    lowered = body.lower()
    assert "finding" in lowered, "no findings section on a draft of a tampered project"
    assert "blocking" in lowered, "the 200 A breaker on 1.5 mm2 is not reported as blocking"


def test_draft_with_63a_on_1_5mm2_lists_a_blocking_finding(client: TestClient) -> None:
    project = tampered(designed_project(client), amps="63")
    response = draft_export(client, project, "calculations_pdf")
    assert response.status_code == 200, response.text[:500]
    body = "\n".join(pdf_pages(response.content)).lower()
    assert "blocking" in body


def test_draft_with_a_missing_referenced_device_is_flagged(client: TestClient) -> None:
    project = designed_project(client)
    _circuit, breaker, _ = lighting_ids(project)
    board = project["boards"][0]
    board["devices"] = [d for d in board["devices"] if d["id"] != breaker["id"]]
    response = draft_export(client, project, "pdf")
    if response.status_code == 200:
        body = "\n".join(pdf_pages(response.content)).lower()
        assert (
            "blocking" in body
        ), "a circuit whose breaker does not exist exported without a finding"
    else:
        assert response.status_code in (409, 422), response.text


def test_valid_draft_succeeds_and_is_labelled(client: TestClient) -> None:
    response = draft_export(client, designed_project(client), "pdf")
    assert response.status_code == 200
    pages = pdf_pages(response.content)
    assert pages
    assert all(DRAFT_WORDS in p for p in pages)


# --------------------------------------------------------------------------- #
# Strict schema: unknown nested fields are 422 with a location
# --------------------------------------------------------------------------- #


def _with(path: list[Any], key: str, base: dict[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(base)
    node: Any = out
    for step in path:
        node = node[step]
    node[key] = {"kind": "ats"} if key in ("transfer_switch", "standby_supply") else "x"
    return out


REQUEST_UNKNOWN = [
    ([], "bogus_top"),
    (["info"], "bogus_info"),
    (["boards", 0], "standby_supply"),
    (["boards", 0], "transfer_switch"),
    (["boards", 0, "supply"], "second_source"),
    (["boards", 0, "loads", 0], "colour"),
]


@pytest.mark.parametrize(("path", "key"), REQUEST_UNKNOWN)
@pytest.mark.parametrize("route", ["/design/project", "/design/projects", "revision"])
def test_unknown_request_fields_are_refused_with_location(
    client: TestClient, path: list[Any], key: str, route: str
) -> None:
    body = _with(path, key, REQUEST)
    if route == "/design/project":
        response = client.post(route, json=body)
    elif route == "/design/projects":
        response = client.post(route, json={"name": "ADV", "request": body})
    else:
        saved = save(client)
        response = revise(client, saved["id"], body)
    assert response.status_code == 422, f"{route} accepted {key}: {response.status_code}"
    assert key in json.dumps(response.json()), "the 422 does not name the field"


@pytest.mark.parametrize(
    ("path", "key"),
    [
        ([], "bogus_top"),
        (["project"], "bogus_project"),
        (["project", "info"], "bogus_info"),
        (["project", "info", "revisions", 0], "approved_at"),
        (["project", "boards", 0], "transfer_switch"),
        (["project", "boards", 0, "supply"], "standby_supply"),
        (["project", "boards", 0, "circuits", 0], "colour"),
        (["project", "boards", 0, "devices", 0], "approved"),
        (["project", "boards", 0, "cables", 0], "verified"),
    ],
)
def test_unknown_export_fields_are_refused_with_location(
    client: TestClient, path: list[Any], key: str
) -> None:
    body = {"project": forged(designed_project(client)), "format": "pdf"}
    response = client.post("/design/export", json=_with(path, key, body))
    assert response.status_code == 422, f"draft export accepted {key}"
    assert key in json.dumps(response.json())


def test_revision_export_refuses_a_client_project(client: TestClient) -> None:
    saved = save(client)
    project = tampered(designed_project(client))
    response = revision_export(client, saved["id"], 1, "pdf", project=project)
    if response.status_code == 422:
        return
    # Accepted only if the client project is ignored entirely: the JSON export
    # must be the server snapshot, not the tampered rating.
    assert response.status_code == 200
    exported = revision_export(client, saved["id"], 1, "json", project=project)
    assert exported.status_code == 200
    _, breaker, _ = lighting_ids(exported.json().get("project", exported.json()))
    assert str(breaker["rated_current_a"]) != "200"


def test_revision_export_refuses_an_unknown_purpose(client: TestClient) -> None:
    saved = save(client)
    response = revision_export(client, saved["id"], 1, "pdf", purpose="approved")
    assert response.status_code == 422


# --------------------------------------------------------------------------- #
# Save → snapshot; approval; labels
# --------------------------------------------------------------------------- #


def test_save_records_a_server_snapshot_and_its_hash(client: TestClient, engine: Engine) -> None:
    saved = save(client)
    row = revision_row(engine, saved["id"], 1)
    assert row["design_snapshot"] is not None
    assert row["snapshot_sha256"] == canonical_sha256(row["design_snapshot"])
    assert row["rules_version"]
    assert row["profile_sha256"]
    assert len(row["profile_sha256"]) == 64
    assert row["catalogue_version"] == "none"
    assert row["approved_snapshot_sha256"] is None
    opened = client.get(f"/design/projects/{saved['id']}").json()
    flat = json.dumps(opened)
    for field in ("findings", "snapshot_sha256", "rules_version", "validated", "export_status"):
        assert field in flat, f"GET does not expose {field}"
    assert row["snapshot_sha256"] in flat


def test_save_ignores_forged_approval_in_the_request(client: TestClient, engine: Engine) -> None:
    request = copy.deepcopy(REQUEST)
    request["info"]["revisions"] = forged({"info": {}})["info"]["revisions"]
    saved = save(client, request)
    row = revision_row(engine, saved["id"], 1)
    assert FORGED_APPROVER not in json.dumps(row["design_snapshot"])
    assert row["approved_by"] is None
    response = revision_export(client, saved["id"], 1, "pdf")
    assert response.status_code == 200, response.text
    body = "\n".join(pdf_pages(response.content))
    assert FORGED_APPROVER not in body
    assert FORGED_CHECKER not in body
    assert "REVIEW APPROVED" not in body


@pytest.mark.parametrize("fmt", ALLOWED_FORMATS)
def test_unapproved_revision_export_is_draft_with_identity(
    client: TestClient, engine: Engine, fmt: str
) -> None:
    saved = save(client)
    row = revision_row(engine, saved["id"], 1)
    response = revision_export(client, saved["id"], 1, fmt)
    assert response.status_code == 200, response.text[:500]
    body = text_of(fmt, response.content)
    assert DRAFT_WORDS in body, f"{fmt}: unapproved revision not marked DRAFT"
    assert "REVIEW APPROVED" not in body
    if fmt in ("pdf", "calculations_pdf"):
        assert row["snapshot_sha256"][:8] in body, f"{fmt}: revision identity not printed"


@pytest.mark.parametrize("fmt", ALLOWED_FORMATS)
def test_approved_revision_export_carries_db_approval(
    client: TestClient, engine: Engine, fmt: str
) -> None:
    saved = save(client)
    assert approve(client, saved["id"], 1).status_code == 200
    row = revision_row(engine, saved["id"], 1)
    assert row["approved_snapshot_sha256"] == row["snapshot_sha256"]
    response = revision_export(client, saved["id"], 1, fmt)
    assert response.status_code == 200, response.text[:500]
    body = text_of(fmt, response.content)
    assert "REVIEW APPROVED" in body, f"{fmt}: approved revision not labelled"
    assert DRAFT_WORDS in body  # "STUDY, NOT FOR EXECUTION"
    if fmt in ("pdf", "calculations_pdf"):
        assert APPROVER in body
        assert row["snapshot_sha256"][:8] in body
        assert row["rules_version"] in body
    if fmt in PDF_FORMATS:
        for page in pdf_pages(response.content):
            assert DRAFT_WORDS in page


@pytest.mark.parametrize("fmt", BLOCKED_FORMATS)
@pytest.mark.parametrize("approved", [False, True])
def test_revision_export_of_an_unprotected_format_is_refused(
    client: TestClient, fmt: str, approved: bool
) -> None:
    saved = save(client)
    if approved:
        assert approve(client, saved["id"], 1).status_code == 200
    assert_unprotected(revision_export(client, saved["id"], 1, fmt), fmt)


def test_approval_cannot_be_forged_through_the_approval_body(
    client: TestClient, engine: Engine
) -> None:
    saved = save(client)
    response = client.post(
        f"/design/projects/{saved['id']}/revisions/1/approval",
        json={"approver": APPROVER, "approved_snapshot_sha256": "0" * 64},
    )
    if response.status_code == 200:
        row = revision_row(engine, saved["id"], 1)
        assert row["approved_snapshot_sha256"] == row["snapshot_sha256"]
    else:
        assert response.status_code == 422


def test_approval_is_single(client: TestClient) -> None:
    saved = save(client)
    assert approve(client, saved["id"], 1).status_code == 200
    again = approve(client, saved["id"], 1, approver="Someone Else")
    assert again.status_code in (400, 409, 422)
    assert error(again)["code"] == "revision_already_approved"


def test_approval_of_a_missing_revision_is_404(client: TestClient) -> None:
    saved = save(client)
    response = approve(client, saved["id"], 9)
    assert response.status_code == 404


def test_missing_approval_is_not_presented_as_approved(client: TestClient) -> None:
    saved = save(client)
    response = revision_export(client, saved["id"], 1, "calculations_pdf")
    body = "\n".join(pdf_pages(response.content))
    assert "REVIEW APPROVED" not in body
    assert DRAFT_WORDS in body


def test_approving_one_revision_does_not_cover_another(client: TestClient, engine: Engine) -> None:
    saved = save(client)
    assert revise(client, saved["id"]).status_code == 201
    assert approve(client, saved["id"], 1).status_code == 200
    second = revision_export(client, saved["id"], 2, "pdf")
    assert second.status_code == 200
    body = "\n".join(pdf_pages(second.content))
    assert "REVIEW APPROVED" not in body
    assert APPROVER not in body
    assert revision_row(engine, saved["id"], 2)["approved_snapshot_sha256"] is None


def test_changes_after_approval_are_a_new_unapproved_revision(
    client: TestClient, engine: Engine
) -> None:
    saved = save(client)
    assert approve(client, saved["id"], 1).status_code == 200
    changed = copy.deepcopy(REQUEST)
    changed["boards"][0]["loads"][1]["power_kw"] = "7.5"
    revised = revise(client, saved["id"], changed)
    assert revised.status_code == 201, revised.text
    assert revised.json()["revision"] == 2
    second = revision_row(engine, saved["id"], 2)
    first = revision_row(engine, saved["id"], 1)
    assert second["snapshot_sha256"] != first["snapshot_sha256"]
    assert second["approved_snapshot_sha256"] is None
    assert second["approved_by"] is None
    body = "\n".join(pdf_pages(revision_export(client, saved["id"], 2, "pdf").content))
    assert "REVIEW APPROVED" not in body
    old = "\n".join(pdf_pages(revision_export(client, saved["id"], 1, "pdf").content))
    assert "REVIEW APPROVED" in old
    assert first["snapshot_sha256"][:8] in old


# --------------------------------------------------------------------------- #
# Database tampering
# --------------------------------------------------------------------------- #


def test_tampered_snapshot_fails_integrity(client: TestClient, engine: Engine) -> None:
    saved = save(client)
    assert approve(client, saved["id"], 1).status_code == 200
    row = revision_row(engine, saved["id"], 1)
    snapshot = tampered(row["design_snapshot"])
    update_row(engine, saved["id"], 1, design_snapshot=snapshot)
    for fmt in ("pdf", "dxf", "json"):
        response = revision_export(client, saved["id"], 1, fmt)
        assert response.status_code >= 400, f"{fmt}: tampered snapshot exported"
        assert error(response)["code"] == "snapshot_integrity_failed"


def test_rehashed_tampered_snapshot_loses_approval_and_blocks(
    client: TestClient, engine: Engine
) -> None:
    """A row edited consistently (snapshot and its hash) is no longer the approved one."""
    saved = save(client)
    assert approve(client, saved["id"], 1).status_code == 200
    row = revision_row(engine, saved["id"], 1)
    snapshot = tampered(row["design_snapshot"])
    update_row(
        engine, saved["id"], 1, design_snapshot=snapshot, snapshot_sha256=canonical_sha256(snapshot)
    )
    response = revision_export(client, saved["id"], 1, "pdf")
    if response.status_code == 200:
        body = "\n".join(pdf_pages(response.content))
        assert "REVIEW APPROVED" not in body, "approval printed for a snapshot it did not approve"
        assert APPROVER not in body
        assert "blocking" in body.lower(), "200 A on 1.5 mm2 not reported"
    else:
        # revision_stale: re-running today's findings on the edited snapshot no
        # longer matches the findings recorded with it (the 200 A is found).
        assert error(response)["code"] in (
            "snapshot_integrity_failed",
            "revision_has_blocking_findings",
            "revision_stale",
        )


def test_approved_hash_mismatch_is_not_approved(client: TestClient, engine: Engine) -> None:
    saved = save(client)
    assert approve(client, saved["id"], 1).status_code == 200
    update_row(engine, saved["id"], 1, approved_snapshot_sha256="f" * 64)
    response = revision_export(client, saved["id"], 1, "pdf")
    assert response.status_code == 200, response.text
    body = "\n".join(pdf_pages(response.content))
    assert "REVIEW APPROVED" not in body
    assert APPROVER not in body
    assert DRAFT_WORDS in body


def test_approved_by_without_hash_is_not_approved(client: TestClient, engine: Engine) -> None:
    """An approver written into the row by hand (no bound hash) is not an approval."""
    saved = save(client)
    update_row(
        engine, saved["id"], 1, approved_by="SQL INJECTED APPROVER", approved_at="2026-10-23"
    )
    response = revision_export(client, saved["id"], 1, "pdf")
    assert response.status_code == 200
    body = "\n".join(pdf_pages(response.content))
    assert "SQL INJECTED APPROVER" not in body
    assert "REVIEW APPROVED" not in body


def test_legacy_revision_without_snapshot_is_not_validated(
    client: TestClient, engine: Engine
) -> None:
    saved = save(client)
    update_row(engine, saved["id"], 1, design_snapshot=None, snapshot_sha256=None)
    for fmt in ("pdf", "dxf", "json"):
        response = revision_export(client, saved["id"], 1, fmt)
        assert response.status_code in (409, 422), response.status_code
        assert error(response)["code"] == "revision_not_validated"
    approval = approve(client, saved["id"], 1)
    assert approval.status_code in (409, 422)
    assert error(approval)["code"] == "revision_not_validated"
    opened = client.get(f"/design/projects/{saved['id']}")
    assert opened.status_code == 200, "an older revision can no longer be opened"
    assert '"validated": false' in json.dumps(opened.json()) or '"validated":false' in opened.text


# --------------------------------------------------------------------------- #
# Blocking findings at save time
# --------------------------------------------------------------------------- #


def test_forced_oversized_breaker_blocks_approval(client: TestClient, engine: Engine) -> None:
    """A profile rule forcing 200 A on lighting cannot produce an approved artifact."""
    request = copy.deepcopy(REQUEST)
    request["profile"] = {"circuit_rules": {"lighting": {"breaker_a": "200"}}}
    response = client.post("/design/projects", json={"name": "ADV", "request": request})
    if response.status_code != 201:
        assert response.status_code in (400, 409, 422)
        return
    saved = response.json()
    row = revision_row(engine, saved["id"], 1)
    findings = row["findings"] or []
    if row["design_snapshot"] is None:
        # The design refused the request: saved without a snapshot, blocked.
        assert any(f["severity"] == "blocking" for f in findings), findings
        approval = approve(client, saved["id"], 1)
        assert approval.status_code in (409, 422)
        assert error(approval)["code"] in (
            "revision_not_validated",
            "revision_has_blocking_findings",
        )
        exported = revision_export(client, saved["id"], 1, "pdf")
        assert exported.status_code in (409, 422)
        assert error(exported)["code"] == "revision_not_validated"
        return
    _snap_circuit, snap_breaker, snap_cable = lighting_ids(row["design_snapshot"])
    from decimal import Decimal

    if Decimal(str(snap_breaker["rated_current_a"])) > Decimal(40) and Decimal(
        str(snap_cable["cross_section_mm2"])
    ) <= Decimal("4"):
        assert any(f["severity"] == "blocking" for f in findings), findings
    blocking = any(f["severity"] == "blocking" for f in findings)
    approval = approve(client, saved["id"], 1)
    if blocking:
        assert error(approval)["code"] == "revision_has_blocking_findings"
        execution = revision_export(client, saved["id"], 1, "pdf", purpose="execution")
        assert execution.status_code == 409


def test_findings_have_the_contract_shape(client: TestClient, engine: Engine) -> None:
    saved = save(client)
    row = revision_row(engine, saved["id"], 1)
    assert isinstance(row["findings"], list)
    assert row["findings"], "no findings recorded"
    for finding in row["findings"]:
        assert finding["severity"] in ("blocking", "warning", "info")
        for key in ("code", "message", "requires_engineering_confirmation", "source", "values"):
            assert key in finding, (key, finding)


# --------------------------------------------------------------------------- #
# Execution purpose
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("fmt", ALL_FORMATS)
@pytest.mark.parametrize("approved", [False, True])
def test_execution_purpose_is_always_refused(client: TestClient, fmt: str, approved: bool) -> None:
    saved = save(client)
    if approved:
        assert approve(client, saved["id"], 1).status_code == 200
    response = revision_export(client, saved["id"], 1, fmt, purpose="execution")
    assert response.status_code == 409, response.status_code
    body = error(response)
    assert body["code"] == "execution_package_incomplete"
    flat = json.dumps(body).lower()
    for needle in ("wiring", "control", "part", "mechanical"):
        assert needle in flat, f"missing component {needle!r} not named"
    if not approved:
        assert "approv" in flat, "missing approval not named"


# --------------------------------------------------------------------------- #
# Cross-tenant
# --------------------------------------------------------------------------- #


def test_another_tenant_cannot_read_export_or_approve(
    client: TestClient, caller: _Caller, tenants: tuple[str, str], engine: Engine
) -> None:
    saved = save(client)
    caller.user = _user(tenants[1], email="intruder@example.com")
    assert client.get(f"/design/projects/{saved['id']}").status_code == 404
    assert client.get(f"/design/projects/{saved['id']}?revision=1").status_code == 404
    exported = revision_export(client, saved["id"], 1, "pdf")
    assert exported.status_code == 404
    assert error(exported)["code"] in ("revision_not_found", "project_not_found")
    for purpose in ("review", "execution"):
        r = revision_export(client, saved["id"], 1, "json", purpose=purpose)
        assert r.status_code == 404, (purpose, r.status_code)
    approval = approve(client, saved["id"], 1, approver="INTRUDER")
    assert approval.status_code == 404
    assert revise(client, saved["id"]).status_code == 404
    assert revision_row(engine, saved["id"], 1)["approved_by"] is None


def test_malformed_ids_are_404_not_500(client: TestClient) -> None:
    for pid in ("not-a-uuid", str(uuid.uuid4())):
        assert revision_export(client, pid, 1, "pdf").status_code == 404
    saved = save(client)
    assert revision_export(client, saved["id"], 0, "pdf").status_code in (404, 422)
    assert revision_export(client, saved["id"], 99, "pdf").status_code == 404


# --------------------------------------------------------------------------- #
# Stale: rules version and company profile
# --------------------------------------------------------------------------- #


def _patch_rules_version(monkeypatch: pytest.MonkeyPatch, value: str) -> int:
    import sys

    patched = 0
    for name, module in list(sys.modules.items()):
        if name.startswith("app") and module is not None and hasattr(module, "RULES_VERSION"):
            monkeypatch.setattr(module, "RULES_VERSION", value)
            patched += 1
    return patched


def test_rules_version_change_after_approval_is_stale(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    saved = save(client)
    assert approve(client, saved["id"], 1).status_code == 200
    assert _patch_rules_version(monkeypatch, "adv-bumped-rules") >= 1, "no RULES_VERSION found"
    response = revision_export(client, saved["id"], 1, "pdf")
    assert response.status_code == 409
    body = error(response)
    assert body["code"] == "revision_stale"
    assert "rules" in json.dumps(body).lower()


def test_rules_version_in_row_differs_is_stale(client: TestClient, engine: Engine) -> None:
    saved = save(client)
    update_row(engine, saved["id"], 1, rules_version="0-old")
    response = revision_export(client, saved["id"], 1, "calculations_pdf")
    assert response.status_code == 409
    assert error(response)["code"] == "revision_stale"


def test_profile_change_inside_the_request_is_stale(client: TestClient, engine: Engine) -> None:
    """The recorded profile hash covers the profile the design was made under.

    The revision's own request carries that profile; if what it resolves to
    changes (simulated by editing the stored request in the disposable DB),
    the approved revision is stale rather than re-designed silently.
    """
    saved = save(client)
    assert approve(client, saved["id"], 1).status_code == 200
    row = revision_row(engine, saved["id"], 1)
    request = row["request"]
    request["profile"] = {"key": "adv-co", "name": "ADV Renamed Co"}
    update_row(engine, saved["id"], 1, request=request)
    response = revision_export(client, saved["id"], 1, "pdf")
    assert response.status_code == 409
    body = error(response)
    assert body["code"] == "revision_stale"
    assert "profile" in json.dumps(body).lower()


def test_company_settings_change_does_not_stale_a_revision(client: TestClient) -> None:
    """Saved company settings are not an input to a revision's design.

    The design is made from the revision's own ``request.profile``; the
    tenant's saved settings only pre-fill the web form. Changing them after
    approval therefore leaves the approved revision exactly what was approved
    (lead decision on the contract, Phase 1).
    """
    saved = save(client)
    assert approve(client, saved["id"], 1).status_code == 200
    changed = client.put(
        "/design/company-settings", json={"settings": {"key": "adv-co", "name": "ADV Renamed Co"}}
    )
    assert changed.status_code == 200, changed.text
    response = revision_export(client, saved["id"], 1, "pdf")
    assert response.status_code == 200, response.text[:300]
    body = "\n".join(pdf_pages(response.content))
    assert "REVIEW APPROVED" in body
    assert "ADV Renamed Co" not in body


def test_profile_hash_in_row_differs_is_stale(client: TestClient, engine: Engine) -> None:
    saved = save(client)
    update_row(engine, saved["id"], 1, profile_sha256="0" * 64)
    response = revision_export(client, saved["id"], 1, "pdf")
    assert response.status_code == 409
    assert error(response)["code"] == "revision_stale"


# --------------------------------------------------------------------------- #
# Defects found by the live attack (adversarial/attack2.json)
# --------------------------------------------------------------------------- #

#: Beyond Postgres ``integer`` (int4), and beyond any int64.
HUGE_REVISIONS = ("2147483648", "99999999999999999999")


@pytest.mark.parametrize("number", HUGE_REVISIONS)
def test_huge_revision_number_is_refused_not_500(client: TestClient, number: str) -> None:
    """Defect 1: approving revision 2147483648 overflowed the query (500)."""
    saved = save(client)
    pid = saved["id"]
    responses = {
        "approval": client.post(
            f"/design/projects/{pid}/revisions/{number}/approval", json={"approver": APPROVER}
        ),
        "export": client.post(
            f"/design/projects/{pid}/revisions/{number}/export", json={"format": "pdf"}
        ),
        "open": client.get(f"/design/projects/{pid}?revision={number}"),
    }
    for name, response in responses.items():
        assert response.status_code in (404, 422), (name, response.status_code, response.text)
        if response.status_code == 404:
            assert error(response)["code"] == "revision_not_found", (name, response.text)


def test_another_tenant_gets_revision_not_found_on_revision_endpoints(
    client: TestClient, caller: _Caller, tenants: tuple[str, str]
) -> None:
    """Defect 2: approval across tenants said ``project_not_found``."""
    saved = save(client)
    caller.user = _user(tenants[1], email="intruder@example.com")
    pid = saved["id"]
    responses = {
        "approval": approve(client, pid, 1, approver="INTRUDER"),
        "export": revision_export(client, pid, 1, "pdf"),
        "open": client.get(f"/design/projects/{pid}?revision=1"),
    }
    for name, response in responses.items():
        assert response.status_code == 404, (name, response.status_code)
        assert error(response)["code"] == "revision_not_found", (name, response.text)


#: No export may claim to be issued, for construction, or ready to execute.
FORBIDDEN_WORDING = ("issued", "for construction", "ready for execution", "execution ready")


def _forbidden_in(text: str) -> list[str]:
    lowered = text.lower()
    return [word for word in FORBIDDEN_WORDING if word in lowered]


@pytest.mark.parametrize("fmt", ALLOWED_FORMATS)
@pytest.mark.parametrize("state", ["draft", "unapproved", "approved"])
def test_no_export_says_issued_or_execution_ready(client: TestClient, fmt: str, state: str) -> None:
    """Defect 3: "Issued under …" and "issued for review" appeared in PDF/DXF/calc."""
    if state == "draft":
        response = draft_export(client, forged(designed_project(client)), fmt)
    else:
        saved = save(client)
        if state == "approved":
            assert approve(client, saved["id"], 1).status_code == 200
        response = revision_export(client, saved["id"], 1, fmt)
    assert response.status_code == 200, response.text[:300]
    found = _forbidden_in(text_of(fmt, response.content))
    assert not found, f"{state} {fmt} says {found}"


def test_second_approval_is_409_already_approved(client: TestClient, engine: Engine) -> None:
    saved = save(client)
    assert approve(client, saved["id"], 1).status_code == 200
    again = approve(client, saved["id"], 1, approver="Second Approver")
    assert again.status_code == 409, again.text
    assert error(again)["code"] == "revision_already_approved"
    assert revision_row(engine, saved["id"], 1)["approved_by"] == APPROVER


def test_overlong_project_name_is_refused(client: TestClient) -> None:
    """Defect 4: a 5 MB ``info.name`` was saved."""
    big = copy.deepcopy(REQUEST)
    big["info"]["name"] = "N" * 100_000
    response = client.post("/design/projects", json={"name": "big", "request": big})
    assert response.status_code == 422, response.status_code
    assert error(response)["detail"][0]["loc"] == ["body", "request", "info", "name"]

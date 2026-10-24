"""Tests for `app/api/v1/routes/design.py`.

Mirrors the module 1:1 — if you add a function there, add its test here.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import deps
from app.api.v1.routes import design as design_route
from app.core.config import get_settings
from app.core.db import get_session
from app.core.errors import install_exception_handlers
from app.domain.rate_limit import InMemoryRateLimitStore
from app.models.schemas.auth import CurrentUser, Role

BOARD = {
    "info": {"name": "Pocket"},
    "board": {
        "name": "DBG-HALL",
        "loads": [
            {"description": "Sockets", "load": "socket", "power_kw": "1.5"},
            {"description": "Lights", "load": "lighting", "power_kw": "0.6"},
        ],
    },
}


def _user() -> CurrentUser:
    return CurrentUser(
        id="u", email="e@example.com", tenant_id="t", roles=frozenset({Role.ENGINEER})
    )


@pytest.fixture(name="client")
def _client() -> Iterator[TestClient]:
    app = FastAPI()
    app.include_router(design_route.router, prefix="/design")
    app.dependency_overrides[deps.get_current_user] = _user
    app.dependency_overrides[get_session] = object
    store = InMemoryRateLimitStore()
    app.dependency_overrides[deps.get_rate_limit_store] = lambda: store
    install_exception_handlers(app)
    with TestClient(app) as test_client:
        yield test_client


def test_a_board_is_designed_then_exported(client: TestClient) -> None:
    response = client.post("/design/distribution-board", json=BOARD)
    assert response.status_code == 200
    project = response.json()["project"]
    assert project["boards"][0]["circuits"][0]["description"] == "Sockets"

    exported = client.post("/design/export", json={"project": project, "format": "pdf"})
    assert exported.status_code == 200
    assert exported.headers["content-type"] == "application/pdf"
    assert exported.headers["content-disposition"] == 'attachment; filename="Pocket.pdf"'
    assert exported.content.startswith(b"%PDF")

    report = client.post("/design/export", json={"project": project, "format": "calculations_pdf"})
    assert report.status_code == 200
    assert report.headers["content-disposition"] == (
        'attachment; filename="Pocket.calculations.pdf"'
    )
    assert report.content.startswith(b"%PDF")


def test_an_unknown_field_is_a_422_naming_it(client: TestClient) -> None:
    board = {**BOARD["board"], "standby_supply": {"voltage_v": 400}}  # type: ignore[dict-item]
    response = client.post("/design/distribution-board", json={**BOARD, "board": board})
    assert response.status_code == 422
    (error,) = response.json()["detail"]
    assert error["type"] == "extra_forbidden"
    assert error["loc"] == ["body", "board", "standby_supply"]


def test_a_draft_export_is_marked_and_prints_no_sent_approval(client: TestClient) -> None:
    project = client.post("/design/distribution-board", json=BOARD).json()["project"]
    project["info"]["revisions"] = [
        {"index": "01", "date": "2026-10-23", "checked_by": "Forged C", "approved_by": "Forged A"}
    ]
    exported = client.post("/design/export", json={"project": project, "format": "json"})
    assert exported.status_code == 200
    body = exported.json()
    assert body["export_status"]["label"] == "DRAFT — NOT FOR EXECUTION"
    (revision,) = body["project"]["info"]["revisions"]
    assert (revision["checked_by"], revision["approved_by"]) == ("", "")


def test_a_tampered_rating_is_a_blocking_finding_in_the_draft(client: TestClient) -> None:
    project = client.post("/design/distribution-board", json=BOARD).json()["project"]
    board = project["boards"][0]
    lights = next(c for c in board["circuits"] if c["description"] == "Lights")
    breaker = next(d for d in board["devices"] if d["id"] == lights["device_ids"][0])
    breaker["rated_current_a"] = "200"
    body = client.post("/design/export", json={"project": project, "format": "json"}).json()
    codes = [f["code"] for f in body["findings"] if f["severity"] == "blocking"]
    assert "integrity_device_above_cable_capacity" in codes


def test_the_dxf_is_exported_marked(client: TestClient) -> None:
    project = client.post("/design/distribution-board", json=BOARD).json()["project"]
    exported = client.post("/design/export", json={"project": project, "format": "dxf"})
    assert exported.status_code == 200
    assert 'filename="Pocket.dxf"' in exported.headers["content-disposition"]
    assert b"NOT FOR EXECUTION" in exported.content


@pytest.mark.parametrize(
    "format_",
    ["eplan_devices_csv", "ace_components_csv", "ace_terminals_csv", "circuits_csv", "qet", "aml"],
)
def test_a_format_whose_mark_an_import_strips_is_409(client: TestClient, format_: str) -> None:
    project = client.post("/design/distribution-board", json=BOARD).json()["project"]
    refused = client.post("/design/export", json={"project": project, "format": format_})
    assert refused.status_code == 409
    assert "content-disposition" not in refused.headers
    body = refused.json()
    assert body["code"] == "export_format_unprotected"
    assert body["params"]["format"] == format_
    assert body["params"]["reason"] == "draft_mark_not_persistent"


def test_an_arabic_project_name_is_kept_for_the_download(client: TestClient) -> None:
    from urllib.parse import quote

    body = {**BOARD, "info": {"name": "برج القمة"}}
    project = client.post("/design/distribution-board", json=body).json()["project"]
    exported = client.post("/design/export", json={"project": project, "format": "pdf"})
    disposition = exported.headers["content-disposition"]
    assert 'filename="project.pdf"' in disposition
    assert "filename*=UTF-8''" + quote("برج-القمة.pdf", safe="") in disposition


def test_a_bad_profile_is_a_client_error(client: TestClient) -> None:
    response = client.post(
        "/design/distribution-board", json={**BOARD, "profile": {"key": "x", "bogus": 1}}
    )
    assert response.status_code in (400, 422)


def test_an_unknown_format_is_refused(client: TestClient) -> None:
    project = client.post("/design/distribution-board", json=BOARD).json()["project"]
    assert (
        client.post("/design/export", json={"project": project, "format": "dwg"}).status_code == 422
    )


def test_a_schedule_is_uploaded(client: TestClient) -> None:
    response = client.post(
        "/design/load-schedule/import",
        files={"file": ("schedule.csv", b"Description,kW\nPump,2.2\n", "text/csv")},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["loads"][0]["load"] == "motor"
    assert body["warnings"] == [
        {
            "code": "import_kind_inferred",
            "params": {"row": "2", "load": "Pump", "kind": "motor"},
            "text": "Row 2 (Pump): taken as motor from its description.",
        }
    ]


def test_an_unreadable_schedule_is_a_client_error(client: TestClient) -> None:
    response = client.post(
        "/design/load-schedule/import",
        files={"file": ("x.csv", b"Name,Colour\n", "text/csv")},
    )
    assert response.status_code in (400, 422)


def test_a_suggestion_is_charged_to_the_month(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from types import SimpleNamespace

    from app.domain import design as design_domain
    from app.domain import model_budget
    from app.models.schemas.design import DesignNote, LoadScheduleSuggestion

    charged: list[str] = []
    committed: list[bool] = []

    def charge(**kwargs: object) -> None:
        charged.append(str(kwargs["tenant_id"]))

    def suggest(**kwargs: object) -> LoadScheduleSuggestion:
        del kwargs
        return LoadScheduleSuggestion(
            loads=[], assumptions=[DesignNote(code="text", params={"text": "x"}, text="x")]
        )

    monkeypatch.setattr(model_budget, "charge_model_call", charge)
    monkeypatch.setattr(design_domain, "suggest_load_schedule", suggest)
    app = client.app
    app.dependency_overrides[deps.enforce_trial_rate_limit] = lambda: None  # type: ignore[attr-defined]
    app.dependency_overrides[get_session] = lambda: SimpleNamespace(  # type: ignore[attr-defined]
        commit=lambda: committed.append(True)
    )
    response = client.post("/design/load-schedule/suggest", json={"description": "a hall"})
    assert response.status_code == 200
    assert response.json() == {
        "loads": [],
        "assumptions": [{"code": "text", "params": {"text": "x"}, "text": "x"}],
    }
    assert charged == ["t"]
    assert committed == [True]


def test_a_board_is_priced(client: TestClient) -> None:
    project = client.post("/design/distribution-board", json=BOARD).json()["project"]
    response = client.post(
        "/design/quotation",
        json={
            "project": project,
            "pricing": {"price_list": [{"key": "circuit_breaker:1P:C16", "unit_price": "4"}]},
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["currency"] == "JOD"
    assert body["complete"] is False
    assert body["status"] == "provisional_estimate"
    assert body["label"].startswith("Provisional estimate: not a quotation, not for execution")
    assert body["blocking"] is False


def _priced(client: TestClient, project: dict[str, Any], **pricing: Any) -> Any:
    return client.post(
        "/design/quotation",
        json={"project": project, "pricing": {"currency": "JOD", **pricing}},
    )


def _tampered(project: dict[str, Any], amps: str = "200") -> dict[str, Any]:
    """The lighting circuit's breaker forged to ``amps`` on a 1.5 mm2 cable."""
    board = project["boards"][0]
    circuit = next(c for c in board["circuits"] if c["load"] == "lighting")
    devices = {d["id"]: d for d in board["devices"]}
    breaker = next(
        devices[i] for i in circuit["device_ids"] if devices[i]["kind"] == "circuit_breaker"
    )
    breaker["rated_current_a"] = amps
    cable = next(c for c in board["cables"] if c["id"] == circuit["cable_id"])
    cable["cross_section_mm2"] = "1.5"
    return project


def test_an_estimate_of_a_tampered_rating_is_flagged_blocking(client: TestClient) -> None:
    project = _tampered(client.post("/design/distribution-board", json=BOARD).json()["project"])
    response = _priced(client, project)
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "provisional_estimate"
    assert body["blocking"] is True
    blocking = [f for f in body["findings"] if f["severity"] == "blocking"]
    assert blocking, body["findings"]


def test_an_estimate_carries_no_approval_or_revision(client: TestClient) -> None:
    """A forged approver on the project's revisions never reaches the estimate."""
    project = client.post("/design/distribution-board", json=BOARD).json()["project"]
    project["info"]["revisions"] = [
        {
            "index": "A",
            "date": "2026-10-23",
            "description": "issued",
            "drawn_by": "D",
            "checked_by": "FORGED",
            "approved_by": "FORGED",
        }
    ]
    response = _priced(client, project)
    assert response.status_code == 200
    raw = response.text
    assert "FORGED" not in raw
    assert "approv" not in raw.lower().replace("not approved", "")
    for key in ("approved_by", "approver", "revision", "approved_at", "snapshot_sha256"):
        assert key not in response.json()


@pytest.mark.parametrize(
    ("where", "extra"),
    [
        ("body", {"status": "approved"}),
        ("body", {"approved_by": "Eng. Forged"}),
        ("body", {"revision": 1}),
        ("pricing", {"approved": True}),
        ("pricing", {"catalogue": [{"key": "x", "unit_price": "1"}]}),
    ],
)
def test_forged_estimate_fields_are_refused_as_unknown(
    client: TestClient, where: str, extra: dict[str, Any]
) -> None:
    project = client.post("/design/distribution-board", json=BOARD).json()["project"]
    body: dict[str, Any] = {"project": project, "pricing": {"currency": "JOD"}}
    if where == "body":
        body.update(extra)
    else:
        body["pricing"].update(extra)
    response = client.post("/design/quotation", json=body)
    assert response.status_code == 422
    (error,) = response.json()["detail"]
    assert error["type"] == "extra_forbidden"
    name = next(iter(extra))
    assert error["loc"] == (["body", name] if where == "body" else ["body", "pricing", name])


@pytest.mark.parametrize(
    "pricing",
    [
        {"labour_per_circuit": "-1"},
        {"labour_per_board": "1e12"},
        {"enclosure_price": "NaN"},
        {"enclosure_price": "Infinity"},
        {"markup_percent": "501"},
        {"vat_percent": "-0.1"},
        {"cable_length_m": "0"},
        {"cable_length_m": "1e9"},
        {"currency": "JOD<script>"},
        {"currency": ""},
        {"price_list": [{"key": "circuit_breaker:1P:C16", "unit_price": "-4"}]},
        {"price_list": [{"key": "circuit_breaker:1P:C16", "unit_price": "1e15"}]},
        {"price_list": [{"key": "circuit_breaker:1P:C16", "unit_price": "NaN"}]},
        {"price_list": [{"key": "", "unit_price": "4"}]},
    ],
)
def test_out_of_bounds_pricing_is_refused(client: TestClient, pricing: dict[str, Any]) -> None:
    project = client.post("/design/distribution-board", json=BOARD).json()["project"]
    response = _priced(client, project, **pricing)
    assert response.status_code == 422, response.text


def test_the_estimate_needs_a_signed_in_caller(monkeypatch: pytest.MonkeyPatch) -> None:
    # Decoding the (absent) token reads the settings, which the unit job
    # does not configure: provide them, as tests/core/test_security.py does.
    for name, value in {
        "ENVIRONMENT": "dev",
        "DATABASE_URL": "postgresql+psycopg://test:test@localhost:5432/test",
        "OPENSEARCH_URL": "http://localhost:9200",
        "OPENAI_API_KEY": "test-key",
        "JWT_SECRET": "test-secret-at-least-32-bytes-long-ok",
        "REDIS_URL": "redis://localhost:6379/0",
    }.items():
        monkeypatch.setenv(name, value)
    get_settings.cache_clear()
    try:
        app = FastAPI()
        app.include_router(design_route.router, prefix="/design")
        app.dependency_overrides[get_session] = object
        install_exception_handlers(app)
        with TestClient(app) as anonymous:
            response = anonymous.post(
                "/design/quotation", json={"project": {}, "pricing": {"currency": "JOD"}}
            )
    finally:
        get_settings.cache_clear()
    assert response.status_code == 401


def test_a_price_list_is_uploaded(client: TestClient) -> None:
    response = client.post(
        "/design/price-list/import",
        files={"file": ("prices.csv", b"Key,Price\nX,2\n", "text/csv")},
    )
    assert response.status_code == 200
    assert response.json() == [{"key": "X", "description": "", "unit_price": "2"}]


def test_the_plc_program_is_refused_as_its_export_is(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The program's source is the unmarked plc_st file: refused, as its export is."""
    from app.design import plc_program

    board = {
        "info": {"name": "Pocket"},
        "board": {
            "name": "DBG-HALL",
            "loads": [
                {
                    "description": "Lights",
                    "load": "lighting",
                    "power_kw": "0.6",
                    "controlled": True,
                }
            ],
        },
    }
    project = client.post("/design/distribution-board", json=board).json()["project"]

    def never(*args: object, **kwargs: object) -> object:
        raise AssertionError("the program was written before it was refused")

    monkeypatch.setattr(plc_program, "build_program", never)
    response = client.post("/design/plc", json={"project": project})
    exported = client.post("/design/export", json={"project": project, "format": "plc_st"})
    assert response.status_code == 409
    body = response.json()
    assert body["code"] == "export_format_unprotected"
    assert body["params"] == exported.json()["params"]
    assert "source" not in body
    assert "END_PROGRAM" not in response.text


def test_an_unknown_nested_profile_setting_is_refused(client: TestClient) -> None:
    profile = {"key": "acme", "circuit_rules": {"lighting": {"breaker_amps": 6}}}
    designed = client.post("/design/distribution-board", json={**BOARD, "profile": profile})
    assert designed.status_code == 422
    assert designed.json()["code"] == "profile_unknown_settings"
    assert "circuit_rules.lighting.breaker_amps" in designed.json()["params"]["settings"]
    project = client.post("/design/distribution-board", json=BOARD).json()["project"]
    exported = client.post(
        "/design/export", json={"project": project, "format": "pdf", "profile": profile}
    )
    assert exported.status_code == 422
    assert exported.json()["code"] == "profile_unknown_settings"


def test_an_approver_of_only_whitespace_is_refused(client: TestClient) -> None:
    refused = client.post("/design/projects/p1/revisions/1/approval", json={"approver": "   "})
    assert refused.status_code == 422


def test_a_saved_request_that_no_longer_validates_is_sent_as_stored(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.domain import design_projects
    from app.models.schemas.design import ProjectDesignRequest, SavedProject

    stored: dict[str, Any] = {
        "info": {"name": "Pocket", "revisions": []},
        "boards": [{"name": "DB", "loads": [{"description": "x" * 600, "load": "socket"}]}],
    }
    saved = SavedProject(
        id="p1",
        name="Pocket",
        revisions=[],
        revision=1,
        request=ProjectDesignRequest.model_construct(**stored),
    ).as_stored(stored)
    monkeypatch.setattr(design_projects, "open_project", lambda **_: saved)
    response = client.get("/design/projects/p1")
    assert response.status_code == 200
    assert response.json()["request"] == stored


def test_a_project_of_boards_is_designed(client: TestClient) -> None:
    loads = [{"description": "Lights", "load": "lighting", "power_kw": "1"}]
    response = client.post(
        "/design/project",
        json={
            "info": {"name": "Tower"},
            "boards": [
                {"name": "MDB", "loads": loads},
                {"name": "DB-1", "loads": loads, "fed_from": "MDB"},
            ],
        },
    )
    assert response.status_code == 200
    boards = response.json()["project"]["boards"]
    assert [b["name"] for b in boards] == ["MDB", "DB-1"]
    looped = client.post(
        "/design/project",
        json={
            "info": {"name": "Tower"},
            "boards": [{"name": "MDB", "loads": loads, "fed_from": "MDB"}],
        },
    )
    assert looped.status_code == 422


def test_the_design_routes_share_one_per_address_budget(client: TestClient) -> None:
    from app.domain.rate_limit import DESIGN_POLICY

    for _ in range(DESIGN_POLICY.limit):
        assert client.post("/design/distribution-board", json=BOARD).status_code == 200
    refused = client.post("/design/plc", json={"project": {}})
    assert refused.status_code == 429


def test_project_routes_call_the_domain(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.domain import design_projects
    from app.models.schemas.design import ProjectPage, ProjectSummary, SavedProject

    saved = SavedProject.model_validate(
        {
            "id": "p1",
            "name": "Pocket",
            "revisions": [{"number": 1, "note": "", "author": "e", "created_at": "now"}],
            "revision": 1,
            "request": {"info": {"name": "Pocket"}, "boards": [BOARD["board"]]},
        }
    )
    calls: list[str] = []

    def record(name: str, result: object):  # type: ignore[no-untyped-def]
        def call(**kwargs: object) -> object:
            calls.append(name)
            return result

        return call

    monkeypatch.setattr(design_projects, "save_project", record("save", saved))
    monkeypatch.setattr(design_projects, "revise_project", record("revise", saved))
    monkeypatch.setattr(design_projects, "open_project", record("open", saved))
    monkeypatch.setattr(design_projects, "delete_project", record("delete", None))
    monkeypatch.setattr(design_projects, "approve_revision", record("approve", saved))
    page = ProjectPage(
        projects=[ProjectSummary(id="p1", name="Pocket", revisions=1, updated_at="now")]
    )
    monkeypatch.setattr(design_projects, "list_projects", record("list", page))

    body = {"name": "Pocket", "request": {"info": {"name": "Pocket"}, "boards": [BOARD["board"]]}}
    assert client.post("/design/projects", json=body).status_code == 201
    assert client.get("/design/projects").json()["projects"][0]["id"] == "p1"
    assert client.get("/design/projects/p1?revision=1").json()["revision"] == 1
    revision = {"request": body["request"], "note": "more"}
    assert client.post("/design/projects/p1/revisions", json=revision).status_code == 201
    approval = client.post("/design/projects/p1/revisions/1/approval", json={"approver": "A"})
    assert approval.status_code == 200
    assert (
        client.post("/design/projects/p1/revisions/0/approval", json={"approver": "A"}).status_code
        == 422
    )
    assert client.delete("/design/projects/p1").status_code == 204
    assert calls == ["save", "list", "open", "revise", "approve", "delete"]


def test_a_project_needs_a_name(client: TestClient) -> None:
    body = {"name": "", "request": {"info": {"name": "Pocket"}, "boards": [BOARD["board"]]}}
    assert client.post("/design/projects", json=body).status_code == 422


def test_markups_are_read_from_an_upload(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.domain import design as design_domain
    from app.models.schemas.design import MarkupReport

    seen: dict[str, object] = {}

    def read(**kwargs: object) -> MarkupReport:
        seen.update(kwargs)
        return MarkupReport(markups=[], matched=False)

    monkeypatch.setattr(design_domain, "read_markups", read)
    response = client.post(
        "/design/markups",
        files={"file": ("set.pdf", b"%PDF", "application/pdf")},
        data={"project": "{}", "profile": '{"key": "acme"}'},
    )
    assert response.status_code == 200
    assert response.json() == {"markups": [], "matched": False}
    assert seen["data"] == b"%PDF"
    assert seen["project_json"] == "{}"
    assert seen["profile_json"] == '{"key": "acme"}'


def test_company_settings_routes_call_the_domain(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.domain import company_settings
    from app.models.schemas.design import CompanySettings

    saved = CompanySettings(settings={"key": "acme"}, updated_by="e", updated_at="now")
    seen: list[object] = []
    monkeypatch.setattr(company_settings, "get_settings", lambda **_: saved)

    def save(**kwargs: object) -> CompanySettings:
        seen.append(kwargs["settings"])
        return saved

    monkeypatch.setattr(company_settings, "save_settings", save)
    assert client.get("/design/company-settings").json()["settings"] == {"key": "acme"}
    response = client.put("/design/company-settings", json={"settings": {"key": "acme"}})
    assert response.status_code == 200
    assert seen == [{"key": "acme"}]
    assert client.put("/design/company-settings", json={}).status_code == 422


def test_the_markets_are_listed(client: TestClient) -> None:
    response = client.get("/design/markets")
    assert response.status_code == 200
    codes = {m["code"]: m for m in response.json()}
    assert codes["PS"]["currency"] == "ILS"
    assert codes["SA"]["frequency_hz"] == 60

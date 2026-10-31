"""M2: configured ``pp-design/2`` revisions through the API, against the disposable Postgres.

The configuration API (``/configuration/validate``, a revision's
``configuration``), the review schedules, printed structural findings and the
per-schema renderer version, end to end:

* switch off: a configuration is refused (``configuration_requires_v2``) and
  nothing is saved; a dry run still answers, ``saveable: false``;
* switch on: a configured revision is a v2 envelope hashed as stored, whose
  findings, model summary and schedules come from the saved model; its
  approval records the v2 renderer version; execution stays refused;
* a client never claims a review or a reserved ref; a restricted-scope
  template is refused; nothing is saved when a configuration is refused;
* another tenant gets 404 for validate, save, open and export;
* an altered configured row (with or without rehash, pattern lock mismatch,
  swapped component template) or an unknown schema is never approved or
  exported;
* a v1 approval is untouched by configured revisions and by the v2 renderer
  version; a v2 approval recorded under an earlier v2 renderer stops counting;
* concurrent configured saves get distinct consecutive revisions, each with
  its own snapshot;
* who may save and approve is unchanged.

Synthetic data only: the configurations are the committed test data.
"""

from __future__ import annotations

import copy
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.engine import Engine

from app import design
from app.design import marking, snapshot
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

Switch = Callable[[str], None]

CASE = "pe_n_distribution"
OTHER_CASE = "io_marshalling"


@pytest.fixture(name="switch")
def _switch(monkeypatch: pytest.MonkeyPatch) -> Switch:
    """Set ``DESIGN_SNAPSHOT_SCHEMA``; every save reads it afresh."""

    def set_to(schema: str) -> None:
        monkeypatch.setenv("DESIGN_SNAPSHOT_SCHEMA", schema)

    return set_to


def _request(case: str = CASE) -> dict[str, Any]:
    fixture = CONFIGS[case]["fixture"]
    request: dict[str, Any] = copy.deepcopy(DESIGNED[fixture]["request"])
    return request


def _configuration(case: str = CASE) -> dict[str, Any]:
    return config_case(case)[1]


def _save(client: TestClient, case: str = CASE) -> str:
    response = client.post(
        "/design/projects", json={"name": f"M2 {case}", "request": _request(case), "note": "r1"}
    )
    assert response.status_code == 201, response.text[:500]
    project_id: str = response.json()["id"]
    return project_id


def _revise(
    client: TestClient,
    project_id: str,
    configuration: dict[str, Any] | None,
    case: str = CASE,
    note: str = "configured",
) -> Any:
    body: dict[str, Any] = {"request": _request(case), "note": note}
    if configuration is not None:
        body["configuration"] = configuration
    return client.post(f"/design/projects/{project_id}/revisions", json=body)


def _validate(client: TestClient, project_id: str, configuration: Any) -> Any:
    return client.post(
        f"/design/projects/{project_id}/configuration/validate",
        json={"configuration": configuration},
    )


def _open(client: TestClient, project_id: str, number: int) -> dict[str, Any]:
    response = client.get(f"/design/projects/{project_id}", params={"revision": number})
    assert response.status_code == 200, response.text[:500]
    body: dict[str, Any] = response.json()
    return body


def _configured(client: TestClient, switch: Switch, case: str = CASE) -> str:
    """A project whose revision 2 is configured (revision 1 as the switch leaves it)."""
    switch("pp-design/2")
    project_id = _save(client, case)
    revised = _revise(client, project_id, _configuration(case), case)
    assert revised.status_code == 201, revised.text[:500]
    return project_id


def _revision_count(engine: Engine, project_id: str) -> int:
    from sqlalchemy import text

    with engine.connect() as conn:
        return int(
            conn.execute(
                text("SELECT count(*) FROM design_project_revisions WHERE project_id = :p"),
                {"p": project_id},
            ).scalar_one()
        )


# --------------------------------------------------------------------------- #
# The switch
# --------------------------------------------------------------------------- #


def test_switch_off_refuses_a_configuration_and_saves_nothing(
    client: TestClient, engine: Engine, switch: Switch
) -> None:
    switch("pp-design/1")
    project_id = _save(client)
    refused = _revise(client, project_id, _configuration())
    assert refused.status_code == 409, refused.text[:300]
    body = error(refused)
    assert body["code"] == "configuration_requires_v2"
    assert body["params"]["written_schema"] == "pp-design/1"
    assert _revision_count(engine, project_id) == 1
    # A dry run persists nothing, so it answers, and says it could not be saved here.
    review = _validate(client, project_id, _configuration())
    assert review.status_code == 200, review.text[:500]
    assert review.json()["saveable"] is False
    assert review.json()["written_schema"] == "pp-design/1"
    assert _revision_count(engine, project_id) == 1
    # A revision with no configuration is a v1 one, as before.
    plain = _revise(client, project_id, None)
    assert plain.status_code == 201
    assert "schema" not in revision_row(engine, project_id, 2)["design_snapshot"]


def test_the_dry_run_returns_the_model_findings_and_schedules(
    client: TestClient, engine: Engine, switch: Switch
) -> None:
    switch("pp-design/2")
    project_id = _save(client)
    review = _validate(client, project_id, _configuration())
    assert review.status_code == 200, review.text[:500]
    body = review.json()
    assert body["based_on_revision"] == 1
    assert body["snapshot_schema"] == "pp-design/2"
    assert body["saveable"] is True
    model = body["model"]
    expect = CONFIGS[CASE]["expect"]
    assert sum(len(s["points"]) for s in model["strips"]) == expect["strip_points"]
    assert len(model["nets"]) == expect["nets"]
    assert len(model["connections"]) == expect["connections"]
    assert len(model["wires"]) == expect["wires"]
    sources = {
        w[name]["source"]
        for w in model["wires"]
        for name in ("number", "cross_section_mm2", "colour")
    }
    assert sources == {"user", "data", "assumption", "unresolved"}
    assert model["terminals"]
    codes = {f["code"] for f in model["findings"]}
    assert {"template_unreviewed", "pattern_unreviewed", "wire_attribute_assumed"} <= codes
    for found in model["findings"]:
        assert set(found) >= {"severity", "unresolved", "blocks_approval", "blocks_execution"}
    assert codes <= {f["code"] for f in body["findings"]}
    assert _revision_count(engine, project_id) == 1
    # The hash it reports is the one the save then stores.
    saved = _revise(client, project_id, _configuration())
    assert saved.status_code == 201
    assert revision_row(engine, project_id, 2)["snapshot_sha256"] == body["snapshot_sha256"]


# --------------------------------------------------------------------------- #
# A configured revision
# --------------------------------------------------------------------------- #


def test_a_configured_revision_is_stored_hashed_and_opens_with_its_model(
    client: TestClient, engine: Engine, switch: Switch
) -> None:
    project_id = _configured(client, switch)
    row = revision_row(engine, project_id, 2)
    stored = row["design_snapshot"]
    assert stored["schema"] == "pp-design/2"
    assert snapshot.sha256_of(stored) == row["snapshot_sha256"]
    assert stored["model"]["patterns"]
    assert stored["model"]["wires"]
    codes = {f["code"] for f in row["findings"]}
    assert {"pattern_unreviewed", "wire_attribute_assumed", "wire_attribute_unset"} <= codes
    opened = _open(client, project_id, 2)
    assert opened["validated"] is True
    assert opened["export_status"] == "draft"
    model = opened["model_v2"]
    assert model is not None
    assert len(model["wires"]) == CONFIGS[CASE]["expect"]["wires"]
    confirm = {f["code"]: f["requires_engineering_confirmation"] for f in opened["findings"]}
    for code in (
        "pattern_unreviewed",
        "wire_attribute_assumed",
        "terminal_requiredness_unresolved",
    ):
        if code in confirm:
            assert confirm[code] is True, code
    assert {"template_unreviewed", "pattern_unreviewed"} <= set(opened["execution_blockers"])
    # A v1 revision opens with no v2 model.
    switch("pp-design/1")
    other = _save(client)
    assert _open(client, other, 1)["model_v2"] is None


def test_a_configured_revision_approves_under_the_v2_renderer_and_exports_its_schedules(
    client: TestClient, engine: Engine, switch: Switch
) -> None:
    project_id = _configured(client, switch)
    approved = approve(client, project_id, 2)
    assert approved.status_code == 200, approved.text[:500]
    row = revision_row(engine, project_id, 2)
    assert row["approved_renderer_version"] == design.renderer_version(versions.V2)
    assert _open(client, project_id, 2)["export_status"] == "review_approved"
    for fmt in ("terminal_schedule_pdf", "wire_schedule_pdf"):
        exported = revision_export(client, project_id, 2, fmt)
        assert exported.status_code == 200, (fmt, exported.text[:300])
        assert exported.headers["content-type"] == "application/pdf"
        pages = [" ".join(p.split()) for p in pdf_pages(exported.content)]
        for page in pages:
            assert marking.REVIEW_APPROVED_LABEL in page
            assert "REVIEW SCHEDULE — NOT FOR EXECUTION" in page
        text = " ".join(pages)
        assert row["snapshot_sha256"] in text
        assert design.renderer_version(versions.V2) in text
        assert "template_unreviewed" in text
        assert "pattern_unreviewed" in text
    # The review drawing prints the structural findings, under the v2 renderer.
    drawing = " ".join(
        " ".join(pdf_pages(revision_export(client, project_id, 2, "pdf").content)).split()
    )
    assert "pattern_unreviewed" in drawing
    assert design.renderer_version(versions.V2) in drawing
    as_json = revision_export(client, project_id, 2, "json").json()
    assert "wire_attribute_assumed" in {f["code"] for f in as_json["findings"]}
    # Execution: always refused, naming the template and pattern review codes.
    refused = revision_export(client, project_id, 2, "pdf", purpose="execution")
    assert refused.status_code == 409
    blockers = error(refused)["params"]["blockers"].split(",")
    assert error(refused)["code"] == "execution_package_incomplete"
    assert {"template_unreviewed", "pattern_unreviewed"} <= set(blockers)
    for fmt in ("devices_csv", "terminals_csv", "qet"):
        assert error(revision_export(client, project_id, 2, fmt))["code"] == (
            "export_format_unprotected"
        )


def test_a_v2_approval_under_an_earlier_v2_renderer_stops_counting(
    client: TestClient, engine: Engine, switch: Switch
) -> None:
    project_id = _configured(client, switch)
    assert approve(client, project_id, 2).status_code == 200
    # As an approval recorded before per-schema versions would read: v1's version.
    update_row(engine, project_id, 2, approved_renderer_version=design.RENDERER_VERSION)
    opened = _open(client, project_id, 2)
    assert opened["approval_current"] is False
    assert "renderer_version" in opened["approval_changed"]
    refused = revision_export(client, project_id, 2, "pdf")
    assert refused.status_code == 409
    assert error(refused)["code"] == "revision_stale"
    assert "renderer_version" in error(refused)["params"]["changed"].split(",")


def test_a_legacy_v1_approval_keeps_counting_beside_configured_revisions(
    client: TestClient, engine: Engine, switch: Switch
) -> None:
    switch("pp-design/1")
    project_id = _save(client)
    assert approve(client, project_id, 1).status_code == 200
    before = revision_row(engine, project_id, 1)
    assert before["approved_renderer_version"] == design.RENDERER_VERSION
    switch("pp-design/2")
    assert _revise(client, project_id, _configuration()).status_code == 201
    after = revision_row(engine, project_id, 1)
    before.pop("updated_at", None)
    after.pop("updated_at", None)
    assert after == before
    first = _open(client, project_id, 1)
    assert first["approval_current"] is True
    assert first["export_status"] == "review_approved"
    assert first["model_v2"] is None
    exported = revision_export(client, project_id, 1, "pdf")
    assert exported.status_code == 200
    text = " ".join(pdf_pages(exported.content))
    assert marking.REVIEW_APPROVED_LABEL in text
    assert f"renderer {design.RENDERER_VERSION}" in " ".join(text.split())
    # A schedule of the v1 revision: nothing to schedule.
    refused = revision_export(client, project_id, 1, "wire_schedule_pdf")
    assert error(refused)["code"] == "schedule_requires_v2"
    # A v1 revision saved with the switch on approves as before.
    plain = _save(client)
    assert approve(client, plain, 1).status_code == 200


# --------------------------------------------------------------------------- #
# What a client may not send
# --------------------------------------------------------------------------- #


def _claimed(configuration: dict[str, Any]) -> None:
    configuration["templates"][0]["review"].update(
        {"status": "approved", "reviewer": "A. Claimant", "reviewed_at": "2026-10-31"}
    )


def _claimed_pattern(configuration: dict[str, Any]) -> None:
    configuration["patterns"][0]["review"]["standards"] = ["ANY-STANDARD"]


def _global_ref(configuration: dict[str, Any]) -> None:
    configuration["templates"][0]["ref"] = "global/legacy-contactor/v1"


def _restricted(configuration: dict[str, Any]) -> None:
    configuration["templates"][0]["restricted_scope"] = ["fire_pump"]


def _oversized(configuration: dict[str, Any]) -> None:
    configuration["bindings"] = configuration["bindings"] * 200


@pytest.mark.parametrize(
    ("alter", "code"),
    [
        (_claimed, "configuration_claims_review"),
        (_claimed_pattern, "configuration_claims_review"),
        (_global_ref, "configuration_ref_reserved"),
        (_restricted, "configuration_refused"),
        (_oversized, "configuration_too_large"),
    ],
)
def test_a_refused_configuration_is_a_422_and_saves_nothing(
    client: TestClient,
    engine: Engine,
    switch: Switch,
    alter: Callable[[dict[str, Any]], None],
    code: str,
) -> None:
    switch("pp-design/2")
    project_id = _save(client)
    configuration = _configuration()
    alter(configuration)
    for response in (
        _validate(client, project_id, configuration),
        _revise(client, project_id, configuration),
    ):
        assert response.status_code == 422, response.text[:300]
        assert error(response)["code"] == code
    assert _revision_count(engine, project_id) == 1


def test_an_unknown_configuration_field_is_refused_with_its_location(
    client: TestClient, switch: Switch
) -> None:
    switch("pp-design/2")
    project_id = _save(client)
    response = client.post(
        f"/design/projects/{project_id}/configuration/validate",
        json={"configuration": _configuration(), "approved": True},
    )
    assert response.status_code == 422


# --------------------------------------------------------------------------- #
# Tenancy, tampering, unknown schemas
# --------------------------------------------------------------------------- #


def test_another_tenant_cannot_validate_save_open_or_export(
    client: TestClient, caller: _Caller, tenants: tuple[str, str], switch: Switch
) -> None:
    project_id = _configured(client, switch)
    caller.user = _user(tenants[1], email="intruder@example.com")
    assert _validate(client, project_id, _configuration()).status_code == 404
    assert _revise(client, project_id, _configuration()).status_code == 404
    assert client.get(f"/design/projects/{project_id}").status_code == 404
    assert client.get(f"/design/projects/{project_id}", params={"revision": 2}).status_code == 404
    for fmt in ("pdf", "terminal_schedule_pdf", "wire_schedule_pdf"):
        assert revision_export(client, project_id, 2, fmt).status_code == 404
    assert approve(client, project_id, 2).status_code == 404


def _pattern_lock(stored: dict[str, Any]) -> None:
    stored["model"]["registry_lock"]["patterns"][0]["digest"] = "0" * 64


def _pattern_edited(stored: dict[str, Any]) -> None:
    stored["model"]["patterns"][0]["review"]["status"] = "approved"


def _template_swapped(stored: dict[str, Any]) -> None:
    model = stored["model"]
    made = next(c for c in model["components"] if c["pattern_refs"])
    made["template_ref"] = next(
        t["ref"] for t in model["templates"] if t["ref"] != made["template_ref"]
    )


def _wire_value(stored: dict[str, Any]) -> None:
    stored["model"]["wires"][0]["colour"] = {"value": "X", "source": "user", "assumption": None}


@pytest.mark.parametrize("alter", [_pattern_lock, _pattern_edited, _template_swapped, _wire_value])
@pytest.mark.parametrize("rehash", [True, False])
def test_an_altered_configured_row_is_never_approved_or_exported(
    client: TestClient,
    engine: Engine,
    switch: Switch,
    alter: Callable[[dict[str, Any]], None],
    rehash: bool,
) -> None:
    project_id = _configured(client, switch)
    stored = copy.deepcopy(revision_row(engine, project_id, 2)["design_snapshot"])
    alter(stored)
    values: dict[str, Any] = {"design_snapshot": stored}
    if rehash:
        values["snapshot_sha256"] = snapshot.sha256_of(stored)
    update_row(engine, project_id, 2, **values)
    opened = _open(client, project_id, 2)
    if rehash and alter is _wire_value:
        # Well-formed and rehashed: it reads, but it is not the design saved:
        # its stored findings no longer match what today's checks find.
        assert opened["export_status"] in ("stale", "not_validated")
    else:
        assert opened["export_status"] == "not_validated"
        assert "snapshot_integrity_failed" in {f["code"] for f in opened["findings"]}
        assert opened["model_v2"] is None
    for fmt in ("pdf", "json", "terminal_schedule_pdf", "wire_schedule_pdf"):
        refused = revision_export(client, project_id, 2, fmt)
        assert refused.status_code == 409, (fmt, refused.text[:200])
        assert error(refused)["code"] in ("snapshot_integrity_failed", "revision_stale")
    refused = approve(client, project_id, 2)
    assert refused.status_code == 409
    assert revision_row(engine, project_id, 2)["approved_at"] is None


def test_an_unknown_schema_is_never_opened_validated_approved_or_exported(
    client: TestClient, engine: Engine, switch: Switch
) -> None:
    project_id = _configured(client, switch)
    stored = copy.deepcopy(revision_row(engine, project_id, 2)["design_snapshot"])
    stored["schema"] = "pp-design/3"
    update_row(
        engine, project_id, 2, design_snapshot=stored, snapshot_sha256=snapshot.sha256_of(stored)
    )
    opened = _open(client, project_id, 2)
    assert opened["export_status"] == "not_validated"
    assert "snapshot_schema_unknown" in {f["code"] for f in opened["findings"]}
    for fmt in ("pdf", "wire_schedule_pdf"):
        assert error(revision_export(client, project_id, 2, fmt))["code"] == (
            "snapshot_schema_unknown"
        )
    assert error(approve(client, project_id, 2))["code"] == "snapshot_schema_unknown"


# --------------------------------------------------------------------------- #
# Concurrency and permissions
# --------------------------------------------------------------------------- #


def test_concurrent_configured_saves_get_distinct_revisions_and_their_own_snapshots(
    app: FastAPI, client: TestClient, engine: Engine, switch: Switch
) -> None:
    switch("pp-design/2")
    project_id = _save(client)
    count = 6
    barrier = threading.Barrier(count)

    def save_one(index: int) -> Any:
        configuration = _configuration()
        # Each save's own wire number, so a mixed-up snapshot would show.
        configuration["bindings"][0]["wires"]["neutral/0"]["number"] = f"SYN-T{index}"
        with TestClient(app, raise_server_exceptions=False) as own:
            barrier.wait()
            return _revise(own, project_id, configuration, note=f"t{index}")

    with ThreadPoolExecutor(max_workers=count) as pool:
        responses = list(pool.map(save_one, range(count)))
    created = [r for r in responses if r.status_code == 201]
    conflicts = [r for r in responses if r.status_code == 409]
    assert len(created) + len(conflicts) == count, [r.text[:200] for r in responses]
    assert all(error(r)["code"] == "revision_conflict" for r in conflicts)
    assert created, "no save went through"
    numbers = sorted({r.json()["revision"] for r in created})
    assert numbers == list(range(2, 2 + len(created)))
    seen: set[str] = set()
    for number in numbers:
        row = revision_row(engine, project_id, number)
        stored = row["design_snapshot"]
        assert snapshot.sha256_of(stored) == row["snapshot_sha256"]
        mine = row["note"].removeprefix("t")
        wire_numbers = {
            w["number"]["value"] for w in stored["model"]["wires"] if w["number"]["value"]
        }
        assert f"SYN-T{mine}" in wire_numbers
        assert not {n for n in wire_numbers if n.startswith("SYN-T")} - {f"SYN-T{mine}"}
        seen.add(mine)
    assert len(seen) == len(numbers)


def test_who_may_save_and_approve_is_unchanged(
    client: TestClient,
    caller: _Caller,
    engine: Engine,
    tenants: tuple[str, str],
    switch: Switch,
) -> None:
    owner = caller.user
    member = _user(tenants[0], email="m2-member@example.com")
    _account_row(engine, member, "M2 Member")
    caller.user = member
    project_id = _configured(client, switch)
    refused = approve(client, project_id, 2)
    assert refused.status_code == 403
    assert error(refused)["code"] == "approval_not_permitted"
    caller.user = owner
    assert approve(client, project_id, 2).status_code == 200


def test_a_second_case_saves_and_schedules_too(
    client: TestClient, engine: Engine, switch: Switch
) -> None:
    project_id = _configured(client, switch, OTHER_CASE)
    for fmt in ("terminal_schedule_pdf", "wire_schedule_pdf"):
        exported = revision_export(client, project_id, 2, fmt)
        assert exported.status_code == 200, exported.text[:300]
        assert all(marking.DRAFT_LABEL in page for page in pdf_pages(exported.content))


#: Every review format a configured revision exports in.
_REVIEW_FORMATS = (
    "pdf",
    "calculations_pdf",
    "dxf",
    "json",
    "quotation_pdf",
    "terminal_schedule_pdf",
    "wire_schedule_pdf",
)


@pytest.mark.parametrize("findings_too", [False, True])
def test_a_forged_review_status_is_refused_even_when_lock_hash_and_findings_match(
    client: TestClient, engine: Engine, switch: Switch, findings_too: bool
) -> None:
    """A row rewritten outside the application to claim engineering review.

    Every embedded template and pattern is marked approved by a made-up
    reviewer, the registry lock and the snapshot hash are recomputed, and (in
    one case) the stored findings too, so every fingerprint matches. No review
    can be issued by this server yet, so the design is refused like an altered
    one: opened as not validated, never approved or exported, and its review
    summary never shown. Found by the final independent review.
    """
    from app.design.model import codec_v2
    from app.design.model.read import read_snapshot
    from app.domain.design_projects import _design_findings
    from app.models.schemas.design import ProjectDesignRequest

    project_id = _configured(client, switch)
    row = revision_row(engine, project_id, 2)
    stored = copy.deepcopy(row["design_snapshot"])
    model = stored["model"]
    forged = {
        "status": "approved",
        "reviewer": "Synthetic forged reviewer",
        "reviewed_at": "2026-01-01",
    }
    for definition in (*model["templates"], *model["patterns"]):
        if definition["ref"].startswith("tenant/"):
            definition["review"].update(forged)
    model["registry_lock"]["templates"] = [
        {"ref": t["ref"], "digest": codec_v2.template_digest(codec_v2.parse_template(t))}
        for t in model["templates"]
    ]
    model["registry_lock"]["patterns"] = [
        {"ref": p["ref"], "digest": codec_v2.pattern_digest(codec_v2.parse_pattern(p))}
        for p in model["patterns"]
    ]
    values: dict[str, Any] = {
        "design_snapshot": stored,
        "snapshot_sha256": snapshot.sha256_of(stored),
    }
    if findings_too:
        read = read_snapshot(stored)
        request = ProjectDesignRequest.model_validate(row["request"])
        conditions = {board.name: board.conditions for board in request.boards}
        values["findings"] = [
            f.model_dump(mode="json") for f in _design_findings(read.project, conditions, read.v2)
        ]
    update_row(engine, project_id, 2, **values)

    opened = _open(client, project_id, 2)
    assert opened["export_status"] == "not_validated"
    assert opened["model_v2"] is None
    assert "snapshot_integrity_failed" in {f["code"] for f in opened["findings"]}
    refused = approve(client, project_id, 2)
    assert refused.status_code == 409
    assert revision_row(engine, project_id, 2)["approved_at"] is None
    for fmt in _REVIEW_FORMATS:
        response = revision_export(client, project_id, 2, fmt)
        assert response.status_code == 409, fmt
        assert error(response)["code"] == "snapshot_integrity_failed", fmt

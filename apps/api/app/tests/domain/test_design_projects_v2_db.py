"""M2a integration: writing ``pp-design/2`` revisions behind ``DESIGN_SNAPSHOT_SCHEMA``.

Drives the real routes against the disposable Postgres (``TEST_DATABASE_URL``),
with the fixtures of the Phase 1 export-integrity suite. The invariants:

* switch off (the default): a save is a bare v1 snapshot, exactly as before;
* switch on: a save is a v2 envelope, hashed as stored, that opens, approves
  and exports with the same content as the v1 export of the same design;
  execution is refused naming ``template_unreviewed``; restricted formats
  stay refused; the quotation stays a provisional estimate;
* a v1 approved revision followed by a v2 one: the v1 row is untouched and
  its approval still counts;
* another tenant gets 404; an altered v2 row is never approved or exported.

Each test sets the switch itself, so the module passes whatever the ambient
``DESIGN_SNAPSHOT_SCHEMA`` is.
"""

from __future__ import annotations

import copy
from collections.abc import Callable, Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.engine import Engine

from app.core.config import get_settings
from app.design import marking, snapshot
from app.domain import design_projects
from app.tests.api.v1.routes.test_export_integrity import (  # noqa: F401  (fixtures)
    REQUEST,
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
    revise,
    revision_export,
    revision_row,
    save,
    update_row,
)
from app.tests.design import render_content

Switch = Callable[[str], None]


@pytest.fixture(name="switch")
def _switch(monkeypatch: pytest.MonkeyPatch) -> Iterator[Switch]:
    """Set ``DESIGN_SNAPSHOT_SCHEMA`` as a deployment would, through the settings."""

    def set_to(schema: str) -> None:
        monkeypatch.setenv("DESIGN_SNAPSHOT_SCHEMA", schema)
        get_settings.cache_clear()

    yield set_to
    get_settings.cache_clear()


def _opened(client: TestClient, project_id: str, number: int) -> dict[str, Any]:
    response = client.get(f"/design/projects/{project_id}", params={"revision": number})
    assert response.status_code == 200, response.text[:500]
    body: dict[str, Any] = response.json()
    return body


CONTENT: dict[str, Callable[[bytes], Any]] = {
    "pdf": render_content.pdf_content,
    "calculations_pdf": render_content.pdf_content,
    "dxf": render_content.dxf_content,
    "json": render_content.json_content,
}


# --------------------------------------------------------------------------- #
# Switch off: exactly as before
# --------------------------------------------------------------------------- #


def test_switch_off_writes_a_bare_v1_snapshot(
    client: TestClient, engine: Engine, switch: Switch
) -> None:
    switch("pp-design/1")
    saved = save(client)
    row = revision_row(engine, saved["id"], 1)
    assert "schema" not in row["design_snapshot"]
    assert snapshot.sha256_of(row["design_snapshot"]) == row["snapshot_sha256"]
    codes = {found["code"] for found in row["findings"]}
    assert not codes & {"template_unreviewed", "out_of_execution_scope", "wire_attribute_unset"}
    opened = _opened(client, saved["id"], 1)
    assert "template_unreviewed" not in opened["execution_blockers"]


# --------------------------------------------------------------------------- #
# Switch on
# --------------------------------------------------------------------------- #


def test_switch_on_saves_a_v2_envelope_that_opens_validated(
    client: TestClient, engine: Engine, switch: Switch
) -> None:
    switch("pp-design/2")
    saved = save(client)
    row = revision_row(engine, saved["id"], 1)
    stored = row["design_snapshot"]
    assert set(stored) == {"schema", "model"}
    assert stored["schema"] == "pp-design/2"
    # The hash is of the stored envelope, marker included.
    assert snapshot.sha256_of(stored) == row["snapshot_sha256"]
    opened = _opened(client, saved["id"], 1)
    assert opened["validated"] is True
    assert opened["export_status"] == "draft"
    unreviewed = [f for f in opened["findings"] if f["code"] == "template_unreviewed"]
    assert unreviewed
    assert {f["severity"] for f in unreviewed} == {"warning"}
    assert all(f["requires_engineering_confirmation"] for f in unreviewed)
    assert all(f["source"] == "integrity" for f in unreviewed)
    assert "template_unreviewed" in opened["execution_blockers"]


def test_v2_revision_approves_and_exports_the_same_content_as_v1(
    client: TestClient, engine: Engine, switch: Switch, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The identity line prints the snapshot's hash, which differs between the
    # two schemas by design; printed empty, everything else must be identical.
    monkeypatch.setattr(design_projects, "SHORT_HASH", 0)
    switch("pp-design/1")
    v1 = save(client)
    switch("pp-design/2")
    v2 = save(client)
    assert revision_row(engine, v2["id"], 1)["design_snapshot"]["schema"] == "pp-design/2"
    for project in (v1, v2):
        assert approve(client, project["id"], 1).status_code == 200
    assert _opened(client, v2["id"], 1)["export_status"] == "review_approved"
    for fmt, content in CONTENT.items():
        first = revision_export(client, v1["id"], 1, fmt)
        second = revision_export(client, v2["id"], 1, fmt)
        assert first.status_code == 200, (fmt, first.text[:300])
        assert second.status_code == 200, (fmt, second.text[:300])
        assert content(first.content) == content(second.content), fmt
    # The approval binding holds for v2 exactly as for v1.
    row = revision_row(engine, v2["id"], 1)
    assert row["approved_snapshot_sha256"] == row["snapshot_sha256"]
    assert row["approved_findings_policy"] is not None
    assert _opened(client, v2["id"], 1)["approval_current"] is True


def test_v2_execution_is_refused_naming_template_unreviewed(
    client: TestClient, switch: Switch
) -> None:
    switch("pp-design/2")
    saved = save(client)
    assert approve(client, saved["id"], 1).status_code == 200
    refused = revision_export(client, saved["id"], 1, "json", purpose="execution")
    assert refused.status_code == 409
    body = error(refused)
    assert body["code"] == "execution_package_incomplete"
    assert body["params"]["approval"] == "present"
    assert "template_unreviewed" in body["params"]["blockers"].split(",")


@pytest.mark.parametrize("fmt", ["devices_csv", "qet", "aml", "plc_st"])
def test_v2_restricted_formats_stay_refused(client: TestClient, switch: Switch, fmt: str) -> None:
    switch("pp-design/2")
    saved = save(client)
    refused = revision_export(client, saved["id"], 1, fmt)
    assert refused.status_code == 409, refused.text[:300]
    assert error(refused)["code"] == "export_format_unprotected"


def test_v2_quotation_stays_a_provisional_estimate(client: TestClient, switch: Switch) -> None:
    switch("pp-design/2")
    saved = save(client)
    assert approve(client, saved["id"], 1).status_code == 200
    quoted = revision_export(client, saved["id"], 1, "quotation_pdf")
    assert quoted.status_code == 200, quoted.text[:300]
    text = " ".join(" ".join(pdf_pages(quoted.content)).split())
    assert "PROVISIONAL ESTIMATE" in text
    assert marking.REVIEW_APPROVED_LABEL not in text


def test_an_unconnected_required_terminal_blocks_approval(
    client: TestClient, engine: Engine, switch: Switch
) -> None:
    """The mapping, end to end: a required terminal in no net is a blocking finding."""
    switch("pp-design/2")
    saved = save(client)
    row = revision_row(engine, saved["id"], 1)
    findings = [
        *row["findings"],
        {
            "code": "unconnected_required_terminal",
            "severity": "blocking",
            "message": "required terminal 1 is in no net",
            "requires_engineering_confirmation": False,
            "source": "integrity",
        },
    ]
    update_row(engine, saved["id"], 1, findings=findings)
    refused = approve(client, saved["id"], 1)
    assert refused.status_code == 409
    assert error(refused)["code"] in ("revision_has_blocking_findings", "revision_stale")


# --------------------------------------------------------------------------- #
# History is never rewritten
# --------------------------------------------------------------------------- #


def test_a_v2_revision_after_an_approved_v1_leaves_the_v1_row_alone(
    client: TestClient, engine: Engine, switch: Switch
) -> None:
    switch("pp-design/1")
    saved = save(client)
    assert approve(client, saved["id"], 1).status_code == 200
    before = revision_row(engine, saved["id"], 1)
    switch("pp-design/2")
    revised = revise(client, saved["id"])
    assert revised.status_code == 201, revised.text[:300]
    after = revision_row(engine, saved["id"], 1)
    before.pop("updated_at", None)
    after.pop("updated_at", None)
    assert after == before
    assert "schema" not in after["design_snapshot"]
    assert revision_row(engine, saved["id"], 2)["design_snapshot"]["schema"] == "pp-design/2"
    first = _opened(client, saved["id"], 1)
    assert first["approval_current"] is True
    assert first["export_status"] == "review_approved"
    latest = _opened(client, saved["id"], 2)
    assert [r["number"] for r in latest["revisions"]] == [1, 2]
    exported = revision_export(client, saved["id"], 1, "pdf")
    assert exported.status_code == 200
    assert marking.REVIEW_APPROVED_LABEL in " ".join(pdf_pages(exported.content))


# --------------------------------------------------------------------------- #
# Tenancy and tampering
# --------------------------------------------------------------------------- #


def test_another_tenant_cannot_see_a_v2_revision(
    client: TestClient, caller: _Caller, tenants: tuple[str, str], switch: Switch
) -> None:
    switch("pp-design/2")
    saved = save(client)
    caller.user = _user(tenants[1], email="intruder@example.com")
    assert client.get(f"/design/projects/{saved['id']}").status_code == 404
    assert revision_export(client, saved["id"], 1, "json").status_code == 404
    assert approve(client, saved["id"], 1).status_code == 404


def _lock_digest(stored: dict[str, Any]) -> None:
    lock = stored["model"]["registry_lock"]["templates"][0]
    lock["digest"] = "0" * 64


def _dangling(stored: dict[str, Any]) -> None:
    stored["model"]["components"][0]["template_ref"] = "global/no-such-template/v1"


def _extra_key(stored: dict[str, Any]) -> None:
    stored["note"] = "added outside PanelPilot"


@pytest.mark.parametrize("alter", [_lock_digest, _dangling, _extra_key])
@pytest.mark.parametrize("rehash", [True, False])
def test_an_altered_v2_row_is_never_approved_or_exported(
    client: TestClient,
    engine: Engine,
    switch: Switch,
    alter: Callable[[dict[str, Any]], None],
    rehash: bool,
) -> None:
    switch("pp-design/2")
    saved = save(client)
    stored = copy.deepcopy(revision_row(engine, saved["id"], 1)["design_snapshot"])
    alter(stored)
    values: dict[str, Any] = {"design_snapshot": stored}
    if rehash:
        values["snapshot_sha256"] = snapshot.sha256_of(stored)
    update_row(engine, saved["id"], 1, **values)
    opened = _opened(client, saved["id"], 1)
    assert opened["export_status"] == "not_validated"
    assert "snapshot_integrity_failed" in {f["code"] for f in opened["findings"]}
    for fmt in ("pdf", "json", "dxf"):
        refused = revision_export(client, saved["id"], 1, fmt)
        assert refused.status_code == 409
        assert error(refused)["code"] == "snapshot_integrity_failed"
    refused = approve(client, saved["id"], 1)
    assert refused.status_code == 409
    assert error(refused)["code"] == "snapshot_integrity_failed"
    assert revision_row(engine, saved["id"], 1)["approved_at"] is None

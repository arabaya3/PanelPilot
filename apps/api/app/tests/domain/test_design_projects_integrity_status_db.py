"""M2: what a revision that fails integrity reports, against the disposable Postgres.

For each case (an intact v1 and an intact v2 revision; a v1 and a v2
revision whose hash no longer matches; a v2 revision whose own net was edited
and its hash recomputed; one whose embedded pattern was edited and its hash
recomputed; a forged review; a genuine findings-policy and rules change), the
value of ``validated`` and ``export_status``, and the refusal code and reason,
on open, in the revision list, on approval and on every review export (and an
execution export, which stays ``execution_package_incomplete``). Every attempt
leaves the stored row as it was (every column but ``updated_at``), and the
older, approved revision keeps its approval and still exports.

The cause rule: a design whose hash verifies but whose recomputed findings
are other findings (other codes) than the stored ones, under the rules and
catalogue it records, is an integrity failure (``findings_mismatch``), never
``revision_stale``; only a reclassification of the same findings, or a
recorded version that differs from the current one, is stale.

Synthetic data only: the configurations are the committed test data.
"""

from __future__ import annotations

import copy
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.engine import Engine

from app.design import snapshot
from app.tests.api.v1.routes.test_export_integrity import (  # noqa: F401  (fixtures)
    _account_row,
    _app,
    _Caller,
    _caller,
    _change_policy,
    _client,
    _engine,
    _tenants,
    _user,
    approve,
    error,
    pytestmark,
    revision_export,
    revision_row,
    update_row,
)
from app.tests.domain.test_design_projects_configured_db import (
    CASE,
    _configuration,
    _open,
    _revise,
    _save,
)

#: Every review format, as the configured-revision tests list them.
FORMATS = (
    "pdf",
    "calculations_pdf",
    "dxf",
    "json",
    "quotation_pdf",
    "terminal_schedule_pdf",
    "wire_schedule_pdf",
)
SCHEDULES = {"terminal_schedule_pdf", "wire_schedule_pdf"}


def _net_signal(stored: dict[str, Any]) -> None:
    """Edit a net of the design's own ``model.nets`` (not a pattern's).

    The first net is given the second's signal, so the structural checks find
    a conflict the stored findings do not hold (as in the reported case).
    """
    nets = stored["model"]["nets"]
    nets[0]["signal"] = nets[1]["signal"]


def _net_renamed(stored: dict[str, Any]) -> None:
    """Rename a net's signal to one no check objects to: the findings stay the same."""
    stored["model"]["nets"][0]["signal"] += "-TAMPERED"


def _pattern_net(stored: dict[str, Any]) -> None:
    """Edit a net inside an embedded connection pattern."""
    for pattern in stored["model"]["patterns"]:
        for net in pattern.get("nets", []):
            if isinstance(net, dict) and isinstance(net.get("signal"), str):
                net["signal"] += "-TAMPERED"
                return
    raise AssertionError("no pattern net to edit")


def _v1_value(stored: dict[str, Any]) -> None:
    """Edit any string in a bare v1 snapshot."""
    stored["info"]["project_name"] = "Altered outside the application"


def _forge_review(stored: dict[str, Any]) -> None:
    from app.design.model import codec_v2

    model = stored["model"]
    for definition in (*model["templates"], *model["patterns"]):
        if definition["ref"].startswith("tenant/"):
            definition["review"].update(
                {"status": "approved", "reviewer": "Synthetic forged", "reviewed_at": "2026-01-01"}
            )
    model["registry_lock"]["templates"] = [
        {"ref": t["ref"], "digest": codec_v2.template_digest(codec_v2.parse_template(t))}
        for t in model["templates"]
    ]
    model["registry_lock"]["patterns"] = [
        {"ref": p["ref"], "digest": codec_v2.pattern_digest(codec_v2.parse_pattern(p))}
        for p in model["patterns"]
    ]


#: case: (schema written, alteration, rehash, policy change, expected)
#: expected: (validated, export_status, code, reason) for the altered revision.
CASES: dict[str, tuple[str, Any, bool, str | None, tuple[bool, str, str | None, str | None]]] = {
    "intact_v1": ("pp-design/1", None, False, None, (True, "draft", None, None)),
    "intact_v2": ("pp-design/2", None, False, None, (True, "draft", None, None)),
    "hash_tampered_v1": (
        "pp-design/1",
        _v1_value,
        False,
        None,
        (False, "not_validated", "snapshot_integrity_failed", "hash_mismatch"),
    ),
    "hash_tampered_v2": (
        "pp-design/2",
        _net_signal,
        False,
        None,
        (False, "not_validated", "snapshot_integrity_failed", "hash_mismatch"),
    ),
    "rehashed_net_edit": (
        "pp-design/2",
        _net_signal,
        True,
        None,
        (False, "not_validated", "snapshot_integrity_failed", "findings_mismatch"),
    ),
    # Known limit, kept visible: a consistent rewrite (hash recomputed) whose
    # findings are the same reads as the design saved. Only a signature over
    # the snapshot could tell; the hash is not one.
    "rehashed_net_rename_undetected": (
        "pp-design/2",
        _net_renamed,
        True,
        None,
        (True, "draft", None, None),
    ),
    "rehashed_pattern_edit": (
        "pp-design/2",
        _pattern_net,
        True,
        None,
        (False, "not_validated", "snapshot_integrity_failed", "unreadable"),
    ),
    "forged_review": (
        "pp-design/2",
        _forge_review,
        True,
        None,
        (False, "not_validated", "snapshot_integrity_failed", "review_not_issued"),
    ),
    "findings_policy_change": (
        "pp-design/2",
        None,
        False,
        "findings_policy",
        (True, "stale", "revision_stale", "findings_policy"),
    ),
    "rules_change": (
        "pp-design/2",
        None,
        False,
        "rules_version",
        (True, "stale", "revision_stale", "rules_version"),
    ),
}


def _kept(row: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in row.items() if key != "updated_at"}


@pytest.mark.parametrize("case", list(CASES))
def test_the_integrity_status_on_open_list_approval_and_every_export(
    client: TestClient,
    engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
    case: str,
) -> None:
    schema, alter, rehash, policy, expected = CASES[case]
    validated, status, code, reason = expected
    monkeypatch.setenv("DESIGN_SNAPSHOT_SCHEMA", "pp-design/1")
    project_id = _save(client)
    # Revision 1, older and approved: nothing done to revision 2 touches it.
    assert approve(client, project_id, 1).status_code == 200
    monkeypatch.setenv("DESIGN_SNAPSHOT_SCHEMA", schema)
    revised = _revise(
        client, project_id, _configuration() if schema == "pp-design/2" else None, CASE
    )
    assert revised.status_code == 201, revised.text[:500]
    if alter is not None:
        stored = copy.deepcopy(revision_row(engine, project_id, 2)["design_snapshot"])
        alter(stored)
        values: dict[str, Any] = {"design_snapshot": stored}
        if rehash:
            values["snapshot_sha256"] = snapshot.sha256_of(stored)
        update_row(engine, project_id, 2, **values)
    if policy is not None:
        saved = _open(client, project_id, 2)
        _change_policy(monkeypatch, policy, saved)
    before = _kept(revision_row(engine, project_id, 2))
    older = _kept(revision_row(engine, project_id, 1))

    def unchanged(step: str) -> None:
        assert _kept(revision_row(engine, project_id, 2)) == before, (case, step)
        assert _kept(revision_row(engine, project_id, 1)) == older, (case, step)

    # Open.
    opened = _open(client, project_id, 2)
    assert opened["validated"] is validated, case
    assert opened["export_status"] == status, case
    integrity = [f for f in opened["findings"] if f["code"] == "snapshot_integrity_failed"]
    if code == "snapshot_integrity_failed":
        assert [f["values"]["reason"] for f in integrity] == [reason]
        assert opened["model_v2"] is None
        assert "not_validated" in opened["execution_blockers"]
    else:
        assert integrity == []
    unchanged("open")

    # The revision list: as the opened revision, and as a listed one.
    listed_self = next(r for r in opened["revisions"] if r["number"] == 2)
    assert (listed_self["validated"], listed_self["export_status"]) == (validated, status)
    other = _open(client, project_id, 1)
    listed = next(r for r in other["revisions"] if r["number"] == 2)
    assert listed["validated"] is validated, case
    if status == "stale" and policy == "findings_policy":
        # Listed from columns alone: a reclassification needs the snapshot.
        assert listed["export_status"] in ("stale", "blocked", "draft")
    else:
        assert listed["export_status"] == status, case
    # Older revision and its approval are unaffected.
    first = next(r for r in other["revisions"] if r["number"] == 1)
    assert first["validated"] is True
    if policy is None:
        assert other["export_status"] == "review_approved"
        assert first["approval_current"] is True
    unchanged("list")

    # Approval.
    approval = approve(client, project_id, 2)
    if code is None:
        assert approval.status_code in (200, 409), approval.text[:300]
        if approval.status_code == 409:
            assert error(approval)["code"] == "revision_has_blocking_findings"
        else:
            # Approving an intact revision is its own write; nothing else moves.
            before = _kept(revision_row(engine, project_id, 2))
    else:
        assert approval.status_code == 409
        body = error(approval)
        assert body["code"] == code, (case, body)
        if code == "snapshot_integrity_failed":
            assert body["params"]["reason"] == reason
        else:
            assert reason in body["params"]["changed"].split(",")
            assert "findings_policy" not in body["params"]["changed"] or policy == (
                "findings_policy"
            )
        assert revision_row(engine, project_id, 2)["approved_at"] is None
    unchanged("approve")

    # Every review export.
    for fmt in FORMATS:
        response = revision_export(client, project_id, 2, fmt)
        if code is not None:
            assert response.status_code == 409, (case, fmt)
            body = error(response)
            assert body["code"] == code, (case, fmt, body)
            if code == "snapshot_integrity_failed":
                assert body["params"]["reason"] == reason, (case, fmt)
            else:
                assert reason in body["params"]["changed"].split(","), (case, fmt)
        elif fmt in SCHEDULES and schema == "pp-design/1":
            assert response.status_code == 409
            assert error(response)["code"] == "schedule_requires_v2"
        else:
            assert response.status_code == 200, (case, fmt, response.text[:300])
        unchanged(fmt)
    # A restricted format is refused before anything else, whatever the state.
    restricted = revision_export(client, project_id, 2, "devices_csv")
    assert restricted.status_code == 409
    assert error(restricted)["code"] == "export_format_unprotected"
    unchanged("devices_csv")
    execution = revision_export(client, project_id, 2, "json", purpose="execution")
    assert execution.status_code == 409
    refused = error(execution)
    assert refused["code"] == "execution_package_incomplete"
    blockers = refused["params"]["blockers"].split(",")
    assert ("snapshot_integrity_failed" in blockers) is (code == "snapshot_integrity_failed")
    unchanged("execution")

    # The older approved revision still exports as approved (no policy change).
    if policy is None:
        assert revision_export(client, project_id, 1, "json").status_code == 200
        assert _open(client, project_id, 1)["export_status"] == "review_approved"
    unchanged("end")


@pytest.mark.parametrize("rehash", [False, True])
def test_an_approved_revision_that_fails_integrity_is_listed_as_such(
    client: TestClient, engine: Engine, monkeypatch: pytest.MonkeyPatch, rehash: bool
) -> None:
    """Listed (not opened), an altered approved revision is neither validated nor current."""
    monkeypatch.setenv("DESIGN_SNAPSHOT_SCHEMA", "pp-design/1")
    project_id = _save(client)
    assert approve(client, project_id, 1).status_code == 200
    assert _revise(client, project_id, None, CASE).status_code == 201
    stored = copy.deepcopy(revision_row(engine, project_id, 1)["design_snapshot"])
    if rehash:
        # A board's notes dropped: the stored findings are no longer its findings.
        stored["boards"][0]["notes"] = []
    else:
        _v1_value(stored)
    values: dict[str, Any] = {"design_snapshot": stored}
    if rehash:
        values["snapshot_sha256"] = snapshot.sha256_of(stored)
    update_row(engine, project_id, 1, **values)
    before = _kept(revision_row(engine, project_id, 1))
    listed = next(r for r in _open(client, project_id, 2)["revisions"] if r["number"] == 1)
    assert listed["validated"] is False
    assert listed["export_status"] == "not_validated"
    assert listed["approval_current"] is False
    assert "snapshot" in listed["approval_changed"]
    refused = revision_export(client, project_id, 1, "pdf")
    assert error(refused)["code"] == "snapshot_integrity_failed"
    assert error(refused)["params"]["reason"] == (
        "findings_mismatch" if rehash else "hash_mismatch"
    )
    assert _kept(revision_row(engine, project_id, 1)) == before

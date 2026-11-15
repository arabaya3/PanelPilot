"""The engineering-review routes, and anchoring end to end, on the disposable Postgres (M3).

* The routes: strict 422s, 403 for who may not, 404 across tenants, 201 for
  the owner's designation and a designated reviewer's decision.
* Anchoring: a configured ``pp-design/2`` revision's definitions count as
  approved only with a server-side decision for their exact digest; a stored
  claim without one is refused, one that is exactly the server's record
  counts; changing a definition after its approval makes it unreviewed again.
* Execution stays refused with every definition approved.

Every tenant, account, statement and decision is synthetic
(``app.tests.domain.review_support``); no engineering review is implied.
"""

from __future__ import annotations

import copy
import uuid
from collections.abc import Callable, Iterator
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.api import deps
from app.api.v1.routes import design as design_route
from app.api.v1.routes import engineering_reviews as reviews_route
from app.core.db import get_session
from app.core.errors import install_exception_handlers
from app.design import snapshot
from app.design.model import anchoring, codec_v2
from app.design.model.read import read_snapshot
from app.models.schemas.auth import CurrentUser
from app.models.tables.base import Base
from app.tests import db_target
from app.tests.api.v1.routes.test_export_integrity import (
    DATABASE_URL,
    approve,
    error,
    revision_export,
    revision_row,
    update_row,
)
from app.tests.design.model.compat_support import DESIGNED
from app.tests.design.model.v2_support import CONFIGS, config_case
from app.tests.domain.review_support import SYNTHETIC_QUALIFICATION, new_account, new_tenant

pytestmark = pytest.mark.skipif(
    not DATABASE_URL.startswith("postgresql"),
    reason="needs Postgres: decisions, approvals and tenancy are the database's",
)

CASE = "pe_n_distribution"


@pytest.fixture(scope="module", name="engine")
def _engine() -> Iterator[Engine]:
    db_target.require_disposable(DATABASE_URL)
    engine = create_engine(DATABASE_URL)
    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


class _As:
    """Who the next request is from."""

    def __init__(self) -> None:
        self.user: CurrentUser | None = None


@pytest.fixture(name="who")
def _who() -> _As:
    return _As()


@pytest.fixture(name="client")
def _client(engine: Engine, who: _As) -> Iterator[TestClient]:
    factory = sessionmaker(bind=engine)

    def session() -> Iterator[Session]:
        opened = factory()
        try:
            yield opened
        finally:
            opened.close()

    app = FastAPI()
    app.include_router(design_route.router, prefix="/design")
    app.include_router(reviews_route.router, prefix="/engineering-reviews")
    app.dependency_overrides[deps.get_current_user] = lambda: who.user
    app.dependency_overrides[get_session] = session
    app.dependency_overrides[deps.enforce_design_rate_limit] = lambda: None
    install_exception_handlers(app)
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client


@pytest.fixture(name="team")
def _team(engine: Engine) -> dict[str, CurrentUser]:
    tenant = new_tenant(engine)
    other = new_tenant(engine)
    return {
        "owner": new_account(engine, tenant, owner=True),
        "engineer": new_account(engine, tenant),
        "stranger": new_account(engine, other, owner=True),
    }


@pytest.fixture(name="v2")
def _v2(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DESIGN_SNAPSHOT_SCHEMA", "pp-design/2")


def _request() -> dict[str, Any]:
    request: dict[str, Any] = copy.deepcopy(DESIGNED[CONFIGS[CASE]["fixture"]]["request"])
    return request


def _revise(client: TestClient, project_id: str) -> int:
    response = client.post(
        f"/design/projects/{project_id}/revisions",
        json={"request": _request(), "note": "configured", "configuration": config_case(CASE)[1]},
    )
    assert response.status_code == 201, response.text[:500]
    number: int = max(r["number"] for r in response.json()["revisions"])
    return number


def _configured(client: TestClient) -> tuple[str, int]:
    response = client.post(
        "/design/projects", json={"name": "M3 synthetic", "request": _request(), "note": "r1"}
    )
    assert response.status_code == 201, response.text[:500]
    project_id: str = response.json()["id"]
    return project_id, _revise(client, project_id)


def _open(client: TestClient, project_id: str, number: int) -> dict[str, Any]:
    response = client.get(f"/design/projects/{project_id}", params={"revision": number})
    assert response.status_code == 200, response.text[:500]
    body: dict[str, Any] = response.json()
    return body


def _subjects(engine: Engine, project_id: str, number: int) -> list[tuple[str, str, str]]:
    model = read_snapshot(revision_row(engine, project_id, number)["design_snapshot"]).v2
    assert model is not None
    return [("template", t.ref, anchoring.definition_digest(t)) for t in model.templates] + [
        ("pattern", p.ref, anchoring.definition_digest(p)) for p in model.patterns
    ]


def _decide(
    client: TestClient, kind: str, ref: str, digest: str, decision: str = "approved"
) -> Any:
    return client.post(
        "/engineering-reviews/decisions",
        json={
            "subject_kind": kind,
            "subject_ref": ref,
            "subject_digest": digest,
            "decision": decision,
            "standards": ["SYNTHETIC-STANDARD-REF"],
            "reason": "Synthetic test decision",
        },
    )


def _designate(client: TestClient, who: _As, team: dict[str, CurrentUser]) -> None:
    who.user = team["owner"]
    response = client.post(
        "/engineering-reviews/reviewers",
        json={"member_id": team["engineer"].id, "qualification_statement": SYNTHETIC_QUALIFICATION},
    )
    assert response.status_code == 201, response.text


def _codes(body: dict[str, Any]) -> set[str]:
    return {f["code"] for f in body["findings"]}


# --------------------------------------------------------------------------- #
# The routes
# --------------------------------------------------------------------------- #


def test_who_may_designate_and_decide(
    client: TestClient, who: _As, team: dict[str, CurrentUser]
) -> None:
    digest = "a" * 64
    ref = "tenant/m3-synthetic-template/v1"
    for caller in ("owner", "engineer"):
        who.user = team[caller]
        refused = _decide(client, "template", ref, digest)
        assert refused.status_code == 403
        assert error(refused)["code"] == "engineering_review_not_permitted"
    who.user = team["engineer"]
    refused = client.post(
        "/engineering-reviews/reviewers",
        json={"member_id": team["engineer"].id, "qualification_statement": "self-made"},
    )
    assert refused.status_code == 403
    assert error(refused)["code"] == "reviewer_designation_owner_only"
    assert client.get("/engineering-reviews/reviewers").status_code == 403

    _designate(client, who, team)
    listed = client.get("/engineering-reviews/reviewers").json()
    assert [r["member_id"] for r in listed] == [team["engineer"].id]

    who.user = team["engineer"]
    recorded = _decide(client, "template", ref, digest)
    assert recorded.status_code == 201, recorded.text
    body = recorded.json()
    assert body["scope"] == "tenant"
    assert body["current"] is True
    assert body["qualification_statement"] == SYNTHETIC_QUALIFICATION
    history = client.get(
        "/engineering-reviews/decisions", params={"subject_kind": "template", "subject_ref": ref}
    )
    assert [d["id"] for d in history.json()] == [body["id"]]

    who.user = team["owner"]
    assert client.delete(f"/engineering-reviews/reviewers/{team['engineer'].id}").status_code == 204
    who.user = team["engineer"]
    assert _decide(client, "template", ref, digest).status_code == 403


@pytest.mark.parametrize(
    "body",
    [
        {"extra": True},
        {"subject_kind": "design"},
        {"subject_digest": "XYZ"},
        {"decision": "approve"},
        {"reviewer": "Synthetic someone"},
        {"reason": ""},
    ],
)
def test_a_malformed_decision_is_a_422(
    client: TestClient, who: _As, team: dict[str, CurrentUser], body: dict[str, Any]
) -> None:
    _designate(client, who, team)
    who.user = team["engineer"]
    good = {
        "subject_kind": "template",
        "subject_ref": "tenant/m3-synthetic/v1",
        "subject_digest": "a" * 64,
        "decision": "approved",
        "standards": [],
        "reason": "Synthetic",
    }
    response = client.post("/engineering-reviews/decisions", json={**good, **body})
    assert response.status_code == 422


def test_a_malformed_ref_is_a_422(
    client: TestClient, who: _As, team: dict[str, CurrentUser]
) -> None:
    who.user = team["owner"]
    for params in (
        {"subject_kind": "template", "subject_ref": "tenant/x@v1"},
        {"subject_kind": "design", "subject_ref": "tenant/x/v1"},
        {"subject_kind": "template"},
    ):
        assert client.get("/engineering-reviews/decisions", params=params).status_code == 422


def test_another_tenant_is_not_found_and_sees_nothing(
    client: TestClient, who: _As, team: dict[str, CurrentUser]
) -> None:
    _designate(client, who, team)
    who.user = team["engineer"]
    ref = "tenant/m3-isolated/v1"
    assert _decide(client, "template", ref, "a" * 64).status_code == 201
    who.user = team["stranger"]
    listed = client.get(
        "/engineering-reviews/decisions", params={"subject_kind": "template", "subject_ref": ref}
    )
    assert listed.status_code == 200
    assert listed.json() == []
    for member in (team["engineer"].id, str(uuid.uuid4())):
        designated = client.post(
            "/engineering-reviews/reviewers",
            json={"member_id": member, "qualification_statement": SYNTHETIC_QUALIFICATION},
        )
        assert designated.status_code == 404
        assert client.delete(f"/engineering-reviews/reviewers/{member}").status_code == 404


# --------------------------------------------------------------------------- #
# Anchoring, end to end
# --------------------------------------------------------------------------- #


def _rewrite(
    stored: dict[str, Any], review_of: Callable[[str, dict[str, Any]], dict[str, Any] | None]
) -> dict[str, Any]:
    """The stored snapshot with reviews replaced where ``review_of`` says, locks recomputed."""
    out = copy.deepcopy(stored)
    model = out["model"]
    for kind, key in (("template", "templates"), ("pattern", "patterns")):
        for definition in model[key]:
            review = review_of(kind, definition)
            if review is not None:
                definition["review"] = review
    model["registry_lock"]["templates"] = [
        {"ref": t["ref"], "digest": codec_v2.template_digest(codec_v2.parse_template(t))}
        for t in model["templates"]
    ]
    model["registry_lock"]["patterns"] = [
        {"ref": p["ref"], "digest": codec_v2.pattern_digest(codec_v2.parse_pattern(p))}
        for p in model["patterns"]
    ]
    return out


def test_definitions_count_as_approved_only_with_a_matching_record(
    client: TestClient, who: _As, team: dict[str, CurrentUser], engine: Engine, v2: None
) -> None:
    who.user = team["owner"]
    project_id, first = _configured(client)
    opened = _open(client, project_id, first)
    assert {"template_unreviewed", "pattern_unreviewed"} <= _codes(opened)
    assert {d["review_status"] for d in opened["model_v2"]["definitions"]} == {"unreviewed"}

    _designate(client, who, team)
    who.user = team["engineer"]
    subjects = _subjects(engine, project_id, first)
    for kind, ref, digest in subjects:
        assert _decide(client, kind, ref, digest).status_code == 201

    # What revision `first` was saved under no longer holds: it is stale,
    # never carried over as if the reviews had always said so.
    who.user = team["owner"]
    assert _open(client, project_id, first)["export_status"] == "stale"

    second = _revise(client, project_id)
    opened = _open(client, project_id, second)
    assert opened["export_status"] == "draft"
    assert not {"template_unreviewed", "pattern_unreviewed"} & _codes(opened)
    assert {d["review_status"] for d in opened["model_v2"]["definitions"]} == {"approved"}

    # Approved everywhere, approved revision: execution is still refused.
    assert approve(client, project_id, second).status_code == 200
    refused = revision_export(client, project_id, second, "pdf", purpose="execution")
    assert refused.status_code == 409
    assert error(refused)["code"] == "execution_package_incomplete"
    for fmt in ("qet", "aml", "plc_st", "devices_csv"):
        assert revision_export(client, project_id, second, fmt).status_code in (409, 422), fmt
    assert revision_export(client, project_id, second, "pdf").status_code == 200

    # Another tenant's decisions never reach this one, and the project is 404 there.
    who.user = team["stranger"]
    assert client.get(f"/design/projects/{project_id}").status_code == 404


def test_a_stored_claim_must_be_the_servers_record(
    client: TestClient, who: _As, team: dict[str, CurrentUser], engine: Engine, v2: None
) -> None:
    who.user = team["owner"]
    project_id, number = _configured(client)
    stored = revision_row(engine, project_id, number)["design_snapshot"]

    # A claim with no record: refused like an altered design (M2's rule kept).
    forged_review: dict[str, Any] = {
        "status": "approved",
        "reviewer": "Synthetic forged reviewer",
        "reviewed_at": "2026-01-01",
        "standards": [],
        "scope_note": None,
    }
    forged = _rewrite(stored, lambda _k, _d: dict(forged_review))
    update_row(
        engine,
        project_id,
        number,
        design_snapshot=forged,
        snapshot_sha256=snapshot.sha256_of(forged),
    )
    opened = _open(client, project_id, number)
    assert opened["export_status"] == "not_validated"
    assert "snapshot_integrity_failed" in _codes(opened)

    # Record approvals; a claim exactly as the server records them counts.
    _designate(client, who, team)
    who.user = team["engineer"]
    recorded: dict[tuple[str, str], dict[str, Any]] = {}
    for kind, ref, digest in _subjects(engine, project_id, number):
        response = _decide(client, kind, ref, digest)
        assert response.status_code == 201
        body = response.json()
        recorded[(kind, ref)] = {
            "status": "approved",
            "reviewer": f"server-record:tenant:{body['id']}",
            "reviewed_at": _decided_at(engine, team["owner"], body["id"]),
            "standards": body["standards"],
            "scope_note": None,
        }
    who.user = team["owner"]
    honest = _rewrite(stored, lambda k, d: recorded[(k, d["ref"])])
    update_row(
        engine,
        project_id,
        number,
        design_snapshot=honest,
        snapshot_sha256=snapshot.sha256_of(honest),
    )
    opened = _open(client, project_id, number)
    # It reads: no integrity failure. Its saved findings said "unreviewed",
    # which the records no longer do, so it is stale (and shows no summary).
    assert "snapshot_integrity_failed" not in _codes(opened)
    assert opened["export_status"] == "stale"

    # The forged claim is still refused, records or not.
    update_row(
        engine,
        project_id,
        number,
        design_snapshot=forged,
        snapshot_sha256=snapshot.sha256_of(forged),
    )
    assert _open(client, project_id, number)["export_status"] == "not_validated"


def _decided_at(engine: Engine, user: CurrentUser, decision_id: str) -> str:
    from app.domain import engineering_reviews

    with sessionmaker(bind=engine)() as session:
        anchor = engineering_reviews.anchor_for(
            session=session, tenant_id=uuid.UUID(user.tenant_id)
        )
    for decided in anchor.tenant.values():
        if decided.record_id == decision_id:
            return decided.decided_at
    raise AssertionError(decision_id)


def test_changing_a_definition_after_approval_makes_it_unreviewed(
    client: TestClient, who: _As, team: dict[str, CurrentUser], engine: Engine, v2: None
) -> None:
    who.user = team["owner"]
    project_id, number = _configured(client)
    _designate(client, who, team)
    who.user = team["engineer"]
    for kind, ref, digest in _subjects(engine, project_id, number):
        assert _decide(client, kind, ref, digest).status_code == 201
    who.user = team["owner"]
    number = _revise(client, project_id)
    assert not {"template_unreviewed"} & _codes(_open(client, project_id, number))

    # Edit one template's content outside the application, locks and hash
    # recomputed: its approval was for another digest, so it is unreviewed.
    stored = copy.deepcopy(revision_row(engine, project_id, number)["design_snapshot"])
    target = next(t for t in stored["model"]["templates"] if t["ref"].startswith("tenant/"))
    target["function"] = target["function"] + "-edited"
    edited = _rewrite(stored, lambda _k, _d: None)
    update_row(
        engine,
        project_id,
        number,
        design_snapshot=edited,
        snapshot_sha256=snapshot.sha256_of(edited),
    )
    # No decision was ever recorded for the edited digest, so stored findings
    # without its review finding cannot have been written by a save: an
    # integrity failure, not a review change.
    opened = _open(client, project_id, number)
    assert opened["export_status"] == "not_validated"
    integrity = [f for f in opened["findings"] if f["code"] == "snapshot_integrity_failed"]
    assert [f["values"]["reason"] for f in integrity] == ["findings_mismatch"]
    # Its anchored review is unreviewed: the decision was for the old digest.
    from app.domain import engineering_reviews

    model = read_snapshot(edited).v2
    assert model is not None
    with sessionmaker(bind=engine)() as session:
        anchor = engineering_reviews.anchor_for(
            session=session, tenant_id=uuid.UUID(team["owner"].tenant_id)
        )
    anchored = anchoring.anchor_model(model, anchor)
    statuses = {t.ref: t.review.status for t in anchored.templates}
    assert statuses[target["ref"]] == "unreviewed"
    assert {s for r, s in statuses.items() if r != target["ref"]} == {"approved"}


# --------------------------------------------------------------------------- #
# A review decision since the save is stale, not altered (M2 x M3)
# --------------------------------------------------------------------------- #

#: The review formats every refusal below is checked on.
_REVIEW_FORMATS = ("pdf", "json", "terminal_schedule_pdf", "wire_schedule_pdf")


def _listed(client: TestClient, project_id: str, number: int) -> dict[str, Any]:
    response = client.get(f"/design/projects/{project_id}")
    assert response.status_code == 200, response.text[:500]
    found: dict[str, Any] = next(r for r in response.json()["revisions"] if r["number"] == number)
    return found


def _approved_then_decided(
    client: TestClient, who: _As, team: dict[str, CurrentUser], engine: Engine
) -> tuple[str, int, int]:
    """A configured revision approved for review and one not, then every definition approved.

    Returns the project, the approved revision and the unapproved one.
    """
    who.user = team["owner"]
    project_id, number = _configured(client)
    assert approve(client, project_id, number).status_code == 200
    assert _open(client, project_id, number)["export_status"] == "review_approved"
    unapproved = _revise(client, project_id)
    assert _open(client, project_id, unapproved)["export_status"] == "draft"
    _designate(client, who, team)
    who.user = team["engineer"]
    for kind, ref, digest in _subjects(engine, project_id, number):
        assert _decide(client, kind, ref, digest).status_code == 201
    who.user = team["owner"]
    return project_id, number, unapproved


def test_a_review_decision_since_the_save_makes_the_revision_stale_not_altered(
    client: TestClient, who: _As, team: dict[str, CurrentUser], engine: Engine, v2: None
) -> None:
    """Finding codes change legitimately (``template_unreviewed`` gone): stale, intact."""
    project_id, number, unapproved = _approved_then_decided(client, who, team, engine)
    stored_codes = {f["code"] for f in revision_row(engine, project_id, number)["findings"]}
    assert {"template_unreviewed", "pattern_unreviewed"} <= stored_codes

    opened = _open(client, project_id, number)
    assert opened["export_status"] == "stale"
    assert opened["validated"] is True
    assert "snapshot_integrity_failed" not in _codes(opened)
    assert opened["approval_current"] is False
    assert "review_decisions" in opened["approval_changed"]
    assert "findings_policy" not in opened["approval_changed"]
    assert opened["model_v2"] is None
    listed = _listed(client, project_id, number)
    assert listed["export_status"] == "stale"
    assert listed["validated"] is True
    assert listed["approval_current"] is False
    assert "review_decisions" in listed["approval_changed"]

    for fmt in _REVIEW_FORMATS:
        refused = revision_export(client, project_id, number, fmt)
        assert refused.status_code == 409, fmt
        assert error(refused)["code"] == "revision_stale", fmt
        assert "review_decisions" in error(refused)["params"]["changed"].split(","), fmt
    refused = revision_export(client, project_id, number, "pdf", purpose="execution")
    assert refused.status_code == 409
    assert error(refused)["code"] == "execution_package_incomplete"
    # The revision not approved: stale too, so it cannot be approved now.
    assert _open(client, project_id, unapproved)["export_status"] == "stale"
    assert _listed(client, project_id, unapproved)["validated"] is True
    refused = approve(client, project_id, unapproved)
    assert refused.status_code == 409
    assert error(refused)["code"] == "revision_stale"
    assert error(refused)["params"]["changed"].split(",") == ["review_decisions"]
    assert revision_row(engine, project_id, unapproved)["approved_at"] is None

    # Withdrawn again: the stored findings hold once more, but the approval
    # was granted before the decisions and its row is unchanged, so it counts.
    who.user = team["engineer"]
    for kind, ref, digest in _subjects(engine, project_id, number):
        assert _decide(client, kind, ref, digest, "withdrawn").status_code == 201
    who.user = team["owner"]
    assert _open(client, project_id, number)["export_status"] == "review_approved"
    assert revision_export(client, project_id, number, "pdf").status_code == 200


def _drop_plain(findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """A finding the review decisions do not decide, removed."""
    target = next(f for f in findings if f["code"] == "wire_attribute_assumed")
    return [f for f in findings if f is not target]


def _drop_one_place(findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One place of a template used at several, its unreviewed finding removed."""
    by_ref: dict[str, list[dict[str, Any]]] = {}
    for found in findings:
        if found["code"] == "template_unreviewed":
            by_ref.setdefault(found["values"]["template"], []).append(found)
    target = next(group for group in by_ref.values() if len(group) > 1)[0]
    return [f for f in findings if f is not target]


def _invent_review(findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """A review finding for a definition the design does not hold."""
    made = copy.deepcopy(next(f for f in findings if f["code"] == "pattern_unreviewed"))
    made["values"]["pattern"] = "tenant/m3-synthetic-absent/v1"
    return [*findings, made]


def _mixed_status(findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One template both unreviewed and rejected: no set of decisions gives that."""
    out = copy.deepcopy(findings)
    groups: dict[str, list[dict[str, Any]]] = {}
    for found in out:
        if found["code"] == "template_unreviewed":
            groups.setdefault(found["values"]["template"], []).append(found)
    group = next(g for g in groups.values() if len(g) > 1)
    group[0]["code"] = "template_rejected"
    group[0]["severity"] = "blocking"
    return out


@pytest.mark.parametrize("decided", [False, True])
@pytest.mark.parametrize("alter", [_drop_plain, _drop_one_place, _invent_review, _mixed_status])
def test_a_tampered_findings_row_is_still_an_integrity_failure(
    client: TestClient,
    who: _As,
    team: dict[str, CurrentUser],
    engine: Engine,
    v2: None,
    alter: Callable[[list[dict[str, Any]]], list[dict[str, Any]]],
    decided: bool,
) -> None:
    """Stored findings rewritten outside the application, decisions recorded or not."""
    if decided:
        project_id, number, unapproved = _approved_then_decided(client, who, team, engine)
    else:
        who.user = team["owner"]
        project_id, number = _configured(client)
        assert approve(client, project_id, number).status_code == 200
        unapproved = _revise(client, project_id)
    for target in (number, unapproved):
        findings = alter(copy.deepcopy(revision_row(engine, project_id, target)["findings"]))
        update_row(engine, project_id, target, findings=findings)

    opened = _open(client, project_id, number)
    assert opened["export_status"] == "not_validated"
    assert opened["validated"] is False
    integrity = [f for f in opened["findings"] if f["code"] == "snapshot_integrity_failed"]
    assert [f["values"]["reason"] for f in integrity] == ["findings_mismatch"]
    assert opened["model_v2"] is None
    assert opened["approval_current"] is False
    listed = _listed(client, project_id, number)
    assert listed["validated"] is False
    assert listed["export_status"] == "not_validated"
    assert listed["approval_current"] is False
    for fmt in _REVIEW_FORMATS:
        refused = revision_export(client, project_id, number, fmt)
        assert refused.status_code == 409, fmt
        assert error(refused)["code"] == "snapshot_integrity_failed", fmt
        assert error(refused)["params"]["reason"] == "findings_mismatch", fmt
    refused = approve(client, project_id, unapproved)
    assert refused.status_code == 409
    assert error(refused)["code"] == "snapshot_integrity_failed"
    assert error(refused)["params"]["reason"] == "findings_mismatch"
    assert revision_row(engine, project_id, unapproved)["approved_at"] is None


def _drop_template(findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Every ``template_unreviewed`` finding of one tenant template removed."""
    ref = next(
        f["values"]["template"]
        for f in findings
        if f["code"] == "template_unreviewed" and f["values"]["template"].startswith("tenant/")
    )
    return [
        f
        for f in findings
        if not (f["code"] == "template_unreviewed" and f["values"]["template"] == ref)
    ]


def test_dropping_a_templates_review_findings_with_no_decision_is_an_integrity_failure(
    client: TestClient, who: _As, team: dict[str, CurrentUser], engine: Engine, v2: None
) -> None:
    """Stored findings that say "approved" need an approval the server recorded."""
    who.user = team["owner"]
    project_id, number = _configured(client)
    unapproved = _revise(client, project_id)
    for target in (number, unapproved):
        findings = _drop_template(
            copy.deepcopy(revision_row(engine, project_id, target)["findings"])
        )
        update_row(engine, project_id, target, findings=findings)
    opened = _open(client, project_id, number)
    assert opened["export_status"] == "not_validated"
    integrity = [f for f in opened["findings"] if f["code"] == "snapshot_integrity_failed"]
    assert [f["values"]["reason"] for f in integrity] == ["findings_mismatch"]
    assert _listed(client, project_id, number)["validated"] is False
    for fmt in _REVIEW_FORMATS:
        refused = revision_export(client, project_id, number, fmt)
        assert error(refused)["params"]["reason"] == "findings_mismatch", fmt
    refused = approve(client, project_id, unapproved)
    assert error(refused)["code"] == "snapshot_integrity_failed"
    assert error(refused)["params"]["reason"] == "findings_mismatch"


def test_a_revocation_makes_revisions_saved_under_the_reviewers_decisions_stale(
    client: TestClient, who: _As, team: dict[str, CurrentUser], engine: Engine, v2: None
) -> None:
    who.user = team["owner"]
    project_id, first = _configured(client)
    _designate(client, who, team)
    who.user = team["engineer"]
    subjects = _subjects(engine, project_id, first)
    for kind, ref, digest in subjects:
        assert _decide(client, kind, ref, digest).status_code == 201
    who.user = team["owner"]
    saved = _revise(client, project_id)
    assert approve(client, project_id, saved).status_code == 200
    pending = _revise(client, project_id)
    assert _open(client, project_id, saved)["export_status"] == "review_approved"

    revoked = client.delete(f"/engineering-reviews/reviewers/{team['engineer'].id}")
    assert revoked.status_code in (200, 204), revoked.text

    opened = _open(client, project_id, saved)
    assert opened["export_status"] == "stale"
    assert opened["validated"] is True
    assert "review_decisions" in opened["approval_changed"]
    assert _listed(client, project_id, saved)["export_status"] == "stale"
    refused = revision_export(client, project_id, saved, "pdf")
    assert error(refused)["code"] == "revision_stale"
    assert "review_decisions" in error(refused)["params"]["changed"].split(",")
    refused = approve(client, project_id, pending)
    assert error(refused)["code"] == "revision_stale"
    assert error(refused)["params"]["changed"].split(",") == ["review_decisions"]
    # The decisions stay recorded, shown as the revoked reviewer's.
    kind, ref, _digest = subjects[0]
    listed = client.get(
        "/engineering-reviews/decisions", params={"subject_kind": kind, "subject_ref": ref}
    )
    assert listed.status_code == 200, listed.text
    assert {(d["reviewer_standing"], d["current"]) for d in listed.json()} == {("revoked", False)}


def test_a_reviewer_may_not_decide_on_a_definition_they_saved(
    client: TestClient, who: _As, team: dict[str, CurrentUser], engine: Engine, v2: None
) -> None:
    """The owner designates themselves and saved the tenant template: refused (O-10 open)."""
    who.user = team["owner"]
    project_id, number = _configured(client)
    response = client.post(
        "/engineering-reviews/reviewers",
        json={"member_id": team["owner"].id, "qualification_statement": SYNTHETIC_QUALIFICATION},
    )
    assert response.status_code == 201, response.text
    subjects = _subjects(engine, project_id, number)
    own = next(s for s in subjects if s[0] == "template" and s[1].startswith("tenant/"))
    refused = _decide(client, *own)
    assert refused.status_code == 403
    assert error(refused)["code"] == "engineering_review_self_review"
    # A global definition has no author the server knows: recorded, and says so.
    shipped = next(s for s in subjects if s[1].startswith("global/"))
    recorded = _decide(client, *shipped)
    assert recorded.status_code == 201, recorded.text
    assert recorded.json()["independence_checked"] is False
    assert "not checked" in recorded.json()["independence"]
    # Another designated reviewer, who saved no revision holding it: checked.
    _designate(client, who, team)
    who.user = team["engineer"]
    other = _decide(client, *own)
    assert other.status_code == 201, other.text
    assert other.json()["independence_checked"] is True

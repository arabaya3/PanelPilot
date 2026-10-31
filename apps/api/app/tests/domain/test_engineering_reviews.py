"""Tests for ``app/domain/engineering_reviews.py`` against the disposable Postgres.

Who may designate reviewers and record decisions, the append-only record and
its supersession, digest binding, tenant isolation and global decisions.
Synthetic tenants, accounts, statements and refs only (``review_support``);
no decision here is a real engineering review.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.errors import AuthorizationError, NotFoundError, ValidationError
from app.design.model import anchoring
from app.domain import engineering_reviews as reviews
from app.domain import permissions
from app.models.schemas.auth import CurrentUser, Permission, Role
from app.models.schemas.engineering_reviews import DecisionRequest
from app.models.tables.base import Base
from app.tests import db_target
from app.tests.design.rules.synthetic import echo_rule
from app.tests.domain.review_support import SYNTHETIC_QUALIFICATION, new_account, new_tenant

DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL.startswith("postgresql"),
    reason="needs Postgres: grants and decisions are the database's",
)

DIGEST_A = "a" * 64
DIGEST_B = "b" * 64


@pytest.fixture(scope="module", name="engine")
def _engine() -> Iterator[Engine]:
    db_target.require_disposable(DATABASE_URL)
    engine = create_engine(DATABASE_URL)
    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


def _session(engine: Engine) -> Session:
    return sessionmaker(bind=engine)()


def _designate(engine: Engine, owner: CurrentUser, member: CurrentUser) -> None:
    with _session(engine) as session:
        reviews.designate(
            session=session,
            user=owner,
            member_id=member.id,
            qualification_statement=SYNTHETIC_QUALIFICATION,
        )
        session.commit()


def _request(**overrides: Any) -> DecisionRequest:
    fields: dict[str, Any] = {
        "subject_kind": "template",
        "subject_ref": "tenant/m3-synthetic-template/v1",
        "subject_digest": DIGEST_A,
        "decision": "approved",
        "standards": ["SYNTHETIC-STANDARD-REF"],
        "reason": "Synthetic test decision",
    }
    fields.update(overrides)
    return DecisionRequest(**fields)


def _record(engine: Engine, user: CurrentUser, **overrides: Any) -> Any:
    with _session(engine) as session:
        out = reviews.record_decision(session=session, user=user, request=_request(**overrides))
        session.commit()
        return out


def _list(engine: Engine, user: CurrentUser, ref: str = "tenant/m3-synthetic-template/v1") -> Any:
    with _session(engine) as session:
        return reviews.decisions(
            session=session, user=user, subject_kind="template", subject_ref=ref
        )


def _held(engine: Engine, user: CurrentUser) -> bool:
    with _session(engine) as session:
        return Permission.REVIEW_ENGINEERING_DEFINITIONS in permissions.allowed(
            session=session, user=user
        )


@pytest.fixture(name="team")
def _team(engine: Engine) -> dict[str, CurrentUser]:
    tenant = new_tenant(engine)
    return {
        "owner": new_account(engine, tenant, owner=True),
        "admin": new_account(engine, tenant, role=Role.ADMIN),
        "reviewer": new_account(engine, tenant, role=Role.REVIEWER),
        "engineer": new_account(engine, tenant),
    }


def test_nobody_holds_the_permission_by_default(
    engine: Engine, team: dict[str, CurrentUser]
) -> None:
    for who, user in team.items():
        assert not _held(engine, user), who
        with pytest.raises(AuthorizationError) as refused:
            _record(engine, user)
        assert refused.value.code == "engineering_review_not_permitted"
    # The owner and an admin still hold everything else.
    with _session(engine) as session:
        assert set(permissions.allowed(session=session, user=team["owner"])) == set(Permission) - {
            Permission.REVIEW_ENGINEERING_DEFINITIONS
        }


@pytest.mark.parametrize("who", ["admin", "reviewer", "engineer"])
def test_only_the_owner_designates(engine: Engine, team: dict[str, CurrentUser], who: str) -> None:
    with _session(engine) as session, pytest.raises(AuthorizationError) as refused:
        reviews.designate(
            session=session,
            user=team[who],
            member_id=team["engineer"].id,
            qualification_statement=SYNTHETIC_QUALIFICATION,
        )
    assert refused.value.code == "reviewer_designation_owner_only"
    with _session(engine) as session, pytest.raises(AuthorizationError):
        reviews.revoke(session=session, user=team[who], member_id=team["engineer"].id)


def test_the_owner_grant_flow(engine: Engine, team: dict[str, CurrentUser]) -> None:
    owner, engineer = team["owner"], team["engineer"]
    _designate(engine, owner, engineer)
    assert _held(engine, engineer)
    with _session(engine) as session:
        listed = reviews.reviewers(session=session, user=team["admin"])
    assert [(r.member_id, r.qualification_statement, r.granted_by) for r in listed] == [
        (engineer.id, SYNTHETIC_QUALIFICATION, owner.id)
    ]
    # A second designation is refused; the statement is never overwritten.
    with _session(engine) as session, pytest.raises(ValidationError) as refused:
        reviews.designate(
            session=session, user=owner, member_id=engineer.id, qualification_statement="other"
        )
    assert refused.value.code == "reviewer_already_designated"

    recorded = _record(engine, engineer)
    assert recorded.qualification_statement == SYNTHETIC_QUALIFICATION
    assert recorded.reviewer == engineer.id

    with _session(engine) as session:
        reviews.revoke(session=session, user=owner, member_id=engineer.id)
        session.commit()
    assert not _held(engine, engineer)
    with pytest.raises(AuthorizationError):
        _record(engine, engineer)
    # What was recorded stays.
    assert [d.id for d in _list(engine, owner)] == [recorded.id]
    with _session(engine) as session, pytest.raises(NotFoundError):
        reviews.revoke(session=session, user=owner, member_id=engineer.id)
    # The owner may designate themselves.
    _designate(engine, owner, owner)
    assert _held(engine, owner)


def test_listing_reviewers_is_for_managers(engine: Engine, team: dict[str, CurrentUser]) -> None:
    for who in ("reviewer", "engineer"):
        with _session(engine) as session, pytest.raises(AuthorizationError):
            reviews.reviewers(session=session, user=team[who])


def test_a_member_of_another_tenant_is_not_found(
    engine: Engine, team: dict[str, CurrentUser]
) -> None:
    other = new_account(engine, new_tenant(engine))
    for member_id in (other.id, str(uuid.uuid4()), "not-a-uuid"):
        with _session(engine) as session, pytest.raises(NotFoundError):
            reviews.designate(
                session=session,
                user=team["owner"],
                member_id=member_id,
                qualification_statement=SYNTHETIC_QUALIFICATION,
            )


def test_decisions_are_append_only_and_supersede(
    engine: Engine, team: dict[str, CurrentUser]
) -> None:
    _designate(engine, team["owner"], team["engineer"])
    first = _record(engine, team["engineer"])
    second = _record(engine, team["engineer"], decision="rejected", reason="Synthetic, later")
    other = _record(engine, team["engineer"], subject_digest=DIGEST_B)
    listed = _list(engine, team["engineer"])
    assert {d.id: d.current for d in listed} == {first.id: False, second.id: True, other.id: True}
    tenant = uuid.UUID(team["owner"].tenant_id)
    with _session(engine) as session:
        anchor = reviews.anchor_for(session=session, tenant_id=tenant)
    ref = "tenant/m3-synthetic-template/v1"
    assert anchor.status("template", ref, DIGEST_A) == "rejected"
    assert anchor.status("template", ref, DIGEST_B) == "approved"
    # Bound to the digest: another digest of the same ref is unreviewed.
    assert anchor.status("template", ref, "c" * 64) == "unreviewed"
    _record(engine, team["engineer"], decision="withdrawn", reason="Synthetic withdrawal")
    with _session(engine) as session:
        anchor = reviews.anchor_for(session=session, tenant_id=tenant)
    assert anchor.status("template", ref, DIGEST_A) == "unreviewed"


def test_another_tenant_sees_nothing(engine: Engine, team: dict[str, CurrentUser]) -> None:
    _designate(engine, team["owner"], team["engineer"])
    _record(engine, team["engineer"])
    stranger_tenant = new_tenant(engine)
    stranger = new_account(engine, stranger_tenant, owner=True)
    assert _list(engine, stranger) == []
    with _session(engine) as session:
        anchor = reviews.anchor_for(session=session, tenant_id=uuid.UUID(stranger_tenant))
    assert anchor.status("template", "tenant/m3-synthetic-template/v1", DIGEST_A) == "unreviewed"
    # A designation in one tenant grants nothing in another.
    assert not _held(engine, stranger)


@pytest.mark.parametrize(
    "overrides",
    [
        {"subject_ref": "tenant/m3-synthetic@v1"},
        {"subject_ref": "elsewhere/x/v1"},
        {"subject_kind": "rule", "subject_ref": "Synthetic/x/v1"},
        {"standards": ["dup", "dup"]},
        {"standards": [" "]},
        {"reason": " "},
    ],
)
def test_a_malformed_decision_is_refused(
    engine: Engine, team: dict[str, CurrentUser], overrides: dict[str, Any]
) -> None:
    _designate(engine, team["owner"], team["engineer"])
    with pytest.raises(ValidationError):
        _record(engine, team["engineer"], **overrides)


def test_a_trial_sees_no_decisions(engine: Engine) -> None:
    trial = CurrentUser(id="trial", email="", tenant_id=new_tenant(engine), roles=frozenset())
    with _session(engine) as session, pytest.raises(AuthorizationError):
        reviews.decisions(
            session=session,
            user=trial,
            subject_kind="template",
            subject_ref="tenant/m3-synthetic-template/v1",
        )


def _global(engine: Engine, ref: str, decision: str, digest: str = DIGEST_A) -> Any:
    with _session(engine) as session:
        row = reviews.record_global_decision(
            session=session,
            subject_kind="rule",
            subject_ref=ref,
            subject_digest=digest,
            decision=decision,
            reviewer_label="Synthetic global reviewer",
            qualification_statement=SYNTHETIC_QUALIFICATION,
            standards=["SYNTHETIC-STANDARD-REF"],
            reason="Synthetic global decision",
            recorded_by="synthetic-operator",
        )
        session.commit()
        return row


def test_a_global_decision_applies_in_every_tenant_and_a_rejection_wins(
    engine: Engine, team: dict[str, CurrentUser]
) -> None:
    # A rule ref no other test uses: global rows touch every tenant.
    rule = echo_rule(ref=f"synthetic/m3-{uuid.uuid4().hex[:12]}/v1")
    _global(engine, rule.ref, "approved", rule.digest)
    tenants = [uuid.UUID(team["owner"].tenant_id), uuid.UUID(new_tenant(engine))]
    for tenant in tenants:
        with _session(engine) as session:
            anchor = reviews.anchor_for(session=session, tenant_id=tenant)
        assert reviews.rule_status(anchor, rule) == "approved"
    # A tenant's rejection wins over the global approval, in that tenant only.
    _designate(engine, team["owner"], team["engineer"])
    _record(
        engine,
        team["engineer"],
        subject_kind="rule",
        subject_ref=rule.ref,
        subject_digest=rule.digest,
        decision="rejected",
    )
    with _session(engine) as session:
        here = reviews.anchor_for(session=session, tenant_id=tenants[0])
    with _session(engine) as session:
        there = reviews.anchor_for(session=session, tenant_id=tenants[1])
    assert reviews.rule_status(here, rule) == "rejected"
    assert reviews.rule_status(there, rule) == "approved"
    # Withdrawn globally: back to the declared status, flagged withdrawn.
    _global(engine, rule.ref, "withdrawn", rule.digest)
    with _session(engine) as session:
        there = reviews.anchor_for(session=session, tenant_id=tenants[1])
    assert reviews.rule_status(there, rule) == "withdrawn"
    # A changed rule (another digest) has no decision.
    changed = echo_rule(ref=rule.ref, title="Synthetic echo, changed")
    assert reviews.rule_status(there, changed) == "draft"
    listed = _list_rule(engine, team["owner"], rule.ref)
    assert {d.scope for d in listed} == {"tenant", "global"}


def _list_rule(engine: Engine, user: CurrentUser, ref: str) -> Any:
    with _session(engine) as session:
        return reviews.decisions(session=session, user=user, subject_kind="rule", subject_ref=ref)


@pytest.mark.parametrize(
    "overrides",
    [
        {"subject_digest": "XYZ"},
        {"decision": "approve"},
        {"reviewer_label": " "},
        {"qualification_statement": ""},
        {"recorded_by": ""},
        {"subject_ref": "synthetic/x@1"},
    ],
)
def test_a_malformed_global_decision_is_refused(engine: Engine, overrides: dict[str, Any]) -> None:
    fields: dict[str, Any] = {
        "subject_kind": "rule",
        "subject_ref": "synthetic/m3-never/v1",
        "subject_digest": DIGEST_A,
        "decision": "approved",
        "reviewer_label": "Synthetic",
        "qualification_statement": SYNTHETIC_QUALIFICATION,
        "standards": [],
        "reason": "Synthetic",
        "recorded_by": "synthetic-operator",
    }
    fields.update(overrides)
    with _session(engine) as session, pytest.raises(ValidationError):
        reviews.record_global_decision(session=session, **fields)


def test_draft_rules_stay_draft_without_a_record(engine: Engine) -> None:
    with _session(engine) as session:
        anchor = reviews.anchor_for(session=session, tenant_id=uuid.UUID(new_tenant(engine)))
    assert reviews.rule_status(anchor, echo_rule()) == "draft"
    assert anchor == anchoring.ReviewAnchor(tenant={}, global_=anchor.global_)

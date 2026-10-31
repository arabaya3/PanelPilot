"""Request and response schemas for engineering review decisions (M3).

A decision is a record of what a designated reviewer decided about one exact
definition (by digest). It is never engineering approval of a design, and
never execution readiness.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

SubjectKind = Literal["template", "pattern", "rule"]
Decision = Literal["approved", "rejected", "withdrawn"]

_DIGEST = r"^[0-9a-f]{64}$"


class DecisionRequest(BaseModel):
    """Record a decision on one definition, bound to its digest.

    Attributes:
        subject_kind: ``template``, ``pattern`` or ``rule``.
        subject_ref: ``<scope>/<id>/v<version>``.
        subject_digest: The SHA-256 of the definition reviewed (for a template
            or pattern, with its review unset).
        decision: ``approved``, ``rejected`` or ``withdrawn``.
        standards: The standards the reviewer relied on, as they name them.
        reason: Why.
    """

    model_config = ConfigDict(extra="forbid")

    subject_kind: SubjectKind
    subject_ref: str = Field(min_length=1, max_length=160)
    subject_digest: str = Field(pattern=_DIGEST)
    decision: Decision
    standards: list[str] = Field(default_factory=list, max_length=20)
    reason: str = Field(min_length=1, max_length=2000)


class DecisionOut(BaseModel):
    """One recorded decision.

    Attributes:
        id: The record.
        scope: ``tenant`` or ``global``.
        subject_kind: What was reviewed.
        subject_ref: Its ref.
        subject_digest: Its digest.
        decision: What was decided.
        reviewer: For a tenant decision the reviewer's account id; for a
            global one the label the operator recorded.
        qualification_statement: As the owner supplied it; not verified.
        standards: As the reviewer named them.
        reason: Why.
        recorded_at: When.
        current: Whether it is the newest decision for its scope and digest.
    """

    id: str
    scope: Literal["tenant", "global"]
    subject_kind: SubjectKind
    subject_ref: str
    subject_digest: str
    decision: Decision
    reviewer: str
    qualification_statement: str
    standards: list[str]
    reason: str
    recorded_at: datetime
    current: bool


class DesignateRequest(BaseModel):
    """The owner designates an account as a qualified engineering reviewer.

    Attributes:
        member_id: An active account of the tenant.
        qualification_statement: The owner's statement of the engineer's
            qualification, recorded verbatim and never verified by the server.
    """

    model_config = ConfigDict(extra="forbid")

    member_id: str = Field(min_length=1, max_length=64)
    qualification_statement: str = Field(min_length=1, max_length=2000)


class ReviewerOut(BaseModel):
    """A current designation.

    Attributes:
        member_id: The account.
        full_name: Its name, if given.
        qualification_statement: As the owner supplied it.
        granted_by: The account that granted it.
        granted_at: When.
    """

    member_id: str
    full_name: str | None = None
    qualification_statement: str
    granted_by: str
    granted_at: datetime

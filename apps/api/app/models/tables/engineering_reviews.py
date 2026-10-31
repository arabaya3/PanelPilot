"""ORM models for engineering reviewer grants and review decisions (M3).

Three append-only tables. Nothing here is ever updated or deleted: the ORM
refuses it (``before_update`` / ``before_delete``) and so does the database
(trigger ``pp_refuse_append_only_change``, per row on ``UPDATE`` and
``DELETE`` and per statement on ``TRUNCATE``, installed by migration
``d9e4b7c2a5f1`` and, for tests that build the schema from the metadata, on
``create_all``). A newer row supersedes an older one; both stay.

- ``engineering_reviewer_grants``: the tenant owner grants or revokes the
  ``review_engineering_definitions`` permission for one of the tenant's
  accounts, recording the qualification statement as the owner supplied it.
- ``engineering_review_decisions``: a designated reviewer's decision on a
  template, pattern or rule, bound to its exact digest, in their tenant.
- ``global_engineering_review_decisions``: the same for the global scope,
  recorded by an operator (worker ``record-global-review``), with no tenant.

``seq`` orders rows for supersession: the highest wins.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    DDL,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Identity,
    Index,
    String,
    Text,
    Uuid,
    event,
    func,
)
from sqlalchemy import (
    false as sa_false,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, Mapper, mapped_column

from app.models.tables.base import Base, UUIDPrimaryKey
from app.models.tables.tenant import TenantScopedMixin

#: The trigger function refusing UPDATE and DELETE (also in the migration).
REFUSE_FUNCTION = "pp_refuse_append_only_change"

REFUSE_FUNCTION_SQL = f"""
CREATE OR REPLACE FUNCTION {REFUSE_FUNCTION}() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION '% is append-only', TG_TABLE_NAME USING ERRCODE = 'restrict_violation';
END;
$$ LANGUAGE plpgsql
"""

_KINDS = "subject_kind IN ('template', 'pattern', 'rule')"
_DECISIONS = "decision IN ('approved', 'rejected', 'withdrawn')"
_DIGEST = "subject_digest ~ '^[0-9a-f]{64}$'"


class AppendOnlyError(RuntimeError):
    """An attempt to change or delete an append-only row."""


class EngineeringReviewerGrantRow(TenantScopedMixin, UUIDPrimaryKey, Base):
    """The owner grants (or revokes) the review permission for one account."""

    __tablename__ = "engineering_reviewer_grants"
    __table_args__ = (
        ForeignKeyConstraint(
            ["user_id", "tenant_id"], ["users.id", "users.tenant_id"], ondelete="RESTRICT"
        ),
        ForeignKeyConstraint(
            ["granted_by_user_id", "tenant_id"],
            ["users.id", "users.tenant_id"],
            ondelete="RESTRICT",
        ),
        CheckConstraint("action IN ('grant', 'revoke')", name="action"),
        CheckConstraint(
            "(action = 'grant') = (qualification_statement IS NOT NULL)", name="statement"
        ),
        Index("ix_engineering_reviewer_grants_user_seq", "user_id", "seq"),
    )

    seq: Mapped[int] = mapped_column(BigInteger, Identity(), nullable=False, unique=True)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    action: Mapped[str] = mapped_column(String(10), nullable=False)
    qualification_statement: Mapped[str | None] = mapped_column(Text, nullable=True)
    granted_by_user_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class EngineeringReviewDecisionRow(TenantScopedMixin, UUIDPrimaryKey, Base):
    """A tenant reviewer's decision on one definition, bound to its digest."""

    __tablename__ = "engineering_review_decisions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["reviewer_user_id", "tenant_id"], ["users.id", "users.tenant_id"], ondelete="RESTRICT"
        ),
        CheckConstraint(_KINDS, name="subject_kind"),
        CheckConstraint(_DECISIONS, name="decision"),
        Index(
            "ix_engineering_review_decisions_subject",
            "tenant_id",
            "subject_kind",
            "subject_ref",
            "seq",
        ),
    )

    seq: Mapped[int] = mapped_column(BigInteger, Identity(), nullable=False, unique=True)
    subject_kind: Mapped[str] = mapped_column(String(10), nullable=False)
    subject_ref: Mapped[str] = mapped_column(String(160), nullable=False)
    subject_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    decision: Mapped[str] = mapped_column(String(10), nullable=False)
    reviewer_user_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    qualification_statement: Mapped[str] = mapped_column(Text, nullable=False)
    standards: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    #: Whether the server checked that the reviewer is not the definition's
    #: author (``engineering_reviews.record_decision``); ``False`` where the
    #: author is not known to the server.
    independence_checked: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=sa_false()
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class GlobalEngineeringReviewDecisionRow(UUIDPrimaryKey, Base):
    """An operator-recorded decision in the global scope; no tenant."""

    __tablename__ = "global_engineering_review_decisions"
    __table_args__ = (
        CheckConstraint(_KINDS, name="subject_kind"),
        CheckConstraint(_DECISIONS, name="decision"),
        Index(
            "ix_global_engineering_review_decisions_subject",
            "subject_kind",
            "subject_ref",
            "seq",
        ),
    )

    seq: Mapped[int] = mapped_column(BigInteger, Identity(), nullable=False, unique=True)
    subject_kind: Mapped[str] = mapped_column(String(10), nullable=False)
    subject_ref: Mapped[str] = mapped_column(String(160), nullable=False)
    subject_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    decision: Mapped[str] = mapped_column(String(10), nullable=False)
    reviewer_label: Mapped[str] = mapped_column(String(200), nullable=False)
    qualification_statement: Mapped[str] = mapped_column(Text, nullable=False)
    standards: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    recorded_by: Mapped[str] = mapped_column(String(200), nullable=False)
    #: Always ``False``: the server knows no author of a global definition.
    independence_checked: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=sa_false()
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


APPEND_ONLY = (
    EngineeringReviewerGrantRow,
    EngineeringReviewDecisionRow,
    GlobalEngineeringReviewDecisionRow,
)


def _refuse(_mapper: Mapper[Any], _connection: Any, target: Any) -> None:
    raise AppendOnlyError(f"{type(target).__tablename__} is append-only")


event.listen(
    Base.metadata,
    "before_create",
    DDL(REFUSE_FUNCTION_SQL.replace("%", "%%")).execute_if(dialect="postgresql"),  # type: ignore[no-untyped-call]
)
for _model in APPEND_ONLY:
    event.listen(_model, "before_update", _refuse)
    event.listen(_model, "before_delete", _refuse)
    _table = _model.__tablename__
    if _model is not EngineeringReviewerGrantRow:
        # Postgres only (a regular expression); the migration adds it as well.
        event.listen(
            _model.__table__,
            "after_create",
            DDL(  # type: ignore[no-untyped-call]
                f"ALTER TABLE {_table} ADD CONSTRAINT ck_{_table}_subject_digest CHECK ({_DIGEST})"
            ).execute_if(dialect="postgresql"),
        )
    event.listen(
        _model.__table__,
        "after_create",
        DDL(  # type: ignore[no-untyped-call]
            f"CREATE TRIGGER {_table}_append_only BEFORE UPDATE OR DELETE ON {_table} "
            f"FOR EACH ROW EXECUTE FUNCTION {REFUSE_FUNCTION}()"
        ).execute_if(dialect="postgresql"),
    )
    event.listen(
        _model.__table__,
        "after_create",
        DDL(  # type: ignore[no-untyped-call]
            f"CREATE TRIGGER {_table}_no_truncate BEFORE TRUNCATE ON {_table} "
            f"FOR EACH STATEMENT EXECUTE FUNCTION {REFUSE_FUNCTION}()"
        ).execute_if(dialect="postgresql"),
    )

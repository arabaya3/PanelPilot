"""ORM model for the calculation audit trail.

Calculations are recorded because an engineer may have to defend a sizing
decision months later: inputs, outputs, and the cited sources are all kept.
"""

from __future__ import annotations

import uuid

from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.tables.base import Base, TimestampMixin, UUIDPrimaryKey


class CalculationRecordRow(UUIDPrimaryKey, TimestampMixin, Base):
    """One executed calculation with its inputs, outputs, and sources.

    Rows are never updated. A corrected calculation is a new row, so the record
    of what was decided at the time survives.
    """

    __tablename__ = "calculation_records"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    kind: Mapped[str] = mapped_column(String(50), nullable=False, index=True)

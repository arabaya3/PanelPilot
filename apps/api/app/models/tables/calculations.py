"""ORM model for the calculation audit trail.

Calculations are recorded because an engineer may have to defend a sizing
decision months later: inputs, outputs, and the cited sources are all kept.
"""

from __future__ import annotations

from app.models.tables.base import Base, TimestampMixin


class CalculationRecordRow(Base, TimestampMixin):
    """One executed calculation with its inputs, outputs, and sources."""

    __tablename__ = "calculation_records"

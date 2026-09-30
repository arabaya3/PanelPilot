"""The ORM tables.

Every table module is imported here, so importing any one of them registers
them all. A foreign key names its target table by string, and SQLAlchemy
resolves it only when the target's module has been imported: the worker
imported ``ingestion`` without ``escalation``, and every crawl failed at the
last step, queueing its passages, on ``verification_items.flagged_answer_id``.
"""

from app.models.tables import (  # noqa: F401
    base,
    calculations,
    diagnostics,
    escalation,
    ingestion,
    session,
    tenant,
    user,
)

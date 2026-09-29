"""ORM table models.

Every table module is imported here, so importing any one of them registers
them all on ``Base.metadata``.

Without this, which tables SQLAlchemy knows about depended on what the entry
point happened to import. The API imports every route and so every model; the
worker's ``crawl`` job did not import the escalation models, and the first
flush that touched ``verification_items`` failed resolving its foreign key to
``flagged_answers`` — a crawl that could never queue its chunks for review.
The test suite never saw it, because some earlier test always imported the
missing module first.
"""

from app.models.tables import (  # noqa: F401  (imported for registration)
    base,
    calculations,
    diagnostics,
    escalation,
    ingestion,
    session,
    tenant,
    user,
)

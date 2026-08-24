"""Staging-to-production promotion service.

This module is the **only** write path into the production index. Nothing in
``app.ingestion`` or ``app.domain.ingestion`` may write there, and no route may
bypass ``promote_document``. Rationale and consequences:
docs/adr/0001-staging-vs-production-index.md.

If you are adding a feature that needs content to become live, extend this
module — do not add a second path.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.schemas.auth import CurrentUser
from app.models.schemas.ingestion import PromotionRequest, PromotionResponse


def promote_document(
    *,
    session: Session,
    reviewer: CurrentUser,
    request: PromotionRequest,
) -> PromotionResponse:
    """Copy a verified staged document into the production index.

    Preconditions, all enforced here rather than by the caller:

    1. The reviewer holds the reviewer role and is not the ingester of record.
    2. The staged document has passed automated verification checks.
    3. The document carries a resolvable source citation.

    The staged document is left in place; promotion writes a new production
    revision and records an immutable audit entry naming the reviewer.

    Args:
        session: Open database session; the audit entry and index write commit
            together.
        reviewer: The human approving the promotion.
        request: Staged document identifier and review notes.

    Returns:
        The promotion outcome, including the production revision written.

    Raises:
        AuthorizationError: If the reviewer lacks the reviewer role.
        PromotionError: If any precondition above is unmet.
        NotFoundError: If the staged document does not exist.
    """
    raise NotImplementedError

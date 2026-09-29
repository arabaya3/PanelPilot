"""Document search service.

Decides *which* index a caller may search and how results are shaped; the query
mechanics live in ``app.ai.retrieval``.
"""

from __future__ import annotations

from app.core.errors import NotImplementedYetError
from app.models.schemas.auth import CurrentUser
from app.models.schemas.search import SearchRequest, SearchResponse


def search_documents(*, user: CurrentUser, request: SearchRequest) -> SearchResponse:
    """Run a hybrid search on behalf of a caller.

    Ordinary callers are restricted to the production index. Only reviewers may
    target staging, and never through the same code path that serves answers.

    Args:
        user: The authenticated caller.
        request: Query text, filters, and pagination.

    Returns:
        Ranked passages with their source documents.

    Raises:
        AuthorizationError: If a non-reviewer requests the staging index.
        NotImplementedYetError: Always, for now. Retrieval serves the
            diagnosis path; a standalone search endpoint over it has not been
            built, and saying so beats an anonymous 500.
    """
    del user, request  # Unused until search exists; the signature is the contract.
    raise NotImplementedYetError("document search is not available yet")

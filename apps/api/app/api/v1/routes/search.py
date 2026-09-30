"""Document search endpoints.

No OpenSearch client is constructed or queried here; that lives behind
``app.domain.search`` → ``app.ai.retrieval``.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.deps import CurrentUserDep, enforce_trial_rate_limit
from app.domain import search as search_domain
from app.models.schemas.search import SearchRequest, SearchResponse

router = APIRouter()


# Each search embeds its query with a paid provider, so it counts against the
# same per-source allowance as asking a question.
@router.post("", response_model=SearchResponse, dependencies=[Depends(enforce_trial_rate_limit)])
def search_documents(payload: SearchRequest, user: CurrentUserDep) -> SearchResponse:
    """Search the documentation corpus.

    Raises:
        AuthorizationError: 403 if a non-reviewer asks for staging.
        ValidationError: 422 on a blank query.
        ServiceUnavailableError: 503 if retrieval fails.
    """
    return search_domain.search_documents(user=user, request=payload)

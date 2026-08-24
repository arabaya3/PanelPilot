"""Document search endpoints.

No OpenSearch client is constructed or queried here; that lives behind
``app.domain.search`` → ``app.ai.retrieval``.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.deps import CurrentUserDep
from app.domain import search as search_domain
from app.models.schemas.search import SearchRequest, SearchResponse

router = APIRouter()


@router.post("", response_model=SearchResponse)
def search_documents(payload: SearchRequest, user: CurrentUserDep) -> SearchResponse:
    return search_domain.search_documents(user=user, request=payload)

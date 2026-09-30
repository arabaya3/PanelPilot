"""Document search service.

Decides *which* index a caller may search and how results are shaped; the query
mechanics live in ``app.ai.retrieval``.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

import structlog

from app.ai.retrieval import hybrid_search, relevance
from app.core.errors import (
    AuthorizationError,
    PanelPilotError,
    ServiceUnavailableError,
    ValidationError,
)
from app.models.schemas.auth import CurrentUser, Role
from app.models.schemas.evaluation import EvalEntry
from app.models.schemas.search import RetrievedPassage, SearchRequest, SearchResponse

logger = structlog.get_logger(__name__)


def search_documents(
    *,
    user: CurrentUser,
    request: SearchRequest,
    search: Callable[..., list[RetrievedPassage]] | None = None,
    search_staging: Callable[..., list[RetrievedPassage]] | None = None,
) -> SearchResponse:
    """Run a hybrid search on behalf of a caller.

    Ordinary callers search production, the corpus answers cite. Only
    reviewers may search staging, and only through ``search_staging`` -- the
    separately named function that answer generation never calls -- so a
    wrong argument here cannot put unverified content in front of an engineer.

    Args:
        user: The authenticated caller.
        request: Query text, filters, corpus and result count.
        search: The production search; ``hybrid_search.search`` by default,
            looked up at call time so a test can replace it there.
        search_staging: The staging search; ``hybrid_search.search_staging``
            by default, likewise.

    Returns:
        Ranked passages with their citations. ``total`` is how many were
        returned: retrieval ranks the best ``top_k`` and does not count the rest.

    Raises:
        AuthorizationError: If a non-reviewer asks for staging.
        ValidationError: If the query is blank.
        ServiceUnavailableError: If the index or the embedding provider fails.
    """
    query = request.query.strip()
    if not query:
        raise ValidationError("a search needs a query")
    if request.corpus == "staging" and not user.has_role(Role.REVIEWER):
        raise AuthorizationError(f"{user.email} does not hold the reviewer role")

    run = (
        (search_staging or hybrid_search.search_staging)
        if request.corpus == "staging"
        else (search or hybrid_search.search)
    )
    try:
        passages = run(query, filters=request.filters, top_k=request.top_k)
    except PanelPilotError:
        raise
    except Exception as exc:
        # The index and the embedding provider fail in ways this layer cannot
        # enumerate. The query itself was fine, so it is a 503 -- try again --
        # and never the text of the underlying error, which can name hosts.
        logger.exception("search.failed", corpus=request.corpus)
        raise ServiceUnavailableError("search is unavailable right now; try again") from exc
    return SearchResponse(passages=passages, total=len(passages))


def calibrate_relevance(entries: Sequence[EvalEntry]) -> relevance.Calibration:
    """Recommend an absolute relevance floor for the production corpus.

    Args:
        entries: An eval set with both answerable and out-of-scope questions.

    Returns:
        The calibration: a recommended ``RETRIEVAL_MIN_SIMILARITY``, or why
        there is none. Read only; an operator applies it.

    Measured against production, the corpus the floor will guard, and with no
    floor in place: a configured one would drop the very passages whose
    similarity decides where it should sit.
    """
    unfloored = hybrid_search.retrieval_config_from_settings().model_copy(
        update={"min_similarity": None}
    )
    return relevance.calibrate_from_eval_set(
        entries,
        lambda entry: hybrid_search.search(entry.query, entry.brand, entry.model, config=unfloored),
    )

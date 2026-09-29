"""Document search service.

Decides *which* index a caller may search and how results are shaped; the query
mechanics live in ``app.ai.retrieval``.
"""

from __future__ import annotations

from collections.abc import Sequence

from app.ai.retrieval import hybrid_search, relevance
from app.core.errors import NotImplementedYetError
from app.models.schemas.auth import CurrentUser
from app.models.schemas.evaluation import EvalEntry
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

"""Dense embeddings for the vector leg of hybrid retrieval.

One provider today (Voyage), behind a callable seam, because the choice is a
vendor decision that outlives any one of them: Anthropic publishes no
embeddings API, so the key already configured for generation cannot serve
this, and whichever vendor is chosen brings its own key, cost line and data
agreement.

Two things this module refuses to do, both for the same reason — a wrong
vector is not a visible failure, it is a silently worse answer:

* **It will not return a vector of the wrong width.** ``EMBEDDING_DIMENSIONS``
  is baked into the index mapping, so a provider or model that returns 1536
  where the index expects 1024 does not error at the boundary — OpenSearch
  rejects the query, or worse, a mismatched index accepts garbage. The width
  is checked on every call.
* **It will not silently substitute a zero vector** when the provider is
  unreachable. A zero vector is a legal input to a kNN query and returns
  arbitrary neighbours, so an outage would degrade into confidently wrong
  retrieval rather than a refusal. Failures raise.

The same function embeds documents at index time and queries at search time,
with ``input_type`` distinguishing them — Voyage, like most providers, encodes
the two asymmetrically, and using the query encoding for stored documents
quietly costs retrieval quality in a way no test would notice.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Literal

import structlog

from app.ai.retrieval.mappings import EMBEDDING_DIMENSIONS
from app.core.errors import PanelPilotError

logger = structlog.get_logger(__name__)

#: Whether a text is being stored or searched for.
#:
#: Providers encode these differently, and the asymmetry is the point: a stored
#: passage is encoded to be *found*, a query to *find*. Passing the wrong one
#: costs recall silently.
InputType = Literal["document", "query"]

#: Embeds a batch of texts. Injected so the provider is a composition-root
#: choice and tests need no network.
Embedder = Callable[[Sequence[str], InputType], list[list[float]]]


class EmbeddingError(PanelPilotError):
    """Raised when embedding fails or returns something unusable."""


def _voyage_embedder(api_key: str, model: str) -> Embedder:
    """Build an embedder backed by Voyage.

    Args:
        api_key: The Voyage API key.
        model: The Voyage model id.

    Returns:
        A callable embedding batches of text.

    Raises:
        ConfigurationError: If the `voyageai` package is not installed.

    Imported lazily so the package is only required when Voyage is actually the
    configured provider — a deployment that never embeds should not fail to
    start over a missing optional dependency.
    """
    from app.core.config import ConfigurationError

    try:
        import voyageai
    except ImportError as exc:  # pragma: no cover - import guard
        raise ConfigurationError(
            "EMBEDDING_PROVIDER is 'voyage' but the voyageai package is not "
            "installed; add it to the api dependencies"
        ) from exc

    client = voyageai.Client(api_key=api_key)

    def embed(texts: Sequence[str], input_type: InputType) -> list[list[float]]:
        """Embed a batch through Voyage.

        Args:
            texts: The texts to embed.
            input_type: Whether these are documents or a query.

        Returns:
            One vector per input text, in order.
        """
        result = client.embed(list(texts), model=model, input_type=input_type)
        return [list(vector) for vector in result.embeddings]

    return embed


def get_embedder() -> Embedder:
    """Return the configured embedder.

    Returns:
        A callable embedding batches of text.

    Raises:
        ConfigurationError: If no provider is configured, or the configured
            one is unknown or missing its key.

    Refuses rather than falling back. A default provider would mean a
    deployment that forgot to configure one still returns vectors — from a
    model the index was not built against, which is the silent-wrong-answer
    case this module exists to prevent.
    """
    from app.core.config import ConfigurationError, get_settings

    settings = get_settings()
    provider = settings.embedding_provider

    if provider is None or provider == "":
        raise ConfigurationError(
            "EMBEDDING_PROVIDER is not set; retrieval cannot embed a query. "
            "See the embedding section of .env.example."
        )

    if provider != "voyage":
        raise ConfigurationError(
            f"EMBEDDING_PROVIDER {provider!r} is not supported; "
            "the only implemented provider is 'voyage'"
        )

    key = settings.embedding_api_key
    if key is None:
        raise ConfigurationError("EMBEDDING_PROVIDER is 'voyage' but EMBEDDING_API_KEY is not set")

    return _voyage_embedder(key.get_secret_value(), settings.embedding_model)


def embed_texts(
    texts: Sequence[str],
    *,
    input_type: InputType,
    embedder: Embedder | None = None,
) -> list[list[float]]:
    """Embed a batch of texts, checking every vector's width.

    Args:
        texts: The texts to embed.
        input_type: Whether these are documents being stored or a query.
        embedder: Injected for tests; the configured provider by default.

    Returns:
        One vector per input text, in the same order.

    Raises:
        EmbeddingError: If the provider fails, returns the wrong number of
            vectors, or returns a vector of the wrong width.

    The width check is not defensive padding. ``EMBEDDING_DIMENSIONS`` is
    written into the index mapping, and changing it invalidates every stored
    vector — so a model swap that silently changes width would either be
    rejected by OpenSearch at query time or, on a freshly built index, accepted
    and quietly wrong. Catching it here names the cause.
    """
    if not texts:
        return []

    resolved = embedder if embedder is not None else get_embedder()

    try:
        vectors = resolved(texts, input_type)
    except Exception as exc:
        # Broad on purpose: a provider client can raise anything, and every
        # one of them means "no usable vector". Returning zeros instead would
        # be a legal kNN input returning arbitrary neighbours — an outage
        # degrading into confidently wrong retrieval.
        logger.exception("embedding.failed", input_type=input_type, count=len(texts))
        raise EmbeddingError(f"embedding failed: {exc.__class__.__name__}") from exc

    if len(vectors) != len(texts):
        raise EmbeddingError(f"embedder returned {len(vectors)} vectors for {len(texts)} texts")

    for index, vector in enumerate(vectors):
        if len(vector) != EMBEDDING_DIMENSIONS:
            raise EmbeddingError(
                f"vector {index} has {len(vector)} dimensions, but the index "
                f"mapping expects {EMBEDDING_DIMENSIONS}; a model change needs "
                "a re-index, not a config edit"
            )

    return vectors


def embed_query(query: str, *, embedder: Embedder | None = None) -> list[float]:
    """Embed one query string for the vector leg of hybrid search.

    Args:
        query: Natural-language query text.
        embedder: Injected for tests.

    Returns:
        The dense embedding vector.

    Raises:
        EmbeddingError: If the query is empty, or embedding fails.
    """
    if not query.strip():
        # An empty query cannot be embedded meaningfully, and a zero vector
        # would return arbitrary neighbours rather than nothing.
        raise EmbeddingError("cannot embed an empty query")

    return embed_texts([query], input_type="query", embedder=embedder)[0]


def embed_documents(texts: Sequence[str], *, embedder: Embedder | None = None) -> list[list[float]]:
    """Embed passages for storage in the index.

    Args:
        texts: The passage texts.
        embedder: Injected for tests.

    Returns:
        One vector per passage.

    Raises:
        EmbeddingError: If embedding fails or returns unusable vectors.

    Separate from ``embed_query`` only to fix ``input_type`` at the call site.
    Ingestion writing query-encoded vectors would cost retrieval quality
    invisibly — every search would still return *something*, just worse.
    """
    return embed_texts(texts, input_type="document", embedder=embedder)

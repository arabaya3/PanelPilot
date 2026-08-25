"""OpenSearch index mapping for the document corpus.

This mapping is the backbone of every citation the product shows. A field that
is absent or wrongly typed here means cite-or-refuse has nothing precise to
cite, so the shape is defined once, in code, and applied identically to both
indices.

Staging and production share this mapping exactly. They differ only in name and
in who may write to them — see docs/adr/0001-staging-vs-production-index.md.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

# Dimensionality of the embedding model backing `content_vector`. Changing this
# invalidates every indexed vector, so it is a re-index, never an edit in place.
EMBEDDING_DIMENSIONS = 1024


class DocType(StrEnum):
    """Kind of source document a chunk came from."""

    MANUAL = "manual"
    DATASHEET = "datasheet"
    GUIDE = "guide"


class VerificationStatus(StrEnum):
    """Whether a human has verified this chunk.

    Production content is always ``VERIFIED``; staging holds ``UNVERIFIED``
    until a reviewer promotes it.
    """

    UNVERIFIED = "unverified"
    VERIFIED = "verified"


# Every field a chunk must carry. Kept as an explicit tuple so the "none left
# null on ingest" acceptance criterion is checkable rather than aspirational.
REQUIRED_FIELDS: tuple[str, ...] = (
    "brand",
    "model",
    "doc_type",
    "page",
    "section",
    "source_url",
    "verification_status",
    "content",
    "content_vector",
    # Identity of the source text, not the text itself. Promotion compares this
    # to decide whether live content has changed underneath an existing
    # citation, so a chunk without it cannot be safely re-promoted. See BE-004.
    "content_hash",
)


def index_mapping(*, embedding_dimensions: int = EMBEDDING_DIMENSIONS) -> dict[str, Any]:
    """Return the full index body: settings plus field mappings.

    Args:
        embedding_dimensions: Width of the dense vector, matching the embedding
            model in use.

    Returns:
        A body suitable for ``client.indices.create(index=..., body=...)``.
    """
    return {
        "settings": {
            # kNN is an index-level toggle; it cannot be enabled after the fact.
            "index": {"knn": True},
        },
        "mappings": {
            # Reject unknown fields rather than silently inferring a type for
            # them. A typo'd field name should fail the write, not create a new
            # column that nothing queries.
            "dynamic": "strict",
            "properties": {
                "brand": {"type": "keyword"},
                "model": {"type": "keyword"},
                "doc_type": {"type": "keyword"},
                "page": {"type": "integer"},
                # Free text: a section heading is matched, not filtered on.
                "section": {"type": "text"},
                "source_url": {"type": "keyword"},
                "verification_status": {"type": "keyword"},
                "content": {"type": "text", "analyzer": "standard"},
                # Change detection for re-crawls; never analysed, only compared.
                "content_hash": {"type": "keyword"},
                # Who staged this chunk. Promotion refuses to let the same
                # person clear their own content (four-eyes, ADR 0001).
                # Optional: content predating the reviewer workflow has none.
                "ingested_by": {"type": "keyword"},
                "content_vector": {
                    "type": "knn_vector",
                    "dimension": embedding_dimensions,
                    # Cosine matches how the embeddings are normalised; using
                    # l2 here would rank by magnitude as well as direction.
                    "space_type": "cosinesimil",
                    "method": {
                        "name": "hnsw",
                        "engine": "lucene",
                        "parameters": {"ef_construction": 128, "m": 16},
                    },
                },
            },
        },
    }


def missing_required_fields(document: dict[str, Any]) -> list[str]:
    """Return the required fields a document leaves absent or null.

    Used on ingest so a chunk cannot reach an index with a null citation
    field, which would surface later as an answer that cannot be traced.

    Args:
        document: The chunk about to be indexed.

    Returns:
        The offending field names, in mapping order. Empty when complete.
    """
    return [f for f in REQUIRED_FIELDS if document.get(f) is None]

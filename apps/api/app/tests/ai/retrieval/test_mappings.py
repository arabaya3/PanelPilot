"""Tests for `app/ai/retrieval/mappings.py`.

Mirrors the module 1:1 — if you add a function there, add its test here.

This mapping is what every citation resolves against, so the assertions here
are about the half of BE-003's acceptance criterion that says no schema field
may be left null on ingest.
"""

from __future__ import annotations

from typing import Any

from app.ai.retrieval.mappings import (
    EMBEDDING_DIMENSIONS,
    REQUIRED_FIELDS,
    DocType,
    VerificationStatus,
    index_mapping,
    missing_required_fields,
)


def test_mapping_declares_every_required_field() -> None:
    props = index_mapping()["mappings"]["properties"]
    assert set(REQUIRED_FIELDS) <= set(props)


def test_field_types_match_how_they_are_queried() -> None:
    """Filterable fields are keywords; searchable prose is text.

    Getting this backwards is silent: a `text` brand still "works" but matches
    fuzzily, so a filter for ABB would also return ABB Ltd.
    """
    props = index_mapping()["mappings"]["properties"]
    for field in ("brand", "model", "doc_type", "source_url", "verification_status"):
        assert props[field]["type"] == "keyword", field
    assert props["content"]["type"] == "text"
    assert props["section"]["type"] == "text"
    assert props["page"]["type"] == "integer"


def test_vector_field_is_knn_indexed_at_the_declared_width() -> None:
    props = index_mapping()["mappings"]["properties"]
    vector = props["content_vector"]
    assert vector["type"] == "knn_vector"
    assert vector["dimension"] == EMBEDDING_DIMENSIONS
    # Cosine, because the embeddings are normalised — l2 would rank by
    # magnitude as well as direction.
    assert vector["space_type"] == "cosinesimil"
    # kNN is an index-level setting that cannot be enabled after creation.
    assert index_mapping()["settings"]["index"]["knn"] is True


def test_unknown_fields_are_rejected_rather_than_inferred() -> None:
    """A typo'd field name must fail the write, not create a new field."""
    assert index_mapping()["mappings"]["dynamic"] == "strict"


def test_embedding_width_is_configurable_for_a_model_change() -> None:
    """Changing models is a re-index; the mapping must at least accept the width."""
    assert (
        index_mapping(embedding_dimensions=768)["mappings"]["properties"]["content_vector"][
            "dimension"
        ]
        == 768
    )


def test_missing_required_fields_names_every_gap() -> None:
    complete: dict[str, Any] = dict.fromkeys(REQUIRED_FIELDS, "x")
    assert missing_required_fields(complete) == []

    partial = dict(complete)
    del partial["page"]
    partial["source_url"] = None
    assert set(missing_required_fields(partial)) == {"page", "source_url"}


def test_empty_string_is_not_treated_as_missing() -> None:
    """Only null counts as absent.

    An empty section heading is legitimate; conflating it with a missing field
    would block valid content at ingest.
    """
    document: dict[str, Any] = dict.fromkeys(REQUIRED_FIELDS, "x")
    document["section"] = ""
    assert missing_required_fields(document) == []


def test_enum_values_match_the_documented_vocabulary() -> None:
    assert {d.value for d in DocType} == {"manual", "datasheet", "guide"}
    assert {v.value for v in VerificationStatus} == {"unverified", "verified"}

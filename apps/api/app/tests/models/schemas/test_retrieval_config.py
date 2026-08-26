"""Tests for `app/models/schemas/retrieval_config.py`.

Mirrors the module 1:1 — if you add a function there, add its test here.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.models.schemas.retrieval_config import (
    BlendWeights,
    QueryType,
    RetrievalConfig,
)

# --- weights must be a genuine blend ---------------------------------------


def test_weights_must_sum_to_one() -> None:
    """Otherwise the fused score is rescaled.

    That moves it relative to the guardrail threshold, which decides whether
    an answer is given at all — a tuning change would silently become a
    safety change.
    """
    with pytest.raises(ValidationError, match="must sum to 1"):
        BlendWeights(bm25=0.7, vector=0.7)


def test_weights_that_undershoot_are_refused() -> None:
    with pytest.raises(ValidationError, match="must sum to 1"):
        BlendWeights(bm25=0.3, vector=0.3)


@pytest.mark.parametrize(
    ("bm25", "vector"),
    [(0.0, 1.0), (1.0, 0.0)],
)
def test_a_zero_weight_is_refused(bm25: float, vector: float) -> None:
    """A zero weight deletes a leg rather than tuning it.

    It turns hybrid retrieval into single-leg retrieval while the name, the
    pipeline, and the tests all still say hybrid — which is exactly how a
    previous review found the fusion was not actually fusing.
    """
    with pytest.raises(ValidationError, match="deletes a retrieval leg"):
        BlendWeights(bm25=bm25, vector=vector)


def test_a_small_weight_is_allowed() -> None:
    """The documented alternative to zero must actually work."""
    assert BlendWeights(bm25=0.99, vector=0.01).vector == 0.01


def test_floating_point_sums_are_tolerated() -> None:
    """0.7 + 0.3 is not exactly 1.0 in binary floating point."""
    assert BlendWeights(bm25=0.7, vector=0.3).bm25 == 0.7


def test_pipeline_weights_are_ordered_bm25_first() -> None:
    """The order must match the `queries` list in the query builder.

    If they disagree, each weight lands on the wrong leg — which looks like a
    tuning problem and is not one.
    """
    assert BlendWeights(bm25=0.85, vector=0.15).as_pipeline_weights() == [0.85, 0.15]


# --- the config ------------------------------------------------------------


def test_every_query_type_has_default_weights() -> None:
    """A type with no weights is one the classifier routes to and nothing tunes."""
    config = RetrievalConfig()
    for query_type in QueryType:
        assert config.weights_for(query_type)


def test_a_missing_query_type_is_refused() -> None:
    """Refuse a config that omits a query type.

    Falling back to a default would let a type stay untuned while the config
    looks complete.
    """
    with pytest.raises(ValidationError, match="no blend weights configured"):
        RetrievalConfig(weights={QueryType.FAULT_CODE: BlendWeights(bm25=0.8, vector=0.2)})


def test_the_defaults_differ_by_query_type() -> None:
    """Identical defaults would make the conditioning decorative.

    The whole premise of AI-002 is that one global weight underserves one
    query type, so the starting points must actually differ.
    """
    config = RetrievalConfig()
    bm25_values = {config.weights_for(t).bm25 for t in QueryType}
    assert len(bm25_values) == len(QueryType)


def test_fault_codes_start_lexical_and_symptoms_start_semantic() -> None:
    """The direction matters more than the exact value."""
    config = RetrievalConfig()
    code = config.weights_for(QueryType.FAULT_CODE)
    symptom = config.weights_for(QueryType.SYMPTOM_DESCRIPTION)
    assert code.bm25 > code.vector
    assert symptom.vector > symptom.bm25


def test_top_k_is_bounded() -> None:
    with pytest.raises(ValidationError):
        RetrievalConfig(top_k=0)
    with pytest.raises(ValidationError):
        RetrievalConfig(top_k=101)


def test_min_score_stays_in_the_unit_interval() -> None:
    """Keep the floor inside the normalised range.

    The fused score is min-max normalised, so anything outside [0, 1] either
    never fires or always does.
    """
    with pytest.raises(ValidationError):
        RetrievalConfig(min_score=1.5)
    with pytest.raises(ValidationError):
        RetrievalConfig(min_score=-0.1)


def test_the_default_weights_are_not_shared_between_configs() -> None:
    """A shared mutable default would let one config's re-tune alter another.

    Asserted against a fresh config built *after* the mutation: the shared
    object is the module-level dict, and that is what must stay untouched.

    Note that pydantic deep-copies a mutable default on construction anyway,
    so `default_factory` is defence-in-depth here rather than the thing making
    this pass — a mutation test cannot tell the two forms apart.
    """
    first = RetrievalConfig()
    first.weights[QueryType.FAULT_CODE] = BlendWeights(bm25=0.5, vector=0.5)
    later = RetrievalConfig()
    assert later.weights_for(QueryType.FAULT_CODE).bm25 != 0.5


def test_the_precision_floor_is_a_real_bar() -> None:
    """A floor of 0 accepts anything, which is the criterion not being met.

    The value is a product decision awaiting agreement, but a default that
    gates nothing would let the acceptance criterion pass vacuously.
    """
    assert RetrievalConfig().min_precision > 0.5


def test_the_precision_floor_is_configurable() -> None:
    """It is the number under discussion; only the enforcement is fixed."""
    assert RetrievalConfig(min_precision=0.9).min_precision == 0.9


def test_the_precision_floor_stays_in_the_unit_interval() -> None:
    with pytest.raises(ValidationError):
        RetrievalConfig(min_precision=1.5)

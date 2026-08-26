"""Retrieval tuning parameters, in one object.

Every number that changes retrieval behaviour lives here. The point is that
re-tuning is a config change, not a code change: constants scattered across
the query builder mean each new parameter needs an edit in a different file,
and nobody can see the current tuning without reading all of them.

**The blend weight is conditional on query type, not global.** A fault-code
lookup ("F0001", "E-024") is a near-exact string match where BM25 is right and
semantic similarity is noise — every fault code in a manual is semantically
adjacent to every other. A symptom description ("the drive trips when the
conveyor starts under load") is the reverse: the engineer's words will not
appear in the manual, and only the vector leg finds it. One global weight
serves whichever type is more common and quietly underserves the other.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field, model_validator


class QueryType(StrEnum):
    """What kind of question is being asked.

    Determines the blend weight. Kept to three because each is a genuinely
    different retrieval problem, not because three is a tidy number — adding a
    fourth means committing to tune and report it separately.
    """

    # "F0001", "E-024", "fault 2340". Near-exact lexical match.
    FAULT_CODE = "fault_code"
    # "par 21.03", "parameter 99.04", "set 20.01 to". Also lexical, but the
    # engineer usually knows the number and wants the surrounding table.
    PARAMETER_LOOKUP = "parameter_lookup"
    # "trips when the conveyor starts under load". Semantic.
    SYMPTOM_DESCRIPTION = "symptom_description"


class BlendWeights(BaseModel):
    """How much each retrieval leg contributes for one query type.

    Attributes:
        bm25: Weight on the lexical leg.
        vector: Weight on the semantic leg.
    """

    bm25: float = Field(ge=0.0, le=1.0)
    vector: float = Field(ge=0.0, le=1.0)

    @model_validator(mode="after")
    def _weights_sum_to_one(self) -> BlendWeights:
        """Require the pair to be a genuine blend.

        Returns:
            The validated weights.

        Raises:
            ValueError: If they do not sum to 1, or if either leg is zero.
                Weights that do not sum to 1 rescale the fused score, which
                silently moves it relative to the guardrail threshold — the
                thing that decides whether an answer is given at all. A zero
                weight is not a tuning value but a deletion of one leg: it
                turns "hybrid" retrieval into single-leg retrieval while the
                name and the tests still say hybrid.
        """
        total = self.bm25 + self.vector
        if abs(total - 1.0) > 1e-9:
            raise ValueError(
                f"blend weights must sum to 1, got {self.bm25} + {self.vector} = {total}"
            )
        if self.bm25 == 0.0 or self.vector == 0.0:
            raise ValueError(
                "a zero weight deletes a retrieval leg rather than tuning it; "
                "use a small weight if a leg should contribute little"
            )
        return self

    def as_pipeline_weights(self) -> list[float]:
        """Render for the OpenSearch normalization processor.

        Returns:
            ``[bm25, vector]`` — the order the ``queries`` list uses. The two
            must agree or the blend applies each weight to the wrong leg,
            which looks like a tuning problem and is not.
        """
        return [self.bm25, self.vector]


# Starting points, not conclusions. These are the values tuning begins from;
# the tuning report records what the eval set actually supports.
_DEFAULT_WEIGHTS: dict[QueryType, BlendWeights] = {
    # Lexical-dominant: the code is a literal token in the manual.
    QueryType.FAULT_CODE: BlendWeights(bm25=0.85, vector=0.15),
    # Also lexical, but slightly less so — engineers paraphrase parameter
    # names ("accel ramp") as often as they cite numbers.
    QueryType.PARAMETER_LOOKUP: BlendWeights(bm25=0.7, vector=0.3),
    # Semantic-dominant: the engineer's words are not the manual's words.
    QueryType.SYMPTOM_DESCRIPTION: BlendWeights(bm25=0.3, vector=0.7),
}


class RetrievalConfig(BaseModel):
    """Everything tunable about retrieval.

    Attributes:
        weights: Blend weights per query type.
        top_k: Passages retrieved per query.
        min_score: Fused-score floor. Passages below it are dropped before the
            guardrail ever sees them.
    """

    weights: dict[QueryType, BlendWeights] = Field(default_factory=lambda: dict(_DEFAULT_WEIGHTS))
    top_k: int = Field(default=12, ge=1, le=100)
    min_score: float = Field(default=0.05, ge=0.0, le=1.0)
    # The bar retrieval must clear, per category, before a change ships.
    #
    # This is deliberately a config field and not a constant: it is a product
    # decision about how much wrong-passage risk is acceptable, and the number
    # below is a *starting proposal* awaiting agreement, not a measured result.
    # `assert_meets_threshold` enforces whatever it is set to, so the argument
    # is about the value rather than about whether anything checks it.
    #
    # It applies per category, never to an average: an aggregate that clears
    # the bar while fault-code lookups sit at 0.2 is the exact failure the
    # per-category reporting exists to surface.
    min_precision: float = Field(default=0.7, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def _every_query_type_is_tuned(self) -> RetrievalConfig:
        """Require a weight for every query type.

        Returns:
            The validated config.

        Raises:
            ValueError: If a type has no weights. Falling back to a default
                for a missing type is how a type ends up untuned while the
                config looks complete — the classifier would route to it and
                nothing would report that it was never tuned.
        """
        missing = sorted(t.value for t in QueryType if t not in self.weights)
        if missing:
            raise ValueError(f"no blend weights configured for query type(s): {missing}")
        return self

    def weights_for(self, query_type: QueryType) -> BlendWeights:
        """Return the blend weights for a query type.

        Args:
            query_type: The classified type.

        Returns:
            Its weights.
        """
        return self.weights[query_type]

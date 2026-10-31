"""Tests for ``app/design/model/anchoring.py``: review status counts only with a record.

The decisions here are synthetic stand-ins for server records (ids and dates
made up, labelled synthetic); no engineering review is implied.
"""

from __future__ import annotations

import dataclasses

import pytest

from app.design.model import anchoring, entities, findings_v2
from app.design.model.read import read_snapshot
from app.domain import design_configuration
from app.tests.design.model.v2_support import config_case


def _model() -> entities.DesignModelV2:
    v1, configuration = config_case("pe_n_distribution")
    model = read_snapshot(design_configuration.compose_snapshot(v1, configuration)).v2
    assert model is not None
    return model


def _decided(
    decision: str, scope: str = "tenant", record: str = "synthetic-1"
) -> anchoring.Decided:
    return anchoring.Decided(
        decision=decision,
        scope=scope,
        record_id=record,
        decided_at="2026-10-31T00:00:00+00:00",
        standards=("synthetic-standard-ref",),
    )


def _all(
    model: entities.DesignModelV2, decision: str, scope: str = "tenant"
) -> anchoring.ReviewAnchor:
    keys = [("template", t.ref, anchoring.definition_digest(t)) for t in model.templates] + [
        ("pattern", p.ref, anchoring.definition_digest(p)) for p in model.patterns
    ]
    found = {key: _decided(decision, scope) for key in keys}
    return (
        anchoring.ReviewAnchor(tenant=found)
        if scope == "tenant"
        else anchoring.ReviewAnchor(global_=found)
    )


def _codes(model: entities.DesignModelV2) -> set[str]:
    return {f.code for f in findings_v2.structural_findings(model)}


def test_the_digest_ignores_the_review_and_covers_the_content() -> None:
    template = _model().templates[0]
    claimed = dataclasses.replace(
        template,
        review=dataclasses.replace(
            anchoring.UNREVIEWED, status="approved", reviewer="x", reviewed_at="y"
        ),
    )
    assert anchoring.definition_digest(claimed) == anchoring.definition_digest(template)
    edited = dataclasses.replace(template, function=template.function + "-edited")
    assert anchoring.definition_digest(edited) != anchoring.definition_digest(template)


def test_no_decision_is_m2s_rule() -> None:
    model = _model()
    assert anchoring.anchor_model(model, anchoring.EMPTY) is model
    assert {"template_unreviewed", "pattern_unreviewed"} <= _codes(model)


@pytest.mark.parametrize("scope", ["tenant", "global"])
def test_an_approval_on_record_counts(scope: str) -> None:
    model = _model()
    anchored = anchoring.anchor_model(model, _all(model, "approved", scope))
    assert all(t.review.status == "approved" for t in anchored.templates)
    assert all(p.review.status == "approved" for p in anchored.patterns)
    assert anchored.templates[0].review.reviewer == f"server-record:{scope}:synthetic-1"
    assert not {"template_unreviewed", "pattern_unreviewed"} & _codes(anchored)
    # Locks stay as stored: the anchored model is never written back.
    assert anchored.registry_lock == model.registry_lock
    assert anchored.pattern_lock == model.pattern_lock


def test_a_rejection_anywhere_wins_and_a_withdrawal_is_unreviewed() -> None:
    model = _model()
    t = model.templates[0]
    key = ("template", t.ref, anchoring.definition_digest(t))
    both = anchoring.ReviewAnchor(
        tenant={key: _decided("approved")}, global_={key: _decided("rejected", "global")}
    )
    assert both.status(*key) == "rejected"
    loosen = anchoring.ReviewAnchor(
        tenant={key: _decided("rejected")}, global_={key: _decided("approved", "global")}
    )
    assert loosen.status(*key) == "rejected"
    withdrawn = anchoring.ReviewAnchor(tenant={key: _decided("withdrawn")})
    assert withdrawn.status(*key) == "unreviewed"
    assert withdrawn.review(*key) == anchoring.UNREVIEWED
    rejected = anchoring.anchor_model(
        model, anchoring.ReviewAnchor(tenant={key: _decided("rejected")})
    )
    assert "template_rejected" in _codes(rejected)


def test_a_decision_for_another_digest_or_ref_does_not_count() -> None:
    model = _model()
    t = model.templates[0]
    other = anchoring.ReviewAnchor(
        tenant={
            ("template", t.ref, "0" * 64): _decided("approved"),
            ("pattern", t.ref, anchoring.definition_digest(t)): _decided("approved"),
        }
    )
    assert anchoring.anchor_model(model, other).templates[0].review == anchoring.UNREVIEWED


def test_a_stored_claim_without_a_matching_record_is_refused() -> None:
    model = _model()
    t = model.templates[0]
    claim = entities.Review(
        status="approved",
        reviewer="Synthetic forged reviewer",
        reviewed_at="2026-01-01",
        standards=(),
        scope_note=None,
    )
    forged = dataclasses.replace(
        model, templates=(dataclasses.replace(t, review=claim), *model.templates[1:])
    )
    with pytest.raises(anchoring.ReviewClaimUnmatchedError):
        anchoring.anchor_model(forged, anchoring.EMPTY)
    # A record exists, but the claim is not what it says.
    with pytest.raises(anchoring.ReviewClaimUnmatchedError):
        anchoring.anchor_model(forged, _all(model, "approved"))
    # The claim is exactly the anchored review: it counts.
    anchor = _all(model, "approved")
    exact = anchor.review("template", t.ref, anchoring.definition_digest(t))
    honest = dataclasses.replace(
        model, templates=(dataclasses.replace(t, review=exact), *model.templates[1:])
    )
    assert anchoring.anchor_model(honest, anchor).templates[0].review == exact


def test_a_changed_definition_falls_back_to_unreviewed() -> None:
    model = _model()
    anchor = _all(model, "approved")
    t = model.templates[0]
    edited = dataclasses.replace(t, function=t.function + "-edited")
    changed = dataclasses.replace(model, templates=(edited, *model.templates[1:]))
    assert anchoring.anchor_model(changed, anchor).templates[0].review == anchoring.UNREVIEWED
